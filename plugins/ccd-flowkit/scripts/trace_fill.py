#!/usr/bin/env python
"""traceability.json の design / code 列を設計HTML・ソースマーカーから機械転記する。

AI 手動充填では手順が consistency-checker.md 側に落ちておらず個体差で列が空のまま
完走する事故が生じたため、決定論スクリプトへ集約する。転記元は元々機械可読——設計書.html
の data-trace-req 属性と src/ の `# DES-NNN [REQ-...]` マーカー——なので、設計方針
P2=機械化の最大化に従う（design/code 列は属性・マーカーから導出されるビューであり、
正本は設計HTML/ソース側＝P1/P3）。

転記内容（冪等。実行のたびに導出値で置き換える）:
  - REQ.design ← 設計書.html の <section data-trace-id=DES data-trace-req="REQ ..."> を反転
  - DES.code   ← src/ のトレースマーカー位置（Python は直後の def/class 名で file:name、
                 それ以外は file:line）
  - REQ.code   ← マーカーの [REQ-...] 注記を経由して同じ参照を転記
  - DES ノードが台帳に無ければ作成（code と、data-trace-deps 由来の dependsOn を持つ最小形。
    hash / depsVerifiedAgainst 等の意味フィールドは consistency-checker が管理する）
既存の test / hash / verifiedAgainst / dependsOn（既存ノード）等は保持する。
REQ ノード自体は作成しない（REQ の登録・hash は consistency-checker の責務。
台帳に無い REQ への参照は skipped_reqs として報告する）。

使い方: python trace_fill.py   （consistency-checker が BLD_GATE/T_GATE で実行する）
出力: JSON {updated_reqs, created_des, updated_des, skipped_reqs}
"""
import glob
import json
import pathlib
import re
import sys

try:
    from project_guard import ensure_project_root
except ImportError:
    def ensure_project_root():
        if not pathlib.Path("docs").is_dir():
            print("エラー: docs/ が見つかりません（プロジェクトルート外での実行）。",
                  file=sys.stderr)
            sys.exit(2)

# 設計セクション・ソースマーカーの収穫は trace_scan の既存パーサを再利用する（二重管理しない）。
import trace_scan  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

LEDGER = pathlib.Path("docs/trace/traceability.json")

# マーカー行（REQ 注記付き）: `# DES-002 [REQ-011 REQ-012]`。trace_scan.SRC_MARKER_RE は
# DES-ID のみ捕捉するため、REQ 注記はここで拾う（書式の正本は trace-conventions.md）。
MARKER_WITH_REQS_RE = re.compile(
    r"^\s*(?:#|//|;|--)\s*(DES-\d{3})\b(?:\s*\[([^\]]*)\])?")
# Python の関数/クラス定義（マーカー直後の定義名で file:name 形式の参照を作る）
PY_DEF_RE = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")
REQ_ID_RE = re.compile(r"REQ-\d{3}")


def harvest_src_refs():
    """src/ のマーカーから {DES: [参照...]}, {REQ: [参照...]} を収穫する（純関数的・cwd相対）。

    参照は Python なら `file:関数名`（マーカー直後の def/class）、取れなければ `file:行番号`。
    走査対象・除外規則（symlink・非ソース拡張子・src 外 reparse）は trace_scan.parse_src と
    同一にするため、対象ファイル列挙は同じ条件で行う。
    """
    des_refs = {}
    req_refs = {}
    try:
        src_root = pathlib.Path("src").resolve(strict=False)
    except Exception:
        src_root = None
    for p in sorted(glob.glob("src/**/*", recursive=True)):
        path = pathlib.Path(p)
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix.lower() not in trace_scan.SOURCE_EXTS:
            continue
        if not trace_scan._resolved_within(path, src_root):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        rel = p.replace("\\", "/")
        for i, line in enumerate(lines):
            m = MARKER_WITH_REQS_RE.match(line)
            if not m:
                continue
            did, req_note = m.group(1), m.group(2) or ""
            ref = f"{rel}:{i + 1}"
            if path.suffix.lower() == ".py":
                # マーカー直後の定義名を探す。デコレータ・空行・コメントは読み飛ばし、
                # それ以外の文に当たったら打ち切って file:行番号 にフォールバックする
                for follow in lines[i + 1:i + 16]:
                    dm = PY_DEF_RE.match(follow)
                    if dm:
                        ref = f"{rel}:{dm.group(1)}"
                        break
                    s = follow.strip()
                    if s and not s.startswith(("@", "#")):
                        break
            des_refs.setdefault(did, []).append(ref)
            for req in REQ_ID_RE.findall(req_note):
                req_refs.setdefault(req, []).append(ref)
    return des_refs, req_refs


