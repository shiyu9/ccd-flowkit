#!/usr/bin/env python
"""result_scan.py
試験結果ドキュメントの「文書内の件数自己整合」を決定論的に点検する。フェーズゲートで実行。

是正・再試験後にサマリーは更新したが集計表（合計行）は旧値のまま、のような
『修正後の更新抜け漏れ』による文書内矛盾を検出する。

各 docs/test/*.md について、文書レベルの判定総数（PASS/FAIL/SKIP）を3系統から抽出する:
  - 2列の判定内訳行     例: | **FAIL** | 0 | 0% |        → FAIL 総数 0
  - 多列集計表の合計行   例: | **合計** | 33 | 2 | 1 | 36 |（ヘッダーの PASS/FAIL/SKIP 列に対応）
  - 個別合否の実数       例: 各試験の `**合否**: PASS` 宣言を数えた実数（サマリーの裏取り）
同一判定の文書レベル総数が2つ以上の異なる値を持てば high（サマリー記載と実際の個別合否の不一致など）。
単一記載しか無ければ比較しない（保守的・低誤検出）。

第3系統（個別合否の実数）は、サマリー表の PASS/FAIL 内訳と個別合否宣言の実数が食い違う
ケース（サマリーだけ更新して個別記載を古いまま残す等）を検出するため。

さらに**機械記録との突合**を行う: 単体試験のハーネス一括実行では、結果文書の宣言と実際の
機械記録（test_results.json）が食い違ったまま素通りするリスクがある。結果文書の正準行
`**機械記録**: <docs/test からの相対パス>` が指す JSON の PASS/FAIL/SKIP 実数と、文書内の
個別合否宣言の実数を突合し、不一致・参照先不在は high。単体試験結果に機械記録行が無い
場合は medium（ハーネス実行の生記録の添付は test-conventions.md で必須）。

high が1件でもあれば exit 1。LLM 判断は使わない。
使い方: python scripts/result_scan.py
"""
import glob
import json
import pathlib
import re
import sys

# Windows コンソール(cp932)で日本語を print してもクラッシュしないよう stdout/stderr を UTF-8 化。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 判定ラベルの正規化（合格/不合格 と PASS/FAIL/SKIP を統一）
VERDICT_MAP = {
    "pass": "PASS", "合格": "PASS",
    "fail": "FAIL", "不合格": "FAIL",
    "skip": "SKIP", "スキップ": "SKIP",
}
TOTAL_LABELS = {"合計", "総計", "total", "計"}
INT_RE = re.compile(r"^\d+$")

# 個別試験の合否宣言（例: `**合否**: PASS` / `**合否**: **PASS**` / `合否: 合格` / `合否：PASS`）。
# 『合否』直後にコロン（半角/全角、前後の太字 ** は許容）が続き判定トークンで終わる宣言のみ拾う。
# コロンを必須にすることで散文（例『本試験の合否は…PASSとする基準』）を誤カウントしない。
# 見出し『合否判定基準との照合』『#### 合否判定』もコロン+判定が無いので拾わない。
# 判定語彙は VERDICT_MAP を単一情報源として正規表現を組み立てる（語彙の二重管理を避ける）。
_VERDICT_ALT = "|".join(re.escape(k) for k in sorted(VERDICT_MAP, key=len, reverse=True))
# 「合否」の直前に漢字が続く派生ラベル（初回合否/再試験合否/全体合否 等）は最終合否として
# 数えない（負の後読み）。派生ラベルが二重計上されると機械記録と件数不一致になり T_GATE
# を無駄に周回するため。最終合否の正準は `**合否**: PASS`。
VERDICT_DECL_RE = re.compile(
    r"(?<![一-龥])合否\*{0,2}\s*[:：]\s*\*{0,2}\s*(?:条件付き\s*)?(" + _VERDICT_ALT + r")",
    re.IGNORECASE)

# 機械記録の正準行（例: `**機械記録**: evidence/unit_results.json`）。パスは文書のある
# docs/test/ からの相対パス。ハーネス一括実行の生記録（JSON）を指す。
# 誤検出防止のため (1)行頭（箇条書き記号は許容）から始まり (2)パスが .json で終わる行のみ拾う
# （『**機械記録**: なし（手動実施のため）』のような散文注記や規約引用をパス扱いしない）。
MACHINE_RECORD_RE = re.compile(
    r"^\s*(?:[-*+]\s+)?\*{0,2}機械記録\*{0,2}\s*[:：]\s*\*{0,2}\s*(\S+\.json)\b",
    re.MULTILINE)

