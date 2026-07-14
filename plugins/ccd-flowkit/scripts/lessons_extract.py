#!/usr/bin/env python
"""lessons_extract.py
レビュー記録・試験結果から教訓候補を決定論的に抽出する。

入力: docs/ 配下のレビュー記録と試験結果（Markdown / HTML）。
出力: 構造化 JSON（教訓候補リスト）を stdout に出力。
抽出条件:
  - 重要度「高」で是正された指摘（深刻な問題の事後発見 → 予防ルール候補）
  - 差し戻し（同一箇所への複数回指摘）が発生した指摘（繰り返し → 規約化の価値が高い）
  - 試験 NG → 修正 → 再試験の履歴（構築・設計起因の不具合パターン）

LLM 判断は使わない。「何が教訓か」の判断は lessons-distiller に委ねる。
使い方: python scripts/lessons_extract.py [docs_dir]
"""
import glob
import json
import os
import pathlib
import re
import sys

# Windows コンソール(cp932)で日本語を print してもクラッシュしないよう stdout/stderr を UTF-8 化。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ---- 指摘パーサ ----

SEVERITY_RE = re.compile(
    r"[*\-]\s*(?:\*\*)?(?:重要度\s*[:：]\s*)?(?:\*\*)?"
    r"[「\[]?(高|中|低)[」\]]?(?:\*\*)?"
    r"\s*\s[/／|]\s"
    r"(.+?)"
    r"\s[/／|]\s"
    r"(.+?)"
    r"\s[/／|]\s"
    r"(.+)",
)

REWORK_HEADER_RE = re.compile(
    r"(?:是正|修正|差し戻し|再レビュー|rework|再指摘)",
    re.IGNORECASE,
)

# 失敗「判定」を表す語のみ（NG / 不合格 / FAIL）。「失敗」「不具合」等の描写語は
# 試験名・観点（例「不正入力で失敗すること」）に頻出し PASS 行でも誤検出するため含めない。
TEST_NG_RE = re.compile(
    r"(?:(?<![A-Za-z])NG(?![A-Za-z])|不合格|(?<![A-Za-z])FAIL(?![A-Za-z]))",
    re.IGNORECASE,
)

# 失敗判定語のゼロ値・是正遷移の文脈（試験IDを含むサマリ行
# 「TEST-106）…52件全てPASS・FAIL 0件」「（下記TEST-001〜TEST-036、36 PASS/0 FAIL/0 SKIP）」
# を test_failure として誤抽出しないため）。この文脈に**含まれない**判定語が残る行だけを失敗とする。
#   - `FAIL 0件` / `FAIL: 0`（後続ゼロ。桁続き `FAIL: 01` は除外しない）
#   - `0 FAIL` / `/0 FAIL`（先行ゼロ。`10 FAIL` のような非ゼロ数は除外しない）
#   - `NG→PASS` / `FAIL -> PASS`（是正遷移の記録）
TEST_NG_ZERO_CONTEXT_RE = re.compile(
    r"(?:(?:NG|不合格|FAIL)\s*[:：]?\s*0(?:\s*件)?(?![0-9])"
    r"|(?<![0-9])0\s*(?:NG|FAIL|不合格)"
    r"|(?:NG|不合格|FAIL)\s*(?:→|->)\s*PASS)",
    re.IGNORECASE,
)


def line_has_real_ng(line):
    """行に「ゼロ値・是正遷移の文脈でない」失敗判定語が残るか（純関数）。

    ゼロ文脈のスパンを塗りつぶしてから TEST_NG_RE を探すことで、
    「FAIL 0件」だけの行は偽、そこに `TEST-007: NG` が同居する行は真、を両立する。
    """
    masked = TEST_NG_ZERO_CONTEXT_RE.sub(lambda m: " " * len(m.group(0)), line)
    return bool(TEST_NG_RE.search(masked))

# 試験ID（例 TEST-001 / TC-003 / IT-005）。実際の失敗は試験結果の必須要素として試験IDを伴う
# （test-conventions）。語境界＋ハイフン必須にし、output の "ut"・commit の "it" 等の部分一致を防ぐ。
TEST_ID_RE = re.compile(r"(?<![A-Za-z])(?:TEST|TC|UT|IT|ST)-\d+", re.IGNORECASE)

TEST_CAUSE_RE = re.compile(
    r"(?:原因|要因|起因|cause)\s*[:：]\s*(.+)",
    re.IGNORECASE,
)

CATEGORY_KEYWORDS = {
    "error-handling": ["エラー", "例外", "exception", "error", "ハンドリング", "未処理"],
    "security": ["脆弱性", "セキュリティ", "injection", "XSS", "CSRF", "認証", "認可"],
    "traceability": ["トレーサビリティ", "追跡", "trace", "REQ-", "DES-", "TEST-"],
    "naming": ["命名", "名前", "naming", "規約"],
    "design": ["設計", "単一責務", "疎結合", "境界条件", "design"],
    "testing": ["試験", "テスト", "test", "カバレッジ", "網羅"],
    "performance": ["性能", "パフォーマンス", "performance", "メモリ", "リソース"],
}


def classify(text):
    """キーワードベースでカテゴリを推定する。"""
    text_lower = text.lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                return cat
    return "other"


# ---- レビュー記録パーサ ----

