#!/usr/bin/env python
"""template_residue_scan.py
成果物テンプレート（references/templates/）由来のプレースホルダが最終成果物に残置されて
いないかを決定論的に点検する。フェーズゲートで実行。

テンプレをコピー→穴埋めしたが `<FILL:>` `<OPT:>` `<GEN:>` `<FILL-COUNT:>` の
削除/置換忘れが残ると、書式起因の high 指摘・レビューでの手戻りを招く。
本スキャナはそれを機械的に検出する（LLM 判断を使わない）。

検査対象:
- `<FILL: ...>` — LLM が中身を埋める場所。残置は high（穴埋め忘れ）
- `<FILL-COUNT: ...>` — N 個繰り返す場所。残置は high（同上）
- `<OPT: ...>` — 任意追加セクション。残置は high（不要なら節ごと削除、必要なら埋める）
- `<GEN: ...>` — 決定論部分の機械生成場所。残置は high（未実行）

以下は残置してよい（KEEP マーカーはテンプレの必須マーカーで削除禁止）:
- `<!-- KEEP: ... -->` — スキャナが必須マーカーとして検査対象にする
- `<!-- template: <name> v<version> -->` — テンプレバージョン記録

コードブロック内・HTML コメント内は誤検出回避のため対象外（テンプレ規約の説明や snippets 中の
例示を pass にする）。

high が1件でもあれば exit 1。使い方: python scripts/template_residue_scan.py [path...]

デフォルト走査（引数なし）は `docs/**/*.md` と `docs/**/*.html` を対象とし、以下を除外する
（説明・参照用ドキュメントでの偽陽性対策）:
- `docs/conventions/**` — 規約集は「プレースホルダの書き方」を例示するため `<FILL:>` 等が正当
- `docs/references/**` — 参照ドキュメントも同様
- `docs/**/templates/**` — テンプレート本体を配布した場合の説明文が該当

明示パス指定時は除外を適用せず（gate 側で対象を絞る用途では明示指定を推奨）。
"""
import glob
import pathlib
import re
import sys

# Windows コンソール(cp932)対策。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


# 残置検出パターン（`< FILL:` は自然文で頻出しないため誤検出しにくい）
# `<!--` で始まる HTML コメント形式（`<!-- FILL: -->`）も検出対象。
RESIDUE_RE = re.compile(
    r"<!?-?-?\s*(FILL|FILL-COUNT|OPT|GEN)\s*[:>]", re.IGNORECASE)

# コードブロック・HTML コメントを除去する（誤検出回避）。
# ただし `<!-- KEEP: -->` `<!-- template: -->` は残置してよいマーカーで、削除しても影響なし。
_CODE_BLOCK_RE = re.compile(
    r"```[\s\S]*?```"          # Markdown fenced code
    r"|~~~[\s\S]*?~~~"           # Markdown fenced code (tilde)
    r"|<pre\b[^>]*>[\s\S]*?</pre>"   # HTML pre
    r"|<code\b[^>]*>[\s\S]*?</code>",  # HTML code
    re.IGNORECASE)
_HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")


def _strip_code_and_comments(text):
    """コードブロック・HTML コメント除去（誤検出回避）。

    HTML コメント除去も含めるため、`<!-- FILL: -->` 形式のプレースホルダも間接的に除去される。
    残置検出を厳格にしたい場合は `<FILL: ...>` プレーンな形式を使うこと（review_record.md 準拠）。
    """
    body = _CODE_BLOCK_RE.sub(" ", text)
    body = _HTML_COMMENT_RE.sub(" ", body)
    return body


def scan_text(text):
    """1つの成果物テキストを点検し findings のリストを返す（純関数・テスト可能）。

    戻り値: [(severity, kind, message), ...] のリスト。
    """
    out = []
    body = _strip_code_and_comments(text)
    matches = RESIDUE_RE.findall(body)
    if not matches:
        return out
    # 種別ごとに件数集計（メッセージを短く保つ）
    counts = {}
    for kind in matches:
        k = kind.upper()
        counts[k] = counts.get(k, 0) + 1
    for kind, n in sorted(counts.items()):
        out.append(("high", "template_residue",
                    f"テンプレート由来の未処理プレースホルダ <{kind}: ...> が {n} 件残置"))
    return out


# 誤 cwd の無検査 green を防ぐ（幽霊 pass 対策）
try:
    from project_guard import ensure_project_root
except ImportError:
    def ensure_project_root():
        if not pathlib.Path("docs").is_dir():
            print("エラー: docs/ が無い（プロジェクトルート外での実行）", file=sys.stderr)
            sys.exit(2)


# 走査除外: 説明・参照用ドキュメントは成果物ではないため対象外にする（偽陽性対策）。
# docs/conventions/ は規約集で、規約自体が「プレースホルダの書き方」を例示するため
# `<FILL:>` `<OPT:>` 等が正当に登場する（プレースホルダの残置ではない）。
# docs/**/templates/ 配下も同様にテンプレ本体を配布した場合の説明文が該当する。
# 明示パス指定（gate 側や CI で対象を絞る用途）ではこの除外は適用せず、指定通りに走査する。
_EXCLUDED_PATH_SEGMENTS = ("docs/conventions/", "docs\\conventions\\",
                            "docs/references/", "docs\\references\\",
                            "/templates/", "\\templates\\")


def _is_excluded_from_default_scan(path):
    """デフォルト走査（引数なし実行）で除外すべきパスなら True を返す（純関数）。

    明示指定された場合は呼ばれない（`docs/conventions/*.md` を指定して検査したければ
    そのまま検査対象になる）。
    """
    p = str(path).replace("\\", "/").lower()
    for seg in _EXCLUDED_PATH_SEGMENTS:
        s = seg.replace("\\", "/").lower()
        if s in p:
            return True
    return False


def _iter_targets(paths):
    """走査対象ファイル一覧を返す。paths が空なら docs/**/*.{md,html} を対象にする。

    デフォルト走査時のみ `docs/conventions/` `docs/references/` `**/templates/` を除外する
    （明示パス指定時は除外しない）。REQ_GATE が説明文中のマーカー引用を誤検出しないための対策。
    """
    if paths:
        for p in paths:
            pp = pathlib.Path(p)
            if pp.is_file():
                yield str(pp)
            elif pp.is_dir():
                yield from glob.iglob(f"{pp}/**/*.md", recursive=True)
                yield from glob.iglob(f"{pp}/**/*.html", recursive=True)
        return
    for p in glob.iglob("docs/**/*.md", recursive=True):
        if not _is_excluded_from_default_scan(p):
            yield p
    for p in glob.iglob("docs/**/*.html", recursive=True):
        if not _is_excluded_from_default_scan(p):
            yield p


def main(argv=None):
    ensure_project_root()
    argv = argv if argv is not None else sys.argv[1:]
    findings = []
    for p in _iter_targets(argv):
        try:
            text = pathlib.Path(p).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for sev, kind, msg in scan_text(text):
            findings.append((sev, kind, f"{p}: {msg}"))

    order = {"high": 0, "medium": 1, "low": 2}
    for sev, kind, msg in sorted(findings, key=lambda x: order.get(x[0], 9)):
        print(f"[{sev}] {kind}: {msg}")

    highs = sum(1 for f in findings if f[0] == "high")
    print(f"\nsummary: high={highs} "
          f"medium={sum(1 for f in findings if f[0]=='medium')} "
          f"low={sum(1 for f in findings if f[0]=='low')}")
    return 1 if highs else 0


if __name__ == "__main__":
    sys.exit(main())
