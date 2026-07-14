#!/usr/bin/env python
"""review_authenticity_scan.py
レビュー記録の真正性を決定論的に点検する。フェーズゲートで実行。

「集約役が自己分析した所見を各レビュアー名に割り当て、サブエージェントは未起動なのに
『全起動済み』と記録する」捏造や、AskUserQuestion 無しにユーザー承認を自称して高指摘を
合格化する捏造など、レビュー記録の真正性を損なう事象を機械的に検出する。

検査（high が1件でも exit 1）:
  - 暫定記録: 「結果が届き次第追記」「バックグラウンドで…起動中」「並行起動中」等、未確定のまま
    所見を記したマーカー。全サブエージェント結果が確定してから記録する規約に反する。→ high
  - 起動捏造: レビュー記録の「起動サブエージェント」に挙がった名前が、実際の起動ログ
    (state/_agent_invocations.jsonl) に存在しない。→ high（起動ログがある場合のみ）
  - 追認（再レビュー未実質）: 指摘が「対応状況=未是正」かつ「再判定=—」のまま残る。→ high
  - 無根拠の承認主張: 『ユーザー承認/判断』を主張するが decisions の裏付け参照が無い。→ high

LLM 判断は使わない。使い方: python scripts/review_authenticity_scan.py
"""
import glob
import json
import os
import pathlib
import re
import sys

# Windows(cp932)対策。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

PROJECT_ROOT = pathlib.Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
INVOCATIONS_FILE = PROJECT_ROOT / "state" / "_agent_invocations.jsonl"

# レビュー記録らしさの判定（このいずれかを含む文書を対象にする）
REVIEW_MARKER_RE = re.compile(r"起動サブエージェント|サブエージェント別の所見|レビュー記録")

# 暫定記録（未確定のまま所見を書いた signal）。具体フレーズのみ（広すぎる『起動中。』は不採用）。
PROVISIONAL_RE = re.compile(
    r"結果が届き次第|届き次第追記|バックグラウンドで.{0,12}起動|並行起動中|未確定.{0,6}追記"
)

# 正準の起動サブエージェント宣言行「**起動サブエージェント**: name1, name2」。コロン以降のその行
# （改行まで）だけを起動主張として読む。散文の200文字窓を推測走査する旧方式は、窓内の `DES-001`
# 等ハイフン付きIDをエージェント名と誤認する偽陽性があった。構造化宣言の厳密パースで根治する。
CLAIM_DECL_RE = re.compile(r"起動サブエージェント\*{0,2}\s*[:：]\s*([^\n]*)")
# エージェント名トークン: ハイフンを含む（code-reviewer 等）。任意のプラグイン接頭辞 'prefix:' を許容。
# 宣言行のリスト内トークンのみに適用するため、散文やIDを拾う恐れはない（窓走査を廃止したため）。
AGENT_TOKEN_RE = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9_]*(?:-[A-Za-z0-9_]+)*:)?"  # 任意のプラグイン接頭辞
    r"[A-Za-z][A-Za-z0-9_]*(?:-[A-Za-z0-9_]+)+"        # ハイフン必須のエージェント名
)
# 集計表の判定セルとして扱うダッシュ（em/en のみ。ASCII ハイフンは散文で頻出するため除外）。
DASH_CELL = {"—", "–"}

# ユーザー承認の主張パターン（AskUserQuestion 無しに『ユーザー承認済み』と書き高指摘を
# 合格化する捏造への対策）。**承認を得た/判断が確定した、という完了形の主張のみ**に限定する
# （『ユーザーの判断が必要』『ユーザーが確認しやすい』等の散文＝承認していない記述を誤検出しないため）。
# 主張する行には decisions の裏付け参照が必須（無根拠の承認表記を high 検出）。
APPROVAL_CLAIM_RE = re.compile(
    r"ユーザー(?:承認済み|承認を得|が承認した|により承認|の承認を|確認済み|"
    r"判断により|判断で(?:確定|合格|是正済|解消)|判断済み|"
    # 免除(waiver)の承諾主張も対象（waiver 付き advance は
    # 自己発行できるため、免除注記に decisions 裏付けを機械要求して事後検証を成立させる。
    # 正準の免除行 `**免除**: <範囲> — ユーザー承諾（日付・decisions 参照）` を含む完了形のみ
    # （『ユーザー承諾が必要』等の未然形を誤検出しない）
    r"承諾済み|承諾を得た|承諾[（(])")