# 機械記録 JSON 内で合否を表すキー（dict 1つ＝試験1件として最初に一致したキーのみ数える）
_STATUS_KEYS = ("status", "result", "verdict", "合否")


def _norm(cell):
    """セル文字列を正規化し、判定ラベルなら PASS/FAIL/SKIP を返す（違えば None）。

    「条件付き」プレフィックスは剥離してから照合する（規約は条件付き使用を禁止するが、
    実文書に残った場合に集計から漏れないようにする安全ネット）。
    """
    s = cell.strip().strip("*").strip()
    s = re.sub(r"^条件付き\s*", "", s)
    s = s.lower()
    return VERDICT_MAP.get(s)


def _cells(line):
    """マークダウンのテーブル行をセルのリストに分解する（前後の空セルを除く）。"""
    return [c.strip().strip("*").strip() for c in line.strip().strip("|").split("|")]


def _as_int(cell):
    s = cell.strip().strip("*").strip()
    return int(s) if INT_RE.match(s) else None


# コードフェンス（``` ... ```）とインラインコードスパン（`...` / ``...``）。是正記録・
# レビュー記録が旧問題の合否宣言を引用しただけで実宣言として二重計上されると T_GATE の
# 書式連鎖を生むため。マスクは空白置換で行い、行構造（MULTILINE アンカー・行数）を保つ。
CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
# 二重バッククォートスパン（`` `合否: X` `` のようにバッククォート自体を含む引用）を
# 先に試し、次に単一スパン。いずれも行を跨がない。
INLINE_CODE_RE = re.compile(r"``[^\n]+?``|`[^`\n]+`")


def mask_code_spans(text):
    """コードフェンス・インラインコード内を空白でマスクする（純関数・冪等）。

    規約（test-conventions.md）は合否宣言形の文字列の例示・引用をコードフェンスで
    囲むことを求める——本マスクはその規約の機械側の受け皿。改行は保持して
    行番号・アンカーを崩さない。順序はフェンスが先（フェンス全体を空白化してから
    インラインを処理する。逆順だとフェンス内の `インライン` が部分マッチして
    フェンス境界の対応が崩れ、マスク漏れが生じうる）。"""
    def _blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    return INLINE_CODE_RE.sub(_blank, CODE_FENCE_RE.sub(_blank, text))


def count_verdict_declarations(text):
    """文書内の個別合否宣言（`**合否**: PASS` 等）の実数を数える（純関数）。

    コードフェンス・インラインコード内の宣言形（例示・旧問題の引用）は数えない。"""
    tally = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for tok in VERDICT_DECL_RE.findall(mask_code_spans(text)):
        v = _norm(tok)
        if v:
            tally[v] += 1
    return tally


def count_json_verdicts(obj, _counted=None):
    """機械記録 JSON から PASS/FAIL/SKIP の実数を数える（純関数・再帰）。

    dict は「合否キー（status/result/verdict/合否）を持てば試験1件」として最初に一致した
    キーだけ数え、数えた dict の中へは再帰しない（入れ子の二重計上防止）。
    リスト・その他の dict は再帰して構造非依存に数える。
    """
    tally = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    if isinstance(obj, dict):
        for key in _STATUS_KEYS:
            v = obj.get(key)
            if isinstance(v, str):
                norm = _norm(v)
                if norm:
                    tally[norm] += 1
                    return tally  # この dict は1件として計上済み。中へは潜らない
        for v in obj.values():
            sub = count_json_verdicts(v)
            for k in tally:
                tally[k] += sub[k]
    elif isinstance(obj, list):
        for item in obj:
            sub = count_json_verdicts(item)
            for k in tally:
                tally[k] += sub[k]
    return tally


