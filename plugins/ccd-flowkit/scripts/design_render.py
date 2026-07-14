#!/usr/bin/env python
"""design_render.py
構造化設計ソース（docs/design/design_spec.json）から人間向けビューを生成し、
設計書.html の生成ブロック（マーカー間）へ埋め込む（「ソースとビューの分離」の第1弾）。

- 機械の正本: design_spec.json（IF 仕様と決定参照。document-standards.md）
- 人間向けビュー: 設計書.html の <!-- GENERATED:DESIGN-SPEC --> ... <!-- /GENERATED:DESIGN-SPEC -->
  区間。決定参照は decisions.json の確定値へ**解決済み**の形で描画する（人間はトークンでなく
  実値を読む。機械は spec 側の参照を検査する＝両立）。

サブコマンド:
  render   設計書.html の生成ブロックを再生成して更新する（マーカーが無ければ末尾 </body> 前に挿入）
  check    再生成結果と現在のブロックを比較し、陳腐化していれば exit 1（DES_GATE で使用）

使い方: python scripts/design_render.py render|check
LLM 判断は使わない。生成ブロックは手で編集しない（render で上書きされる）。
"""
import html
import json
import pathlib
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 誤 cwd での無検査書込を防ぐ（project_guard.py 参照）
try:
    from project_guard import ensure_project_root
except ImportError:  # 配置差異でも fail-open にしない最小フォールバック
    def ensure_project_root(root=None):
        check = pathlib.Path(root) if root is not None else pathlib.Path('.')
        if not (check / 'docs').is_dir():
            print(f'エラー: {check} に docs/ が無い（プロジェクトルート外での実行）', file=sys.stderr)
            sys.exit(2)

SPEC_FILE = pathlib.Path("docs/design/design_spec.json")
LEDGER_FILE = pathlib.Path("docs/design/decisions.json")
DOC_FILE = pathlib.Path("docs/design/設計書.html")

MARK_BEGIN = "<!-- GENERATED:DESIGN-SPEC -->"
MARK_END = "<!-- /GENERATED:DESIGN-SPEC -->"


def load_json(path):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def resolve_decision(ledger, dec_id):
    """決定IDを台帳から (value, status) に解決する。無ければ (None, None)。"""
    if not isinstance(ledger, dict):
        return None, None
    for d in ledger.get("decisions", []) or []:
        if isinstance(d, dict) and d.get("id") == dec_id:
            return d.get("value"), d.get("status")
    return None, None


def render_fragment(spec, ledger):
    """spec＋台帳から生成ブロックの中身（HTML）を作る（純関数・決定論）。

    出力はキー順ソートで安定化する（check の比較を成立させるため）。
    生成ブロックは設計単位（DES）でなく IF 仕様一覧のメタ節のため、data-trace-skip="true" を
    付けて trace_scan の「data-trace-id 必須」対象から外す（付与しないと presence high と
    衝突し、生成ブロックそのものがゲートを恒久ブロックしてしまうため）。
    """
    lines = [MARK_BEGIN,
             "<section id=\"design-spec-generated\" data-trace-skip=\"true\">",
             "<h2>IF仕様一覧（design_spec.json から生成。手編集禁止＝design_render.py render で更新）</h2>",
             "<table border=\"1\">",
             "<tr><th>DES</th><th>関数</th><th>引数</th><th>戻り値</th><th>送出</th><th>関連決定（解決値）</th></tr>"]
    components = (spec or {}).get("components", {})
    if not isinstance(components, dict):
        components = {}
    for des_id in sorted(components):
        comp = components[des_id]
        # 形崩れ（dict でない）は空扱いで描画を続ける（スキーマ違反の検出・修正指示は
        # design_spec_scan.check_schema の責務。renderer がクラッシュすると high 指摘が届かない）
        comp = comp if isinstance(comp, dict) else {}
        decs = []
        for dec_id in comp.get("decisions", []) or []:
            value, status = resolve_decision(ledger, dec_id)
            shown = f"{dec_id}={value}" if value is not None else f"{dec_id}=?(台帳に無い)"
            if status == "provisional":
                shown += "(暫定)"
            decs.append(shown)
        dec_cell = html.escape(", ".join(decs)) or "—"
        funcs = comp.get("functions", []) or []
        if not funcs:
            lines.append(f"<tr><td>{html.escape(des_id)}</td><td>—</td><td>—</td><td>—</td>"
                         f"<td>—</td><td>{dec_cell}</td></tr>")
        for fn in funcs:
            if not isinstance(fn, dict):
                continue
            name = html.escape(str(fn.get("name", "?")))
            params = html.escape(", ".join(fn.get("params", []) or []))
            returns = html.escape(str(fn.get("returns", "") or "—"))
            raises = html.escape(", ".join(fn.get("raises", []) or []) or "—")
            lines.append(f"<tr><td>{html.escape(des_id)}</td><td>{name}</td><td>{params}</td>"
                         f"<td>{returns}</td><td>{raises}</td><td>{dec_cell}</td></tr>")
    lines += ["</table>", "</section>", MARK_END]
    return "\n".join(lines)