# 裏付け参照（decisions 台帳への言及 / 参照トークン）。これが同一行に在れば承認は裏付けありとみなす。
DECISIONS_REF_RE = re.compile(r"decisions\.(?:md|json)|\[D:")


def _bare_agent(name):
    """プラグイン接頭辞を除いた素のエージェント名を返す。

    起動ログは 'ccd-flowkit:code-reviewer' のように接頭辞付きで記録される一方、レビュー記録は
    'code-reviewer' と素で書かれることが多い。素名で突合しないと同一エージェントを未起動と
    誤判定する（捏造の誤検出）。最後の ':' 以降を素名とする。
    """
    return name.rsplit(":", 1)[-1].strip() if isinstance(name, str) else name


# 走査から除外するディレクトリ。docs/conventions/ はフローが配置した規約参照ファイルで、
# 禁止例として『結果が届き次第追記』等の語句を説明文に引用する。これはレビュー記録ではないため
# 真正性検査の対象外にする（規約文書内の引用例を誤検出しないための対策）。
EXCLUDED_DIRS = {"conventions"}


def is_excluded(path):
    """パスが走査除外ディレクトリ配下なら True。

    区切り文字（`/` と `\\`）の両方で分割するため、OS や glob の返す区切りに依存しない
    （pathlib.Path.parts はホスト OS 依存で、POSIX 上ではバックスラッシュを分割しない）。
    """
    parts = re.split(r"[\\/]+", str(path))
    return any(part in EXCLUDED_DIRS for part in parts)


def _strip_html(text):
    return re.sub(r"<[^>]+>", " ", text)


def load_invoked_agents(path=INVOCATIONS_FILE):
    """起動ログから実際に起動されたサブエージェント名の集合を返す（無ければ None）。"""
    if not path.exists():
        return None
    invoked = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            a = d.get("agent")
            if isinstance(a, str) and a:
                invoked.add(a)
        except Exception:
            continue
    return invoked


def extract_claimed_agents(text):
    """レビュー記録の正準宣言行『起動サブエージェント: a, b, c』からエージェント名を厳密に拾う。

    コロン以降のその行（改行まで）だけを起動主張として読む。散文や周囲の `DES-001` 等のIDは
    宣言行の外にあるため構造上拾わない（200文字窓の推測走査を廃止し、偽陽性を根治する）。
    """
    claimed = set()
    for m in CLAIM_DECL_RE.finditer(text):
        for tok in AGENT_TOKEN_RE.findall(m.group(1)):
            claimed.add(tok)
    return claimed


TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
CELL_RE = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.DOTALL | re.IGNORECASE)


def _cells_unresolved(cells):
    cells = [c.strip().strip("*").strip() for c in cells]
    return any(c == "未是正" for c in cells) and any(c in DASH_CELL for c in cells)


def _has_unresolved_row(text):
    """集計表に『未是正』セルかつ『—』セルを併せ持つ行（追認）が在るか。

    markdown の `| ... |` 行と HTML の `<tr><td>...</td></tr>` 行の両方を判定する
    （設計レビューは HTML、構築レビューは markdown のため両対応が必須）。
    """
    # markdown 行
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("|") and _cells_unresolved(s.strip("|").split("|")):
            return True
    # HTML 行
    for tr in TR_RE.findall(text):
        cells = [re.sub(r"<[^>]+>", " ", c) for c in CELL_RE.findall(tr)]
        if _cells_unresolved(cells):
            return True
    return False