def parse_machine_record_paths(text):
    """正準行 `**機械記録**: <path>` の相対パスを抽出する（純関数）。

    - コードフェンス・インラインコード内の正準行（規約引用・例示）は拾わない。
    - **重複パスは1件に丸める**（順序保持）: サマリ節と個別節が同じ evidence JSON を
      参照した場合に合算が二重計上され、宣言実数との不一致 high を誤発報するため。
      異なる記録ファイルへの分割参照は従来どおり全て合算する。"""
    seen = set()
    out = []
    for m in MACHINE_RECORD_RE.findall(mask_code_spans(text)):
        p = m.strip()
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def check_machine_records(text, doc_dir):
    """機械記録の突合 findings を返す。doc_dir は結果文書のあるディレクトリ（docs/test）。

    - 参照先が不在/読めない/パスが docs/test 外へ逃げる → high（証跡主張が成立しない）
    - **全機械記録の合算** PASS/FAIL/SKIP 実数と、文書の個別合否宣言の実数が不一致 → high
      （モジュール別に複数の機械記録へ分割した場合や、再試験で記録を追加した場合に、
      個々のファイルと文書全体を1対1で比べる誤検出をしない。宣言0件の文書は比較しない＝保守的）
    """
    out = []
    doc_dir = pathlib.Path(doc_dir)
    declared = count_verdict_declarations(text)
    machine_total = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    readable_records = []
    for rel in parse_machine_record_paths(text):
        norm = rel.replace("\\", "/")
        if norm.startswith("/") or ".." in norm.split("/") or re.match(r"^[A-Za-z]:", norm):
            out.append(("high", "machine_record_unverifiable",
                        f"機械記録のパスが docs/test 外を指す: {rel}（docs/test からの相対パスで記す）"))
            continue
        target = doc_dir / norm
        if not target.exists():
            out.append(("high", "machine_record_unverifiable",
                        f"機械記録の参照先が存在しない: {rel}（証跡主張が検証不能）"))
            continue
        try:
            data = json.loads(target.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            out.append(("high", "machine_record_unverifiable",
                        f"機械記録が JSON として読めない: {rel}"))
            continue
        counts = count_json_verdicts(data)
        readable_records.append(rel)
        for k in machine_total:
            machine_total[k] += counts[k]
    if readable_records:
        if sum(declared.values()) > 0:
            diffs = [f"{k}: 宣言={declared[k]} / 機械記録合算={machine_total[k]}"
                     for k in ("PASS", "FAIL", "SKIP") if declared[k] != machine_total[k]]
            if diffs:
                out.append(("high", "machine_record_mismatch",
                            f"機械記録（{', '.join(readable_records)}）の合算と文書の合否宣言が不一致"
                            f"（{' , '.join(diffs)}）。再実行して機械記録を更新するか、宣言を実態に合わせること"))
        elif machine_total["FAIL"] > 0:
            # security-review検出（マスク導入の副作用封鎖）: 宣言を全てコードフェンスに
            # 入れると宣言0件になり突合がスキップされ、機械記録のFAILを隠してゲートを
            # 通過できてしまう。機械記録にFAILがあるのに文書に宣言が1件も無いのは
            # 「宣言なし」でなく「隠蔽の疑い」として fail-closed で high にする
            # （全PASSの機械記録＋表のみの文書は従来どおり保守的にスキップ）。
            out.append(("high", "machine_record_mismatch",
                        f"機械記録（{', '.join(readable_records)}）に FAIL が"
                        f"{machine_total['FAIL']}件あるが、文書に合否宣言が1件も無い"
                        f"（コードフェンス内の宣言は実宣言として数えない）。"
                        f"FAIL を含む結果は地の文の宣言として明記すること"))
    return out


def scan_text(text):
    """1つの試験結果テキストを点検し findings のリストを返す（純関数・テスト可能）。

    判定総数を2つの独立チャネルから別々に集める:
      - 内訳(breakdown): `| FAIL | 0 |` のような判定ラベル先頭の2列行（サマリー内訳表）
      - 合計(total): 多列集計表の合計行（ヘッダーの判定列に対応）
    誤検出を避けるため、**両チャネルが共に確定値を持ち、かつ食い違う場合のみ** high にする。
    - 内訳は各判定が1回だけ現れたものを「サマリー総数」とみなす（同一判定が複数回＝試験番号等の
      明細行なので総数扱いしない）。
    - 合計は全合計行の値が一致した場合のみ確定値とみなす（複数フェーズ等で食い違う場合は曖昧として除外）。
    こうして「サマリー内訳 vs 集計表合計」の更新漏れ（FAIL 0 vs 2 のような食い違い）を検出する。
    """
    breakdown = {"PASS": [], "FAIL": [], "SKIP": []}  # 判定ラベル先頭2列行の値
    totalrow = {"PASS": [], "FAIL": [], "SKIP": []}   # 合計行の値

    # 既知の限界: 合否宣言を全件数えるため、1試験に複数の合否宣言を残す文書（同一試験の
    # 再試験記録など）では実数が膨らみうる。本フローの結果文書は試験ごとに最終合否を1件
    # 記す規約のため通常は問題にならない。判定が0件の文書はサマリーと比較しない（保守的）。
    # コードフェンス・インラインコード内の引用（旧サマリ表・旧宣言）は全チャネルで対象外にする。
    # 宣言カウントだけマスクしてテーブル走査を生テキストで行うと、フェンス内の旧表だけが
    # breakdown/totalrow に乗って2チャネルが食い違い、mismatch high を誤発報する（非対称の防止）。
    text = mask_code_spans(text)

    # 個別試験の `**合否**: PASS` を数える（サマリーの裏取り用の独立チャネル）。
    tally = count_verdict_declarations(text)

    header = None  # 直近のテーブルヘッダーの {列index: verdict}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            header = None  # テーブル外でヘッダー対応を切る
            continue
        if set(stripped) <= set("|-: "):
            continue  # 区切り行
        cells = _cells(line)
        if not cells:
            continue

        # ヘッダー検出: cells[1:] に判定ラベルが2つ以上並ぶ行
        verdict_cols = {i: _norm(c) for i, c in enumerate(cells) if _norm(c)}
        if sum(1 for i in verdict_cols if i >= 1) >= 2:
            header = verdict_cols
            continue

        # 合計行: ヘッダーの判定列を文書レベル総数として採用
        if header and cells[0].strip().lower() in TOTAL_LABELS:
            for i, verdict in header.items():
                if i < len(cells):
                    v = _as_int(cells[i])
                    if v is not None:
                        totalrow[verdict].append(v)
            continue

        # 2列の判定内訳行: cells[0] が判定ラベル、cells[1] が整数
        v0 = _norm(cells[0])
        if v0 and len(cells) >= 2:
            v = _as_int(cells[1])
            if v is not None:
                breakdown[v0].append(v)

    out = []
    tally_present = sum(tally.values()) > 0
    for verdict in ("PASS", "FAIL", "SKIP"):
        # 内訳: 当該判定がちょうど1回ならサマリー総数として確定
        b_vals = breakdown[verdict]
        b_total = b_vals[0] if len(b_vals) == 1 else None
        # 合計行: 全て同値なら確定、食い違えば曖昧として除外（保守的）
        t_set = set(totalrow[verdict])
        t_total = next(iter(t_set)) if len(t_set) == 1 else None
        # 確定した系統を集める（個別合否は宣言が1件でもあれば全判定の実数を採用）
        sources = []
        if b_total is not None:
            sources.append(("サマリー内訳", b_total))
        if t_total is not None:
            sources.append(("集計表合計", t_total))
        if tally_present:
            sources.append(("個別合否の実数", tally[verdict]))
        # 2系統以上が確定し、かつ値が食い違う場合のみ更新漏れ/裏取り不一致と判定
        if len({v for _, v in sources}) >= 2:
            detail = " / ".join(f"{k}={v}" for k, v in sources)
            out.append(("high", "result_count_mismatch",
                        f"{verdict} の件数が文書内で不一致: {detail}"))
    return out


# 誤 cwd で docs/ が存在せず glob が空振りする「無検査 green」を防ぐ（project_guard.py 参照）
try:
    from project_guard import ensure_project_root
except ImportError:  # 配置差異でも fail-open にしない最小フォールバック
    def ensure_project_root():
        import pathlib as _p, sys as _s
        if not _p.Path('docs').is_dir():
            print('エラー: docs/ が無い（プロジェクトルート外での実行）', file=_s.stderr)
            _s.exit(2)


def main():
    ensure_project_root()
    findings = []
    for p in glob.glob("docs/test/*.md"):
        path = pathlib.Path(p)
        text = path.read_text(encoding="utf-8", errors="ignore")
        for sev, kind, msg in scan_text(text):
            findings.append((sev, kind, f"{p}: {msg}"))
        # 機械記録の突合は試験結果文書のみ（根本原因解析.md 等が正準行を引用しても誤検出しない）
        if "試験結果" in path.name:
            for sev, kind, msg in check_machine_records(text, path.parent):
                findings.append((sev, kind, f"{p}: {msg}"))
            # 単体試験（ハーネス一括実行）は機械記録の添付が必須（test-conventions.md）。
            # 行が無い＝宣言合否を機械記録で裏取りできない状態を medium で可視化する。
            if "単体試験結果" in path.name and not parse_machine_record_paths(text):
                findings.append(("medium", "machine_record_absent",
                                 f"{p}: 機械記録行（**機械記録**: <path>.json）が無い。ハーネス実行の"
                                 f"生記録(JSON)を docs/test/evidence/ に保存し正準行で参照すること"))
    for sev, kind, msg in findings:
        print(f"[{sev}] {kind}: {msg}")
    highs = [f for f in findings if f[0] == "high"]
    print(f"\nsummary: high={sum(1 for f in findings if f[0]=='high')} "
          f"medium={sum(1 for f in findings if f[0]=='medium')} "
          f"low={sum(1 for f in findings if f[0]=='low')}")
    sys.exit(1 if highs else 0)


if __name__ == "__main__":
    main()
