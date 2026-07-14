#!/usr/bin/env python
"""design_spec_scan.py
構造化設計ソース docs/design/design_spec.json を決定論で検査する。フェーズゲートで実行。

design_spec.json（正本。document-standards.md）:
  {"version":1, "components": {
     "DES-002": {"decisions": ["UD-05"],
                 "functions": [{"name":"load_tasks","params":["path"],
                                "returns":"tuple[list,int]","raises":["SystemExit"]}]}}}

検査（mode=design。DES_GATE で gate-runner が実行）:
  - スキーマ不備（components が dict でない・functions の name/params 型崩れ）→ high
  - 決定参照の dangling（decisions.json に無い id）／provisional 参照 → high（ontology と同基準）
  - 生成ビューの陳腐化（design_render.py check 相当）→ high
  - design_spec.json 自体が無い → medium（未採用プロジェクトを壊さない移行措置）

検査（mode=build。BLD_GATE で consistency-checker が実行。design 検査も全て含む）:
  - spec の関数が src/**/*.py に実在しない → high（設計の IF が未実装）
  - 実在するが引数名列が不一致（self/cls 除外・位置引数名の完全一致）→ high（design_impl IF 乖離の決定論化）
  - src の公開モジュール関数（_ 始まりと main 以外）が spec に未宣言 → medium（設計外の副産物候補を
    機械検出する。実装だけに存在する隠れた公開 API の見落とし対策）

high が1件でもあれば exit 1。LLM 判断は使わない。
使い方: python scripts/design_spec_scan.py design|build
"""
import ast
import glob
import json
import pathlib
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SPEC_FILE = pathlib.Path("docs/design/design_spec.json")
LEDGER_FILE = pathlib.Path("docs/design/decisions.json")
SRC_GLOB = "src/**/*.py"

# spec に宣言しなくてよい公開名（エントリポイント等）
UNDECLARED_ALLOW = {"main"}


def load_json(path):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        return "invalid"


def check_schema(spec):
    """spec の形を検査し findings を返す（純関数）。"""
    out = []
    if not isinstance(spec, dict) or not isinstance(spec.get("components"), dict):
        return [("high", "spec_schema", "design_spec.json: components(dict) が無い/型不正")]
    for des_id, comp in spec["components"].items():
        if not isinstance(comp, dict):
            out.append(("high", "spec_schema", f"{des_id}: コンポーネント定義が dict でない"))
            continue
        for fn in comp.get("functions", []) or []:
            if not isinstance(fn, dict) or not isinstance(fn.get("name"), str) or not fn.get("name"):
                out.append(("high", "spec_schema", f"{des_id}: functions に name の無い要素がある"))
                continue
            params = fn.get("params", [])
            if not isinstance(params, list) or any(not isinstance(p, str) for p in params):
                out.append(("high", "spec_schema",
                            f"{des_id}.{fn.get('name')}: params は文字列の配列で書く"))
    return out


def check_decisions(spec, ledger):
    """spec の決定参照を台帳と突合し findings を返す（純関数）。"""
    out = []
    if not isinstance(spec, dict):
        return out
    by_id = {}
    if isinstance(ledger, dict):
        for d in ledger.get("decisions", []) or []:
            if isinstance(d, dict) and d.get("id"):
                by_id[d["id"]] = d
    for des_id, comp in (spec.get("components") or {}).items():
        if not isinstance(comp, dict):
            continue
        for dec_id in comp.get("decisions", []) or []:
            d = by_id.get(dec_id)
            if d is None:
                out.append(("high", "spec_decision_dangling",
                            f"{des_id}: 決定参照 {dec_id} が decisions.json に無い"))
            elif d.get("status") != "confirmed":
                out.append(("high", "spec_decision_provisional",
                            f"{des_id}: 決定参照 {dec_id} が未確定(provisional)のまま設計に固定されている"))
    return out


def collect_src_functions(src_texts):
    """{関数名: [引数名リスト, ...]} を src テキスト群から収集する（純関数・ast）。

    引数名列は **位置専用(posonlyargs)＋通常(args)＋キーワード専用(kwonlyargs)** をこの順で
    連結する（`def f(a, /, b, *, c)` → [a, b, c]。args.args だけ見ると posonly/kwonly を持つ
    正しい実装を signature 不一致と誤検出するため）。*args/**kwargs は
    spec 側で表現しないため無視する（名前列の一致で IF を判定）。
    モジュール関数とクラスメソッドの両方を集める。メソッドは self/cls を除外した候補も持つ。
    同名多重定義は全候補を保持し、いずれか一致で OK とする（判定側）。
    """
    funcs = {}

    def _params_of(node):
        a = node.args
        return ([x.arg for x in getattr(a, "posonlyargs", [])]
                + [x.arg for x in a.args]
                + [x.arg for x in a.kwonlyargs])

    for text in src_texts:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # 直上が ClassDef かは walk では分からないため、両解釈の引数列を候補に入れる
                # （self 付き/なし。誤検出より見逃し防止を優先しつつ、名前一致＋どちらかの引数列一致で通す）
                all_names = _params_of(node)
                funcs.setdefault(node.name, []).append(all_names)
                if all_names and all_names[0] in ("self", "cls"):
                    funcs.setdefault(node.name, []).append(all_names[1:])
    return funcs