def find_unfounded_approvals(text):
    """ユーザー承認を主張するが decisions の裏付け参照を伴わない行を返す（純関数）。

    AskUserQuestion 無しに『ユーザー承認済み』と書き高指摘を合格化する捏造への対策。
    承認の正本はユーザーの明示回答（decisions.md/json）。主張行に decisions 参照が無ければ無根拠とみなす。
    """
    out = []
    for line in _strip_html(text).splitlines():
        if APPROVAL_CLAIM_RE.search(line) and not DECISIONS_REF_RE.search(line):
            out.append(line.strip())
    return out


# レビュー記録ファイル名 → reviewers.json のフェーズキー（観点網羅チェック用）
# reviewers.json のキーはノード名の小文字形に統一されているため、
# 「試験計画レビュー記録」を SYS/INT/UNIT_REVIEW の3ノードに分割マッチする。
# 順序注意: 「総合試験計画/結合試験計画/単体試験計画」を「試験計画」より先に照合する。
_PHASE_KEY_PATTERNS = (
    ("設計レビュー記録", "des_review"),
    ("構築レビュー記録", "bld_review"),
    ("総合試験計画レビュー記録", "sys_review"),
    ("結合試験計画レビュー記録", "int_review"),
    ("単体試験計画レビュー記録", "unit_review"),
)

# スキップの正直な申告（未インストール等）。期待観点の名前の近傍にこれがあれば網羅違反にしない。
_SKIP_NOTE_RE = r"(スキップ|未インストール|skip)"


def phase_key_for(path):
    """レビュー記録のパスから reviewers.json のフェーズキーを返す（該当なしは None）。"""
    base = str(path).replace("\\", "/").rsplit("/", 1)[-1]
    for pat, key in _PHASE_KEY_PATTERNS:
        if pat in base:
            return key
    return None


def load_reviewers_config(path="docs/conventions/reviewers.json"):
    """reviewers.json の {フェーズキー: [観点サブエージェント名,...]} を返す（無ければ None）。"""
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    out = {}
    for key, val in data.items():
        # メタキー（_note, _migration, _optional_disabled 等）は明示スキップ。
        # 意図しないメタ辞書がスキーマ拡張で subagents を持ってしまった場合の誤検出防止。
        if isinstance(key, str) and key.startswith("_"):
            continue
        if isinstance(val, dict) and isinstance(val.get("subagents"), list):
            out[key] = [s for s in val["subagents"] if isinstance(s, str) and s]
    return out or None


def check_reviewer_coverage(text, expected):
    """reviewers.json の期待観点が宣言行に揃っているか点検する（純関数）。

    集約役の早期終了により起動済み観点の結果が未統合のままレビュー記録が一部観点だけで
    合否を出す事象（記録自体は正直＝起動捏造ではないため既存チェックをすり抜ける）への対策。
    期待観点は (a)宣言行に載る か (b)名前の近傍にスキップ/未インストールの申告がある、の
    どちらかでなければ high（結果の破棄・観点の黙った欠落を機械検出する）。
    """
    out = []
    if not expected:
        return out
    claimed_bare = {b for b in (_bare_agent(c) for c in extract_claimed_agents(text)) if b}
    for name in expected:
        bare = _bare_agent(name)
        if not bare or bare in claimed_bare:
            continue
        if re.search(re.escape(bare) + r"[^\n]{0,60}" + _SKIP_NOTE_RE, text) or \
           re.search(_SKIP_NOTE_RE + r"[^\n]{0,60}" + re.escape(bare), text):
            continue
        out.append(("high", "review_coverage_missing",
                    f"観点欠落: reviewers.json が要求する {bare} が起動サブエージェント宣言に無く、"
                    f"スキップの申告も無い（起動済み結果の破棄・観点の黙った欠落を疑う。"
                    f"全観点の結果を統合するか、スキップ理由を明記すること）"))
    return out


