#!/usr/bin/env python
"""ontology_scan.py
設計判断（決定事項）の「正本＝参照」整合を決定論的に点検する。フェーズゲートで実行。

オントロジーの考え方に倣い、確定した設計判断（終了コード・出力フォーマット等）を
正本 `docs/design/decisions.json` に一意の個体として保持し、下流文書（試験計画・設計書）は
値を複製せず参照トークン `[D:<id>=<value>]` で引く。確定した決定に反して下流文書が値を独立に
主張し、決定確定後も是正されず陳腐化するケースを、値を正本から引いて機械照合することで
「修正後の更新漏れ」として検出する。

正本（台帳）: docs/design/decisions.json
  {"version":1,"decisions":[
     {"id":"UD-07","slug":"usage_exit_code","value":"1","status":"confirmed"}, ...]}
参照トークン（下流文書の本文）: [D:UD-07=1]

検査（high が1件でも exit 1）:
  - 台帳不備: 重複 id / 必須フィールド(id,value,status)欠落 / status が confirmed|provisional 以外。→ high
  - dangling: 参照する [D:id=...] の id が台帳に無い。→ high
  - provisional 参照: 参照先 decision の status が confirmed でない（未確定を確定として下流に固定）。→ high
  - 値不一致: トークンの値が台帳の正本値と異なる（複製された値の陳腐化）。→ high
  - 根拠欠落: confirmed の decision に根拠(evidence)が無い（裏取りされない確定）。→ high
  - 根拠不正: evidence の型/形式が不正（vrr:/codebase:/ud/spec 以外・vrr topic 不正・codebase 絶対/traversal）。→ high
  - 根拠ダングリング: evidence の参照先(vrr:/codebase:)が実在しない（根拠文書/コード欠落）。→ high
  - 未参照: confirmed の decision がどの文書からも参照されない。→ medium（任意・横展開漏れの示唆）

台帳もトークンも無ければ何もしない（非オントロジー・プロジェクトを壊さない＝fail-open）。
散文中に値が直書きされ token 化されていないケースは決定論では追えないため consistency-checker
（LLM）が担う。役割分担は trace_scan / evidence_scan と同じ思想。

トークンの値は `]` を含めない（角括弧で区切るため）。したがって本機構は終了コード・真偽・列挙の
選択肢のようなスカラ値（陳腐化が最も起きやすく最も照合価値が高い）向け。フォーマット文字列など
`]` を含む複雑値は token 化せず、その整合は consistency-checker（LLM）に委ねる。

LLM 判断は使わない。使い方: python scripts/ontology_scan.py
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

# 台帳も文書も同じプロジェクト配下にあり、ゲートはプロジェクト直下を cwd に実行する。
# 兄弟スキャナ(evidence_scan/result_scan/trace_scan)と同様、いずれも cwd 相対で解決する
# （台帳だけ別基準にすると、ディレクトリ不整合で台帳は読めるが文書が拾えず空振りする）。
LEDGER_FILE = pathlib.Path("docs/design/decisions.json")

VALID_STATUS = {"confirmed", "provisional"}
# 台帳 value に許す型（スカラのみ。null/配列/オブジェクトは正本値として不正）。
SCALAR_TYPES = (str, int, float, bool)

# 参照トークン: [D:UD-07=1]。id は英字始まりの語＋ハイフン。
# 値は 1 文字以上・同一行・] を含まない（空値や改行またぎ・ネストの暴走を防ぐ＝スカラ専用）。
DECISION_REF_RE = re.compile(r"\[D:([A-Za-z][\w-]*)=([^\]\n]+)\]")

# 走査から除外するディレクトリ（規約参照ファイルがトークン書式を例示するため）。
EXCLUDED_DIRS = {"conventions"}


def is_excluded(path):
    """パスが走査除外ディレクトリ配下なら True（区切り文字 / と \\ の両方に対応）。"""
    return any(part in EXCLUDED_DIRS for part in re.split(r"[\\/]+", str(path)))


def _strip_code(text):
    """フェンス/コードブロック/HTMLコメントを除去する。

    規約や設計書がトークン書式 `[D:...=...]` を例示する箇所を実参照として誤検出しないため。
    インラインのバッククォート単体は除去しない（散在する `…` 対が本文の実参照を巻き込んで
    消す副作用があるため）。書式例は docs/conventions/（走査除外）かフェンス内に置く規約。
    """
    return re.sub(
        r"```.*?```|<pre\b[^>]*>.*?</pre>|<code\b[^>]*>.*?</code>|<!--.*?-->",
        " ", text, flags=re.DOTALL | re.IGNORECASE,
    )


def _norm_value(s):
    """値の正規化。前後空白・囲みのバッククォート/引用符を除去して比較する。"""
    s = s.strip()
    for q in ("`", '"', "'"):
        if len(s) >= 2 and s[0] == q and s[-1] == q:
            s = s[1:-1].strip()
    return s


def load_ledger(path=LEDGER_FILE):
    """決定台帳を読み、(by_id, findings) を返す。台帳が無ければ (None, [])。

    by_id: {id: {"value": str, "status": str, "slug": str, "evidence": str,
                 "doc_reference": str, "doc_reference_reason": str}}。
    findings: 台帳自体の不備（high）。破損 JSON は high を1件返し by_id を空にする。
    """
    if not path.exists():
        return None, []
    findings = []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (json.JSONDecodeError, ValueError):
        return {}, [("high", "ontology_ledger_broken",
                     "decisions.json が不正な JSON で読み込めない")]
    decisions = data.get("decisions") if isinstance(data, dict) else None
    if not isinstance(decisions, list):
        return {}, [("high", "ontology_ledger_broken",
                     "decisions.json に decisions 配列が無い")]
    by_id = {}
    for d in decisions:
        if not isinstance(d, dict):
            findings.append(("high", "ontology_ledger_broken",
                             "decisions の要素がオブジェクトでない"))
            continue
        did = d.get("id")
        if not isinstance(did, str) or not did:
            findings.append(("high", "ontology_ledger_broken",
                             "decision に id が無い"))
            continue
        if "value" not in d or "status" not in d:
            findings.append(("high", "ontology_ledger_broken",
                             f"{did}: value または status が欠落している"))
            continue
        value = d.get("value")
        if not isinstance(value, SCALAR_TYPES):
            findings.append(("high", "ontology_ledger_broken",
                             f"{did}: value がスカラでない（{type(value).__name__}）。"
                             "終了コード等のスカラ値を文字列で持つこと"))
            continue
        status = d.get("status")
        if status not in VALID_STATUS:
            findings.append(("high", "ontology_ledger_broken",
                             f"{did}: status が {sorted(VALID_STATUS)} 以外（{status!r}）"))
            continue
        if did in by_id:
            findings.append(("high", "ontology_ledger_broken",
                             f"{did}: id が台帳内で重複している"))
            continue
        ev = d.get("evidence", "")
        # evidence は文字列参照のみ（value 同様に型を検証）。非文字列(数値/真偽/配列)は
        # str() で偶発的に真の文字列になり『根拠あり』と誤認するため台帳不備として弾く。
        if not isinstance(ev, str):
            findings.append(("high", "ontology_ledger_broken",
                             f"{did}: evidence が文字列でない（{type(ev).__name__}）。"
                             "vrr:/ud/codebase:/spec 等の文字列参照にすること"))
            continue
        # doc_reference（任意）: "not-expected" は「下流文書からの [D:] 参照が構造的に不要」の
        # 台帳内宣言。プロセス上の決定など参照先文書が存在しない決定は、放置すると
        # ontology_unused_decision medium が毎ゲート再報告される恒久ノイズになるため、
        # 台帳側で明示的に免除する。乱用防止に理由（doc_reference_reason）を必須とする。
        doc_ref = d.get("doc_reference", "expected")
        if doc_ref not in ("expected", "not-expected"):
            findings.append(("high", "ontology_ledger_broken",
                             f"{did}: doc_reference が不正（{doc_ref!r}）。"
                             "'expected'（省略時）/'not-expected' のみ"))
            continue
        reason = d.get("doc_reference_reason", "")
        if doc_ref == "not-expected" and (not isinstance(reason, str) or not reason.strip()):
            findings.append(("high", "ontology_ledger_broken",
                             f"{did}: doc_reference=not-expected には doc_reference_reason"
                             "（参照が不要である理由の文字列）が必須"))
            continue
        by_id[did] = {"value": str(value), "status": status,
                      "slug": str(d.get("slug", "")), "evidence": ev.strip(),
                      "doc_reference": doc_ref,
                      "doc_reference_reason": reason.strip() if isinstance(reason, str) else ""}
    return by_id, findings


def check_unused(by_id, referenced_ids, unfounded_ids):
    """未参照の confirmed 決定を検査し findings を返す（純関数・テスト可能）。

    - 通常は medium（横展開漏れの示唆）。
    - doc_reference=not-expected の決定は low に格下げ（台帳内に理由が記録済みのため、
      ゲートごとの同一説明の反復を要しない）。
    - not-expected なのに実際は参照されている決定は medium（宣言の陳腐化。除去すべき。
      根拠欠落 high の有無とは独立の軸なので unfounded 除外の対象にしない）。
    - 未参照 medium/low は、既に根拠欠落(high)で挙げた決定を二重報告しない（1決定1主因）。
    """
    findings = []
    for did, dec in sorted(by_id.items()):
        if dec["status"] != "confirmed":
            continue
        waived = dec.get("doc_reference") == "not-expected"
        if did in referenced_ids:
            # 宣言の陳腐化は unfounded（根拠欠落 high）の有無と独立に検出する
            if waived:
                findings.append(("medium", "ontology_stale_doc_reference",
                                 f"{did}: doc_reference=not-expected だが実際は参照されている"
                                 "（宣言が陳腐化。フィールドを除去すること）"))
            continue
        if did in unfounded_ids:
            continue
        if waived:
            findings.append(("low", "ontology_unused_decision_waived",
                             f"{did}: 参照なしを台帳で許容済み"
                             f"（doc_reference_reason: {dec.get('doc_reference_reason', '')}）"))
        else:
            findings.append(("medium", "ontology_unused_decision",
                             f"{did}: confirmed の決定がどの文書からも参照されていない"))
    return findings


def check_grounding(by_id):
    """confirmed の決定が根拠(evidence)を持つか検査し findings を返す（純関数・テスト可能）。

    確定値は VRR/ud/codebase 等の根拠に裏打ちされるべき。裏取りされない confirmed は、
    内部整合はしていても実機と矛盾したまま下流に固定されるリスクがある。決定論で追えるのは
    『根拠参照の存在』まで。値が根拠・実機と実際に一致するかの意味検証は
    consistency-checker（LLM）が担う。provisional は検討中のため対象外。
    """
    out = []
    for did, dec in sorted((by_id or {}).items()):
        if dec["status"] == "confirmed" and not dec.get("evidence"):
            out.append(("high", "ontology_unfounded_decision",
                        f"{did}: confirmed の決定に根拠(evidence)が無い。"
                        "VRR/ud/codebase 参照を付し、値が根拠で裏付くこと"))
    return out


# evidence の許容型（evidence-conventions の4種）。vrr/codebase は参照先を実在検査する。
VRR_TOPIC_RE = re.compile(r"^[a-z0-9_]{1,64}$")  # evidence-conventions の topic 制約
CODEBASE_RE = re.compile(r"^(?P<file>[^\s:][^\s]*?)(?::(?P<line>\d+))?$")  # <file>[:<line>]


def classify_evidence(ev):
    """evidence 文字列を分類する（純関数）。戻り値 (kind, info)。

    kind='ok_nofile'（ud/spec＝叙述参照、info=None）／'file'（vrr/codebase、info=検査すべき相対パス）／
    'malformed'（型/形式が不正、info=理由）／'empty'。
    型は vrr:/codebase:/ud/spec のみ許容し、タイプミス・大文字・未知型は malformed として弾く
    （『非空文字列なら根拠あり』の黙認を防ぐ）。
    """
    ev = (ev or "").strip()
    if not ev:
        return ("empty", None)
    if ev == "ud" or ev == "spec" or ev.startswith("ud:") or ev.startswith("spec:"):
        return ("ok_nofile", None)
    if ev.startswith("vrr:"):
        topic = ev[4:].strip()
        if not VRR_TOPIC_RE.match(topic):
            return ("malformed", f"vrr topic が不正（^[a-z0-9_]{{1,64}}$ 必須・traversal 不可）: {topic!r}")
        return ("file", f"docs/design/vrr/{topic}.md")
    if ev.startswith("codebase:"):
        ref = ev[len("codebase:"):].strip()
        m = CODEBASE_RE.match(ref)
        if not m:
            return ("malformed", f"codebase 参照が不正: {ref!r}")
        f = m.group("file")
        norm = f.replace("\\", "/")
        if norm.startswith("/") or re.match(r"^[A-Za-z]:", f) or ".." in norm.split("/"):
            return ("malformed", f"codebase は相対パスのみ可（絶対/ドライブ/.. 不可）: {f!r}")
        return ("file", f)
    return ("malformed", f"未知の evidence 型（vrr:/codebase:/ud/spec のいずれか）: {ev!r}")


def check_evidence_quality(by_id, exists=None):
    """confirmed の evidence の型・形式・参照先実在を検査し findings を返す（純関数・テスト可能）。

    『参照の存在』は check_grounding、『型/形式の妥当性と参照先の実在』は本関数。evidence が
    指す VRR 文書やコードが未作成のダングリング状態は、根拠として成立していないため high。
    exists: パス存在判定（既定は実ファイル。テスト注入用）。
    """
    if exists is None:
        exists = lambda p: pathlib.Path(p).exists()
    out = []
    for did, dec in sorted((by_id or {}).items()):
        if dec.get("status") != "confirmed":
            continue
        ev = (dec.get("evidence") or "").strip()
        if not ev:
            continue  # 欠落は check_grounding が担当
        kind, info = classify_evidence(ev)
        if kind == "malformed":
            out.append(("high", "ontology_evidence_malformed",
                        f"{did}: evidence の {info}"))
        elif kind == "file" and not exists(info):
            out.append(("high", "ontology_evidence_dangling",
                        f"{did}: evidence '{ev}' の参照先 {info} が実在しない（根拠文書/コードの欠落）"))
    return out


def find_refs(text):
    """文書本文から参照トークン [D:id=value] を抽出し [(id, value), ...] を返す（純関数）。

    コードスパン/コメントを除いた本文から拾う（書式例の誤検出を避ける）。
    """
    body = _strip_code(text)
    return [(m.group(1), m.group(2)) for m in DECISION_REF_RE.finditer(body)]


def check_refs(refs, by_id):
    """参照リストを台帳と突合し findings を返す（純関数・テスト可能）。

    by_id が None（台帳なし）でもトークンがあれば全て dangling として high にする。
    """
    out = []
    ledger = by_id or {}
    for did, value in refs:
        if did not in ledger:
            out.append(("high", "ontology_dangling_ref",
                        f"参照 [D:{did}=...] の決定 {did} が台帳 decisions.json に存在しない"))
            continue
        dec = ledger[did]
        if dec["status"] != "confirmed":
            out.append(("high", "ontology_provisional_ref",
                        f"未確定の決定 {did}（status={dec['status']}）を確定として参照している"))
            continue
        if _norm_value(value) != _norm_value(dec["value"]):
            out.append(("high", "ontology_value_mismatch",
                        f"{did} の値が不一致: 参照={_norm_value(value)!r} / "
                        f"台帳の正本={_norm_value(dec['value'])!r}（複製値の陳腐化）"))
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


def check_duplicate_ledgers(paths):
    """正本以外の decisions.json を検出して findings を返す（純関数）。

    要件フェーズに独自スキーマの docs/decisions.json が作られ、正本 docs/design/decisions.json と
    並存すると、UD 追加時に片側だけ更新される陳腐台帳になるため。『スカラ値の正本は一つ』
    （本規約）を決定論で強制する。
    """
    out = []
    canonical = "docs/design/decisions.json"
    for p in paths:
        norm = str(p).replace("\\", "/")
        if norm != canonical:
            out.append(("high", "ontology_duplicate_ledger",
                        f"{norm}: 決定台帳の二重化。正本は {canonical} のみ（このファイルを"
                        f"正本へ統合して削除すること。二重台帳は値の陳腐化ハザード）"))
    return out


def main():
    ensure_project_root()
    by_id, findings = load_ledger()

    findings.extend(check_duplicate_ledgers(
        glob.glob("docs/**/decisions.json", recursive=True)))

    targets = []
    for pat in ("docs/**/*.md", "docs/**/*.html"):
        targets.extend(glob.glob(pat, recursive=True))

    all_refs = []
    referenced_ids = set()
    for p in sorted(set(targets)):
        if is_excluded(p):
            continue
        text = pathlib.Path(p).read_text(encoding="utf-8", errors="ignore")
        refs = find_refs(text)
        for sev, kind, msg in check_refs(refs, by_id):
            findings.append((sev, kind, f"{p}: {msg}"))
        all_refs.extend(refs)
        referenced_ids.update(did for did, _ in refs)

    # confirmed の決定は根拠(evidence)必須（high）。台帳がある時のみ。
    unfounded_ids = set()
    if by_id:
        grounding = check_grounding(by_id)
        findings.extend(grounding)
        unfounded_ids = {did for did, dec in by_id.items()
                         if dec["status"] == "confirmed" and not dec.get("evidence")}
        # evidence の型/形式の妥当性＋参照先（vrr:/codebase:）の実在性（high）。
        # 008 UD-03 のダングリングVRR・壊れた根拠表記の黙認対策。
        findings.extend(check_evidence_quality(by_id))

    # 未参照の confirmed 決定（medium・横展開漏れの示唆。doc_reference=not-expected は low）。
    # 台帳がある時のみ。
    if by_id:
        findings.extend(check_unused(by_id, referenced_ids, unfounded_ids))

    order = {"high": 0, "medium": 1, "low": 2}
    for sev, kind, msg in sorted(findings, key=lambda x: order.get(x[0], 9)):
        print(f"[{sev}] {kind}: {msg}")
    highs = [f for f in findings if f[0] == "high"]
    note = "" if (by_id is not None or all_refs) else "（台帳・参照なし: 検査スキップ）"
    print(f"\nsummary: high={sum(1 for f in findings if f[0]=='high')} "
          f"medium={sum(1 for f in findings if f[0]=='medium')} "
          f"low={sum(1 for f in findings if f[0]=='low')} {note}")
    sys.exit(1 if highs else 0)


if __name__ == "__main__":
    main()