def _find_markers(doc_text):
    """マーカー位置 (b, e) を返す。両方無ければ (None, None)、片方だけ・逆順は ValueError。

    孤立マーカーを「無し」と誤認すると、render がブロックを追加挿入した後に旧 BEGIN と
    新 END が誤ペアリングされ、間の**手書き散文が上書き消去**されてしまう。
    壊れたマーカーは自動修復せず、人に修復させる（fail-closed）。
    """
    b = doc_text.find(MARK_BEGIN)
    e = doc_text.find(MARK_END)
    if b == -1 and e == -1:
        return None, None
    if b == -1 or e == -1 or e < b:
        raise ValueError("生成マーカーが壊れている（BEGIN/END の孤立または逆順）")
    return b, e


def replace_block(doc_text, fragment):
    """設計書テキストの生成ブロックを fragment に置換する（純関数）。

    マーカーが無い場合は </body> の直前（無ければ末尾）に挿入する。
    マーカーが壊れている（孤立・逆順）場合は ValueError（呼び手がエラー終了する）。
    戻り値: (新テキスト, 置換できたか bool)
    """
    b, e = _find_markers(doc_text)
    if b is not None:
        return doc_text[:b] + fragment + doc_text[e + len(MARK_END):], True
    lower = doc_text.lower()
    pos = lower.rfind("</body>")
    if pos == -1:
        return doc_text.rstrip() + "\n\n" + fragment + "\n", False
    return doc_text[:pos] + fragment + "\n" + doc_text[pos:], False


def current_block(doc_text):
    """設計書テキスト中の現在の生成ブロック（マーカー込み）を返す。

    無ければ None、マーカーが壊れていれば ValueError（呼び手が high として扱う）。
    """
    b, e = _find_markers(doc_text)
    if b is None:
        return None
    return doc_text[b:e + len(MARK_END)]


def cmd_render():
    spec = load_json(SPEC_FILE)
    if spec is None:
        print(f"エラー: {SPEC_FILE} が無いか JSON として読めない", file=sys.stderr)
        return 1
    fragment = render_fragment(spec, load_json(LEDGER_FILE))
    doc = DOC_FILE.read_text(encoding="utf-8") if DOC_FILE.exists() else "<html><body>\n</body></html>\n"
    try:
        new, replaced = replace_block(doc, fragment)
    except ValueError as e:
        print(f"エラー: {e}。設計書.html のマーカー行（{MARK_BEGIN} / {MARK_END}）を手で修復して"
              f"から再実行してください（自動修復は手書き散文を壊す恐れがあるため行わない）",
              file=sys.stderr)
        return 1
    DOC_FILE.parent.mkdir(parents=True, exist_ok=True)
    DOC_FILE.write_text(new, encoding="utf-8")
    print(json.dumps({"status": "rendered", "replaced_existing": replaced}, ensure_ascii=False))
    return 0


def cmd_check():
    spec = load_json(SPEC_FILE)
    if spec is None:
        # spec 未採用のプロジェクトはチェック対象外（採否は design_spec_scan が見る）
        print(json.dumps({"status": "no_spec"}, ensure_ascii=False))
        return 0
    fragment = render_fragment(spec, load_json(LEDGER_FILE))
    doc = DOC_FILE.read_text(encoding="utf-8") if DOC_FILE.exists() else ""
    try:
        cur = current_block(doc)
    except ValueError:
        print("[high] design_view_markers_broken: 生成マーカーが壊れている（BEGIN/END の孤立・逆順）。"
              "マーカー行を手で修復してから design_render.py render を実行すること")
        return 1
    if cur is None:
        print("[high] design_view_missing: 設計書.html に生成ブロックが無い。"
              "design_render.py render を実行して IF仕様一覧を埋め込むこと")
        return 1
    if cur != fragment:
        print("[high] design_view_stale: 設計書.html の生成ブロックが design_spec.json/decisions.json と"
              "食い違う（陳腐化）。design_render.py render で再生成すること")
        return 1
    print(json.dumps({"status": "fresh"}, ensure_ascii=False))
    return 0


def main():
    ensure_project_root()
    if len(sys.argv) < 2 or sys.argv[1] not in ("render", "check"):
        print(__doc__)
        return 1
    return cmd_render() if sys.argv[1] == "render" else cmd_check()


if __name__ == "__main__":
    sys.exit(main())