def scan_text(text, invoked=None, expected=None):
    """1つの成果物テキストを点検し findings のリストを返す（純関数・テスト可能）。

    invoked: 実際に起動されたサブエージェント名の集合（None なら起動照合をスキップ）。
    expected: reviewers.json 由来の期待観点リスト（None なら網羅チェックをスキップ）。
    """
    out = []
    body = _strip_html(text)

    # ユーザー承認の主張は decisions の裏付け参照を要する（レビュー記録に限らず全成果物で点検）。
    for snippet in find_unfounded_approvals(text):
        out.append(("high", "review_unfounded_approval",
                    f"無根拠の承認主張: ユーザー承認/判断を主張するが decisions の裏付け参照が無い"
                    f"（AskUserQuestion を実施し decisions.md/json に明示回答を記録して参照すること）: 「{snippet}」"))

    if not REVIEW_MARKER_RE.search(body):
        return out  # 以降はレビュー記録のみ対象

    if PROVISIONAL_RE.search(body):
        out.append(("high", "review_provisional",
                    "暫定記録: サブエージェント結果が未確定のまま所見を記載している"
                    "（『結果が届き次第追記』『バックグラウンドで起動中』等）。全結果確定後に記録すること"))

    if invoked is not None:
        claimed = extract_claimed_agents(body)
        # 両側を素名（接頭辞除去）に正規化して対称に突合する。
        # 'ccd-flowkit:code-reviewer' と 'code-reviewer' を同一視。空名は除外する。
        # 限界: 接頭辞を捨てるため、別プラグインの同名が起動ログにあると見逃しうる（稀）。
        # 接頭辞差での誤検出（正規の起動を捏造と誤判定）を防ぐ方を優先する。
        invoked_bare = {b for b in (_bare_agent(i) for i in invoked) if b}
        claimed_bare = {b for b in (_bare_agent(c) for c in claimed) if b}
        not_invoked = sorted(claimed_bare - invoked_bare)
        if not_invoked:
            out.append(("high", "review_fabricated_agents",
                        f"起動捏造: レビュー記録が挙げる起動サブエージェントが実際の起動ログに無い: "
                        f"{', '.join(not_invoked)}（自己レビューを各エージェント所見として記録した疑い）"))

    if _has_unresolved_row(text):
        out.append(("high", "review_unresolved",
                    "追認: 指摘が『未是正』かつ『再判定 —』のまま残っている（再レビューが実質化していない）"))

    out.extend(check_reviewer_coverage(body, expected))

    return out


# 誤 cwd の無検査 green を防ぐ（幽霊 pass 対策。project_guard.py 参照）
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
    invoked = load_invoked_agents()
    reviewers = load_reviewers_config()
    findings = []
    targets = []
    for pat in ("docs/**/*.md", "docs/**/*.html"):
        targets.extend(glob.glob(pat, recursive=True))
    for p in sorted(set(targets)):
        if is_excluded(p):
            continue
        text = pathlib.Path(p).read_text(encoding="utf-8", errors="ignore")
        expected = (reviewers or {}).get(phase_key_for(p)) if reviewers else None
        for sev, kind, msg in scan_text(text, invoked, expected):
            findings.append((sev, kind, f"{p}: {msg}"))
    for sev, kind, msg in findings:
        print(f"[{sev}] {kind}: {msg}")
    highs = [f for f in findings if f[0] == "high"]
    note = "" if invoked is not None else "（起動ログ無し: 起動照合はスキップ）"
    print(f"\nsummary: high={sum(1 for f in findings if f[0]=='high')} "
          f"medium={sum(1 for f in findings if f[0]=='medium')} "
          f"low={sum(1 for f in findings if f[0]=='low')} {note}")
    sys.exit(1 if highs else 0)


if __name__ == "__main__":
    main()
