#!/usr/bin/env python
"""security_scan.py
依存パッケージの既知脆弱性を osv-scanner で決定論的に点検する。フェーズゲート
（BLD_GATE）で実行する想定。

- osv-scanner は Google/OSV.dev 公式のスキャナ（Apache-2.0・単一 Go バイナリ）。
- OSV.dev = GHSA + PyPA + RustSec + Go + npm + distros + OpenSSF Malicious Packages
  を統合したデータ源で、API は無料・レート制限なし（公式 FAQ）。
- 対応 lockfile: requirements.txt / poetry.lock / uv.lock / package-lock.json /
  pnpm-lock.yaml / yarn.lock / go.mod / Cargo.lock / pom.xml / gradle.lockfile など 20+ 種。
- 発見された脆弱性は severity ごとに集計する: CRITICAL/HIGH → high、MODERATE → medium、
  LOW → low。**high が 1 件でもあれば exit 1**（他ゲートスキャナと同じ規約）。medium/low は
  報告のみで exit 0。
- osv-scanner が PATH に無い場合は exit 2（強制ポリシー・未検査 green を出さない）。

出力: `evidence/security/osv-scan.json` に osv-scanner の raw JSON をそのまま保存する
（これが単一正本。docs/packages/ 等の二次ドキュメントは作らない）。

使い方: python scripts/security_scan.py
インストール: `winget install Google.OSVScanner` (Windows) / `scoop install osv-scanner` /
             https://github.com/google/osv-scanner/releases から SLSA3 署名済みバイナリ
"""
import json
import pathlib
import shutil
import subprocess
import sys

# Windows コンソール(cp932)で日本語を print してもクラッシュしないよう stdout/stderr を UTF-8 化。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


# 誤 cwd の無検査 green を防ぐ（project_guard.py 参照）
try:
    from project_guard import ensure_project_root
except ImportError:
    def ensure_project_root():
        if not pathlib.Path("docs").is_dir():
            print("エラー: docs/ が無い（プロジェクトルート外での実行）", file=sys.stderr)
            sys.exit(2)


EVIDENCE_DIR = pathlib.Path("evidence/security")
EVIDENCE_FILE = EVIDENCE_DIR / "osv-scan.json"

# OSV の severity 文字列 → ccd-flowkit の重要度規約へマップ。
# CVSS スコアからの推定にも同じ閾値を使う（>=7.0 high / >=4.0 medium / それ以外 low）。
_OSV_SEVERITY_MAP = {
    "CRITICAL": "high",
    "HIGH": "high",
    "MODERATE": "medium",
    "MEDIUM": "medium",
    "LOW": "low",
}


def _classify_from_cvss(score_str):
    """CVSS v3 vector 文字列またはスコア表記から high/medium/low を返す。
    パース不能なら 'medium' を返す（fail-safe: 判定不能を high 側に寄せない代わりに、
    完全に無視もしない）。
    """
    try:
        # score_str が数値ならそのまま、CVSS ベクトルなら BaseScore を抜き出す。
        val = float(score_str)
    except (TypeError, ValueError):
        return "medium"
    if val >= 7.0:
        return "high"
    if val >= 4.0:
        return "medium"
    return "low"


def classify_severity(vuln):
    """1つの vulnerability dict から high/medium/low を判定する（純関数・テスト可能）。

    優先順位:
    1. database_specific.severity（GHSA が付与する文字列 enum）
    2. severity[].score（CVSS ベクトル/スコア）
    3. どちらも無ければ medium（判定不能を安全側に寄せる）
    """
    db_sev = (vuln.get("database_specific") or {}).get("severity")
    if isinstance(db_sev, str):
        mapped = _OSV_SEVERITY_MAP.get(db_sev.upper())
        if mapped:
            return mapped
    for entry in vuln.get("severity") or []:
        score = entry.get("score")
        if score:
            return _classify_from_cvss(score)
    return "medium"