def harvest_design_links():
    """設計HTMLから {REQ: [DES...]}, {DES: [deps...]} を収穫する（trace_scan.parse_design 再利用）。"""
    req_design = {}
    des_deps = {}
    for tid, req_str, deps_str in trace_scan.parse_design():
        des_deps[tid] = trace_scan.parse_deps(deps_str)
        for req in REQ_ID_RE.findall(req_str or ""):
            req_design.setdefault(req, []).append(tid)
    return req_design, des_deps


def fill(led, req_design, des_deps, des_refs, req_refs):
    """台帳 dict に design/code 列を転記し、変更サマリを返す（純関数・テスト可能）。

    - 既存 REQ ノードの design / code は導出値で置き換える（列は導出ビュー＝P3。
      手書きの残置値を残すと陳腐化するため union にしない）
    - REQ ノードは作成しない（台帳に無い REQ は skipped_reqs で報告）
    - DES ノードの新規作成は**設計に実在する DES（des_deps のキー）に限る**。src マーカー
      だけにある DES（dangling マーカー）を台帳に作成すると、check_code の code_dangling high に
      加えて check_bidirectional の幽霊ID high を自ら作ってしまう（マーカーの誤りはマーカー側の
      検査に委ね、台帳を汚染しない）。既存 DES は code のみ更新（dependsOn/hash 等の
      意味フィールドは consistency-checker の管理域なので触らない）。新規作成時の
      dependsOn/dependedOnBy は data-trace-deps から機械導出する。
    """
    updated_reqs = []
    skipped_reqs = []
    created_des = []
    updated_des = []
    all_reqs = sorted(set(req_design) | set(req_refs))
    for req in all_reqs:
        node = led.get(req)
        if not isinstance(node, dict):
            skipped_reqs.append(req)
            continue
        new_design = sorted(set(req_design.get(req, [])))
        new_code = sorted(set(req_refs.get(req, [])))
        if node.get("design") != new_design or node.get("code") != new_code:
            node["design"] = new_design
            node["code"] = new_code
            updated_reqs.append(req)
    # 逆依存（dependedOnBy）は dependsOn から機械導出できる（新規作成ノード用）
    depended_on_by = {}
    for did, deps in des_deps.items():
        for dep in deps:
            depended_on_by.setdefault(dep, set()).add(did)
    for did in sorted(set(des_refs) | set(des_deps)):
        refs = sorted(set(des_refs.get(did, [])))
        node = led.get(did)
        if isinstance(node, dict):
            if node.get("code") != refs:
                node["code"] = refs
                updated_des.append(did)
        elif did in des_deps:  # 設計に実在する DES のみ新規作成（dangling マーカーで台帳を汚染しない）
            led[did] = {"code": refs, "dependsOn": sorted(set(des_deps.get(did, []))),
                        "dependedOnBy": sorted(depended_on_by.get(did, set()))}
            created_des.append(did)
    return {"updated_reqs": updated_reqs, "created_des": created_des,
            "updated_des": updated_des, "skipped_reqs": skipped_reqs}


def main():
    ensure_project_root()
    if not LEDGER.exists():
        print("エラー: docs/trace/traceability.json がありません（台帳の生成は "
              "consistency-checker の責務。先に台帳を作成してください）。", file=sys.stderr)
        return 2
    try:
        led = json.loads(LEDGER.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"エラー: traceability.json が読めません: {e}", file=sys.stderr)
        return 2
    if not isinstance(led, dict):
        print("エラー: traceability.json のトップレベルがオブジェクトでない", file=sys.stderr)
        return 2
    req_design, des_deps = harvest_design_links()
    des_refs, req_refs = harvest_src_refs()
    summary = fill(led, req_design, des_deps, des_refs, req_refs)
    # アトミック書込（security-review対応）: write_text 直書きは truncate 後の中断で
    # 台帳が 0 バイト/不完全 JSON になり、以降の全ゲートが ledger high で停止する。
    tmp = LEDGER.with_name(LEDGER.name + ".tmp")
    tmp.write_text(json.dumps(led, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(LEDGER)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