def parse_review_findings(text, source_file):
    """レビュー記録テキストから指摘行を抽出する。

    フォーマット: 「重要度(高/中/低) / 該当箇所 / 指摘 / 是正案」
    Markdown テーブル行（| で囲まれた行）は試験結果等の誤検出を避けるため除外する。
    """
    results = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") or stripped.startswith("|-"):
            continue
        m = SEVERITY_RE.search(line)
        if m:
            results.append({
                "source_file": source_file,
                "severity": m.group(1),
                "location": m.group(2).strip(),
                "finding": m.group(3).strip(),
                "remediation": m.group(4).strip(),
                "category": classify(m.group(3)),
            })
    return results


def detect_rework_sections(text):
    """差し戻し・再レビューのセクションの有無を検出する。"""
    return bool(REWORK_HEADER_RE.search(text))


def count_location_occurrences(findings_list):
    """同一ファイル内の同一箇所への指摘回数をカウントし rework_count を付与する。

    異なるレビューファイルからの独立した指摘を差し戻しと誤判定しないよう、
    (source_file, location) のペアでカウントする。
    """
    loc_counts = {}
    for f in findings_list:
        key = (f["source_file"], f["location"])
        loc_counts[key] = loc_counts.get(key, 0) + 1
    for f in findings_list:
        key = (f["source_file"], f["location"])
        f["rework_count"] = loc_counts[key]
    return findings_list


# ---- 試験結果パーサ ----

def parse_test_failures(text, source_file):
    """試験結果から NG 項目を抽出する。

    失敗とみなすのは「試験ID」と「失敗判定語(NG/不合格/FAIL)」を併せ持つ行のみ。
    試験IDの無い NG 語（表ヘッダーの FAIL 列・集計セル「FAIL: 0」・見出し「## 残不具合」等）や、
    判定でなく描写の語（PASS 行の「失敗の検証」等）、試験IDを含むサマリ行のゼロ値
    （「FAIL 0件」「36 PASS/0 FAIL」）・是正遷移（「NG→PASS」）は偽陽性として除外される。
    実データ行の FAIL（例: `| TC-003 | ... | FAIL |`、`TEST-001: 不合格`）は検出する。
    """
    results = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if TEST_ID_RE.search(line) and line_has_real_ng(line):
            cause = ""
            for j in range(i + 1, min(i + 5, len(lines))):
                cm = TEST_CAUSE_RE.search(lines[j])
                if cm:
                    cause = cm.group(1).strip()
                    break
            results.append({
                "source_file": source_file,
                "type": "test_failure",
                "description": line.strip().lstrip("-* "),
                "cause": cause,
                "category": classify(line + " " + cause),
            })
    return results


# ---- ファイル収集 ----

REVIEW_GLOBS = [
    "docs/design/設計書.md",
    "docs/design/設計書.html",
    "docs/build/構築レビュー記録.md",
    "docs/build/単体試験計画.md",
    "docs/design/結合試験計画.md",
    "docs/requirements/総合試験計画.md",
]

TEST_RESULT_GLOBS = [
    "docs/test/単体試験結果.md",
    "docs/test/結合試験結果.md",
    "docs/test/総合試験結果.md",
]


def collect_files(base_dir, patterns):
    """glob パターンに一致するファイルを収集する。"""
    found = []
    for pat in patterns:
        full = os.path.join(base_dir, pat)
        found.extend(glob.glob(full))
    return found


def _strip_html_tags(text):
    """HTML タグを除去してプレーンテキストにする（指摘行の抽出用）。

    <li> はリストマーカー「- 」に変換し、他のタグはスペースに置換する。
    """
    text = re.sub(r"<li\b[^>]*>", "- ", text, flags=re.IGNORECASE)
    return re.sub(r"<[^>]+>", " ", text)


# ---- メイン抽出ロジック ----

def extract(docs_dir="."):
    """教訓候補を抽出して構造化リストで返す。"""
    all_findings = []
    has_rework = False

    for fp in collect_files(docs_dir, REVIEW_GLOBS):
        text = pathlib.Path(fp).read_text(encoding="utf-8", errors="ignore")
        if fp.endswith(".html"):
            text = _strip_html_tags(text)
        findings = parse_review_findings(text, fp)
        all_findings.extend(findings)
        if detect_rework_sections(text):
            has_rework = True

    all_findings = count_location_occurrences(all_findings)

    test_failures = []
    for fp in collect_files(docs_dir, TEST_RESULT_GLOBS):
        text = pathlib.Path(fp).read_text(encoding="utf-8", errors="ignore")
        test_failures.extend(parse_test_failures(text, fp))

    candidates = []

    for f in all_findings:
        reason = []
        if f["severity"] == "高":
            reason.append("severity_high")
        if f["rework_count"] >= 2:
            reason.append("rework")
        if reason:
            candidates.append({
                "type": "review_finding",
                "extraction_reason": reason,
                **f,
            })

    for tf in test_failures:
        candidates.append({
            "extraction_reason": ["test_ng"],
            **tf,
        })

    return {
        "version": 1,
        "docs_dir": docs_dir,
        "total_findings": len(all_findings),
        "total_test_failures": len(test_failures),
        "has_rework_sections": has_rework,
        "candidates": candidates,
    }


def main():
    docs_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    result = extract(docs_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["candidates"]:
        print("\n教訓候補はありません。", file=sys.stderr)
    else:
        print(f"\n教訓候補: {len(result['candidates'])} 件", file=sys.stderr)


if __name__ == "__main__":
    main()