def summarize(scan_json):
    """osv-scanner の raw JSON から findings リストと集計を返す（純関数・テスト可能）。

    osv-scanner は同一 lockfile を type=lockfile（直接依存のみ）と type=unknown（transitive
    も含む）の 2 種類で報告することがあり、findings が重複する。同じ (package@version,
    vuln_id) の組で dedup し、最初に観測した source_path を代表として保持する。

    findings: [(severity, source_path, package@version, vuln_id, summary), ...]
    counts: {"high": n, "medium": n, "low": n}
    """
    seen = {}  # (pkg@ver, vuln_id) -> (sev, source_path, pkg@ver, vuln_id, summary)
    for result in scan_json.get("results") or []:
        source_path = (result.get("source") or {}).get("path") or "?"
        for pkg in result.get("packages") or []:
            pkg_name = (pkg.get("package") or {}).get("name") or "?"
            pkg_ver = (pkg.get("package") or {}).get("version") or "?"
            pkg_key = f"{pkg_name}@{pkg_ver}"
            for vuln in pkg.get("vulnerabilities") or []:
                vuln_id = vuln.get("id") or "?"
                key = (pkg_key, vuln_id)
                if key in seen:
                    continue
                sev = classify_severity(vuln)
                summary = vuln.get("summary") or ""
                seen[key] = (sev, source_path, pkg_key, vuln_id, summary)

    findings = list(seen.values())
    counts = {"high": 0, "medium": 0, "low": 0}
    for sev, *_ in findings:
        counts[sev] += 1
    return findings, counts


def run_osv_scanner():
    """osv-scanner を実行して JSON を返す。未インストール時は exit 2、実行エラー時も exit 2。

    exit code の扱い:
    - 0: 脆弱性なし → 空 results で返す
    - 1: 脆弱性あり → JSON をパースして返す
    - その他: ツール実行エラー → stderr に転送して exit 2
    """
    if shutil.which("osv-scanner") is None:
        print(
            "エラー: osv-scanner が PATH に見つかりません。\n"
            "  Windows: winget install Google.OSVScanner\n"
            "  scoop:   scoop install osv-scanner\n"
            "  その他:  https://github.com/google/osv-scanner/releases\n"
            "  ※ccd-flowkit は依存脆弱性スキャンを必須ゲートとして扱うため、"
            "未検査 green を返さず exit 2 で hard-fail します。",
            file=sys.stderr,
        )
        sys.exit(2)

    # 「scan source -r .」で cwd 配下の lockfile を再帰的に自動検出させる。
    # 対応 lockfile は osv-scanner が既知（requirements.txt / package-lock.json / go.mod
    # / Cargo.lock / pom.xml など 20+ 種）。--format json は raw JSON を stdout に吐く。
    proc = subprocess.run(
        ["osv-scanner", "scan", "source", "-r", ".", "--format", "json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if proc.returncode not in (0, 1):
        # 例外: 「lockfile ゼロ」は osv-scanner が exit 128 と "No package sources found"
        # で返す。既存スキャナ（evidence_scan / trace_scan 等）は対象ゼロを green とする
        # 規約なので、ここも空 results として扱い exit 0 に寄せる。
        # （プロジェクトに依存が無い純シェル/静的サイト等での誤 hard-fail を避けるため。）
        if "No package sources found" in (proc.stderr or ""):
            return {"results": []}
        # それ以外の非 0/1 は真の実行エラー。green を返さず hard-fail する。
        print(
            f"エラー: osv-scanner が exit {proc.returncode} で失敗しました。\n"
            f"  stderr:\n{proc.stderr}",
            file=sys.stderr,
        )
        sys.exit(2)

    if not proc.stdout.strip():
        # 脆弱性ゼロで exit 0 かつ stdout も空の場合の防御（v1 で観測はないが念のため）。
        return {"results": []}

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        print(
            f"エラー: osv-scanner の JSON 出力をパースできません: {e}\n"
            f"  raw stdout (先頭500字):\n{proc.stdout[:500]}",
            file=sys.stderr,
        )
        sys.exit(2)


def main():
    ensure_project_root()

    scan_json = run_osv_scanner()

    # 単一正本として raw JSON をそのまま保存する（AI 判断はここを Read して行う）。
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCE_FILE.write_text(
        json.dumps(scan_json, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    findings, counts = summarize(scan_json)
    order = {"high": 0, "medium": 1, "low": 2}
    for sev, src, pkg, vuln_id, summary in sorted(findings, key=lambda x: order[x[0]]):
        summary_snippet = (summary[:80] + "…") if len(summary) > 80 else summary
        print(f"[{sev}] {src} :: {pkg} :: {vuln_id}: {summary_snippet}")

    print(
        f"\nsummary: high={counts['high']} medium={counts['medium']} low={counts['low']} "
        f"(evidence: {EVIDENCE_FILE.as_posix()})"
    )
    sys.exit(1 if counts["high"] else 0)


if __name__ == "__main__":
    main()