def collect_module_public_functions(src_texts):
    """モジュール直下の公開関数名の集合を返す（純関数。設計外の副産物検出用）。"""
    names = set()
    for text in src_texts:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_") and node.name not in UNDECLARED_ALLOW:
                    names.add(node.name)
    return names


def check_impl(spec, src_texts):
    """spec の IF と実装の突合 findings を返す（純関数）。"""
    out = []
    if not isinstance(spec, dict):
        return out
    funcs = collect_src_functions(src_texts)
    declared = set()
    for des_id, comp in (spec.get("components") or {}).items():
        if not isinstance(comp, dict):
            continue
        for fn in comp.get("functions", []) or []:
            if not isinstance(fn, dict) or not fn.get("name"):
                continue
            name = fn["name"]
            declared.add(name)
            params = fn.get("params", []) or []
            candidates = funcs.get(name)
            if candidates is None:
                out.append(("high", "spec_impl_missing",
                            f"{des_id}: 設計の関数 {name}({', '.join(params)}) が実装に存在しない"))
            elif not any(params == c for c in candidates):
                got = " / ".join("(" + ", ".join(c) + ")" for c in candidates[:3])
                out.append(("high", "spec_impl_signature",
                            f"{des_id}: {name} の引数が設計({', '.join(params)})と実装{got}で不一致"))
    for name in sorted(collect_module_public_functions(src_texts) - declared):
        out.append(("medium", "spec_undeclared_public",
                    f"実装の公開関数 {name} が design_spec.json に未宣言（設計外の副産物の疑い。"
                    f"設計に反映するか、内部関数なら _ 始まりに）"))
    return out


def _read_src_texts():
    """src/**/*.py のテキストを読む。実体（resolve 後）が src/ 配下のものだけを対象にする
    （Windows junction 等でリポジトリ外へ走査が逃げ、外部の同名関数で「実在」と誤判定される
    ゲート整合性破壊を封じる。trace_scan と同じ対策）。"""
    texts = []
    try:
        src_root = pathlib.Path("src").resolve(strict=True)
    except OSError:
        return texts
    for p in glob.glob(SRC_GLOB, recursive=True):
        path = pathlib.Path(p)
        if path.is_symlink() or not path.is_file():
            continue
        try:
            path.resolve(strict=True).relative_to(src_root)
        except (OSError, ValueError):
            continue
        try:
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return texts


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
    # mode は必須。省略時に design へ暗黙フォールバックすると、BLD_GATE で引数を落として実行した
    # 場合に IF 突合が無音でスキップされる fail-open になるため。
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("design", "build"):
        print("使い方: design_spec_scan.py design|build（mode は必須）", file=sys.stderr)
        return 2
    findings = []
    spec = load_json(SPEC_FILE)
    if spec is None:
        findings.append(("medium", "spec_missing",
                         "docs/design/design_spec.json が無い（IF・決定参照の構造化ソース。"
                         "document-standards.md に従い designer が作成する）"))
    elif spec == "invalid":
        findings.append(("high", "spec_schema", "design_spec.json が JSON として読めない"))
    else:
        findings += check_schema(spec)
        findings += check_decisions(spec, load_json(LEDGER_FILE))
        # 生成ビューの鮮度（design_render.check と同判定）。形崩れ spec 等での例外は
        # クラッシュさせず finding に変換する（トレースバックで検出済み high が握り潰されるのを防ぐ）。
        try:
            import design_render as dr
            fragment = dr.render_fragment(spec, dr.load_json(dr.LEDGER_FILE))
            doc = dr.DOC_FILE.read_text(encoding="utf-8") if dr.DOC_FILE.exists() else ""
            try:
                cur = dr.current_block(doc)
            except ValueError:
                cur = "BROKEN"
                findings.append(("high", "design_view_markers_broken",
                                 "設計書.html の生成マーカーが壊れている（BEGIN/END の孤立・逆順）。"
                                 "マーカー行を手で修復してから design_render.py render を実行"))
            if cur is None:
                findings.append(("high", "design_view_missing",
                                 "設計書.html に生成ブロックが無い（design_render.py render を実行）"))
            elif cur != "BROKEN" and cur != fragment:
                findings.append(("high", "design_view_stale",
                                 "設計書.html の生成ブロックが陳腐化（design_render.py render で再生成）"))
        except ImportError:
            findings.append(("medium", "renderer_unavailable", "design_render.py を import できない"))
        except Exception as e:  # noqa: BLE001 - クラッシュより finding 化を優先
            findings.append(("high", "design_view_check_failed",
                             f"生成ビューの鮮度検査が失敗（{type(e).__name__}: {e}）。spec の形を確認"))
        if mode == "build" and isinstance(spec, dict):
            findings += check_impl(spec, _read_src_texts())
    for sev, kind, msg in findings:
        print(f"[{sev}] {kind}: {msg}")
    highs = [f for f in findings if f[0] == "high"]
    print(f"\nsummary: high={len(highs)} "
          f"medium={sum(1 for f in findings if f[0]=='medium')}")
    return 1 if highs else 0


if __name__ == "__main__":
    sys.exit(main())
