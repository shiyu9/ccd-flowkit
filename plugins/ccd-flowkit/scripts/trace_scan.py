#!/usr/bin/env python3
"""trace_scan.py
トレーサビリティの決定論チェック。フェーズゲートで実行し、未採番・書式・重複・
参照整合・双方向・カバレッジ・設計間依存・実装トレース(DES→code)・試験トレース(TEST→上流)を点検する。
高(severity=high)が1件でもあれば終了コード1。LLM判断は使わない。

フェーズ整合の原則: 下流成果物へのカバレッジ検査は**下流成果物が存在する時のみ**行う
（REQ→設計は設計書がある時／DES→code は src がある時／試験カバレッジは各計画がある時）。
gate-runner は exit code のみで判定するため、フェーズ的に未着手の下流を high にすると
恒久ループになる（フェーズ整合を保つための予防的な設計）。

実装トレース: src/ の `# DES-NNN [REQ-xxx]` マーカーを収穫し、(a)実在しない DES を指すマーカー=dangling、
(b)コードがある時に未実装の設計(マーカー無しの DES)=coverage を high とする。REQ→実装は REQ→DES→code で
辿る（実装リンクは設計を経由）。マーカー漏れ等の公開シンボル網羅(意味検査)は consistency-checker/LSP に委ねる。

試験トレース(TEST→上流): 各試験計画の試験項目直下の正準トレース行 `**トレース**: <上流ID>` を収穫する。
レベルはファイル名で判定（総合=REQ網羅 / 結合・単体=DES網羅）。(a)実在しない REQ/DES を指す=dangling(high)、
(b)レベル相応の上流型を持たない試験=presence(high)、(c)総合試験計画がある時に総合試験で参照されない REQ=
req_coverage(high)、(d)単体試験計画がある時に単体/結合のどちらでも参照されない DES=des_coverage(high)。
"""
import re, json, glob, pathlib, sys
from html.parser import HTMLParser

# Windows コンソール(cp932)で日本語を print してもクラッシュしないよう stdout/stderr を UTF-8 化。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = pathlib.Path(".")
findings = []
def add(sev, kind, msg): findings.append((sev, kind, msg))

ID_FMT = {
    "REQ": re.compile(r"^REQ-\d{3}$"),
    "DES": re.compile(r"^DES-\d{3}$"),
    "TEST": re.compile(r"^TEST-\d{3}$"),
}

# markdown 見出し行（`#`〜`######`）。試験IDの「定義箇所」は見出しに置く規約（trace-conventions）。
# 表・散文・修正履歴・レビュー記録内の TEST-ID 参照を定義の重複と誤計上しないため、定義の収穫は
# 見出し行に限定する（参照を重複計上する偽陽性を防ぐため）。
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")

# ---- 文書パーサ（差し替え可能な部品）----
def parse_requirements():
    """要件: '**REQ-001**' 形式の箇条書きを1単位とする（要件定義書が正本）。

    docs/requirements/ には総合試験計画も置かれ REQ を参照するため、'試験計画'/'試験結果' を
    名に含むファイルは除外する（要件の定義元でなく参照側なので REQ を二重計上しない）。
    '要望書'（要件定義フェーズの入力）も除外する。要望書は REQ の定義元でなく、要件定義書が正本
    （要望書に REQ 風の記述があっても二重計上しないため）。
    """
    ids = []
    for p in glob.glob("docs/requirements/*.md"):
        name = pathlib.Path(p).name
        if "試験計画" in name or "試験結果" in name or "要望書" in name:
            continue
        for line in pathlib.Path(p).read_text(encoding="utf-8", errors="ignore").splitlines():
            for m in re.findall(r"\*\*(REQ-\w+)\*\*", line):
                ids.append((m, p))
    return ids

# トレース対象外のメタセクション（エビデンス一覧・検討アプローチ・レビュー記録など）。
# data-trace-skip="true" 属性、または既知のメタ id を持つ <section> は data-trace-id 必須から除外する。
META_SECTION_IDS = {"evidence-list", "approach-summary", "review-record"}

class SectionScan(HTMLParser):
    """設計HTML: <section> ごとに data-trace-id / data-trace-req / data-trace-deps を点検"""
    def __init__(self): super().__init__(); self.sections=[]; self.no_id=0
    def handle_starttag(self, tag, attrs):
        if tag == "section":
            d = dict(attrs)
            tid = d.get("data-trace-id")
            req = d.get("data-trace-req","")
            deps = d.get("data-trace-deps","")
            if tid:
                self.sections.append((tid, req, deps))
            elif d.get("data-trace-skip") == "true" or d.get("id") in META_SECTION_IDS:
                pass  # メタセクションはトレース対象外
            else:
                self.no_id += 1

def parse_design():
    secs=[]; missing=0
    for p in glob.glob("docs/design/*.html"):
        s=SectionScan(); s.feed(pathlib.Path(p).read_text(encoding="utf-8", errors="ignore"))
        secs += s.sections; missing += s.no_id
        if s.no_id:
            add("high","presence",f"{p}: data-trace-id の無い <section> が {s.no_id} 個")
    return secs

def _test_plan_files(token="試験計画"):
    """試験計画ファイル一覧を返す（**レビュー記録は除外**）。

    `結合試験計画レビュー記録.md` 等は '試験計画' を名に含むが試験の定義元でなく、レビュー記録内の
    TEST-ID 見出し（指摘）を試験の定義と誤計上すると重複IDの偽陽性になる（レビュー記録を別ファイル化した
    場合の副作用への対策）。
    """
    return [p for p in glob.glob(f"docs/**/*{token}*.md", recursive=True)
            if "レビュー記録" not in pathlib.Path(p).name]


def parse_tests():
    """試験: 試験計画の見出し行にある TEST-NNN（数字ID）を1単位とする。

    TEST-NNN/TEST-XXX のような記録様式テンプレートのプレースホルダ（数字でない）は拾わない。
    定義箇所は markdown 見出し行に限定する（表・散文・修正履歴・レビュー記録内の TEST-ID 参照を
    定義の重複と誤計上する偽陽性を防ぐ）。レビュー記録ファイル自体も除外する。
    """
    ids=[]
    for p in _test_plan_files():
        for line in pathlib.Path(p).read_text(encoding="utf-8", errors="ignore").splitlines():
            if not HEADING_RE.match(line):
                continue
            for m in re.findall(r"\b(TEST-\d+)\b", line):
                ids.append((m,p))
    return ids

# ---- 実装トレース（DES→code）----
# src/ の公開シンボル直前の独立行コメントに書く設計IDマーカー（例 `# DES-001 [REQ-001]`）。
# 行頭（空白後）のコメント限定で拾い、文字列リテラル中の "DES-001" や行末注記は収穫しない。
# DES は採番と同じ3桁固定（DES-NNN）。MVP は # // ; -- 様式（Python 等）。
# 限界（trace-conventions に明記）: 行ベースで字句解析しないため docstring/サンプルコード内の
# 行頭コメント風行は誤収穫しうる。行末注記コメントは収穫しない（マーカーは直前の独立行に書く規約）。
SRC_MARKER_RE = re.compile(r"^\s*(?:#|//|;|--)\s*(DES-\d{3})\b")
# 走査対象のソース拡張子（バイナリ/データ/LICENSE 等で has_code が誤って立つのを防ぐ）。
SOURCE_EXTS = {".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".go", ".java", ".rb",
               ".rs", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".cs", ".php", ".sh",
               ".bash", ".kt", ".kts", ".swift", ".scala", ".pl", ".lua", ".r", ".jl",
               ".dart", ".ex", ".exs"}

def _resolved_within(path, root):
    """path の実体（resolve 後）が root 配下なら True（純関数・テスト可能）。

    `is_symlink()` は Windows のジャンクション/reparse point を捕捉できないため、解決後の実体パスが
    src/ ツリー内に収まることを確認して、リポジトリ外への走査エスケープを封じる。
    root が None なら制限しない。解決不能・root 外は False。
    """
    try:
        real = path.resolve(strict=True)
    except OSError:
        return False
    if root is None:
        return True
    try:
        real.relative_to(root)
        return True
    except ValueError:
        return False


def parse_src():
    """src/ のトレースマーカーを収穫する（cwd 相対）。

    戻り値 (marked_des, marker_locs, has_code):
      marked_des: コードが参照する DES-ID 集合 / marker_locs: {DES: ["file:line",...]} /
      has_code: src/ に非空のソースファイル（SOURCE_EXTS）が1つ以上あるか
                （カバレッジ検査のフェーズゲート。コード未着手＝設計フェーズ等では誤検出しない）。
    symlink・非ソース拡張子・src/ 外を指す reparse point は対象外
    （リポジトリ外参照やバイナリでの誤判定・走査エスケープを避ける）。
    """
    try:
        src_root = pathlib.Path("src").resolve(strict=False)
    except Exception:
        src_root = None
    marked_des = set(); marker_locs = {}; has_code = False
    for p in glob.glob("src/**/*", recursive=True):
        path = pathlib.Path(p)
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix.lower() not in SOURCE_EXTS:
            continue
        # 実体が src/ 配下にあることを保証（Windows junction 等 reparse point の追従を封じる）。
        if not _resolved_within(path, src_root):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if text.strip():
            has_code = True
        for ln, line in enumerate(text.splitlines(), 1):
            m = SRC_MARKER_RE.match(line)
            if m:
                did = m.group(1)
                marked_des.add(did)
                marker_locs.setdefault(did, []).append(f"{p}:{ln}")
    return marked_des, marker_locs, has_code


def check_code(marked_des, marker_locs, des_set, has_code):
    """src/ マーカーと設計集合を突合し findings を返す（純関数・テスト可能）。

    dangling: コードのマーカーが設計に無い DES を指す → high。
    coverage: src にコードがある時のみ、各 DES が未実装（マーカー無し）→ high。
    has_code が False（コード未着手）なら coverage は出さない（設計ゲートの誤検出防止）。
    """
    out = []
    for did in sorted(marked_des - des_set):
        out.append(("high", "code_dangling",
                    f"src のトレースマーカー {did} が設計に存在しない（dangling）: "
                    f"{', '.join(marker_locs.get(did, []))}"))
    if has_code:
        for did in sorted(des_set - marked_des):
            out.append(("high", "code_coverage",
                        f"未実装: 設計 {did} に対応するコードが無い（src にトレースマーカー # {did} が無い）"))
    return out


def check_test_coverage(led):
    """試験結果がある時に REQ.test が空ならば medium で警告する（純関数・テスト可能）。

    呼び出し元が試験結果ファイルの存在を確認してから呼ぶ。
    """
    out = []
    if not led:
        return out
    for key in sorted(led):
        if not key.startswith("REQ-"):
            continue
        node = led[key]
        if isinstance(node, dict) and not node.get("test"):
            out.append(("medium", "test_traceability",
                        f"{key} の test 列が空（試験との紐づけが未登録）"))
    return out


def _fill_phase_active():
    """design/code 空チェックを発火してよいフェーズか（build/test。不明なら True＝従来動作）。

    デルタフロー（変更モード）では REQ_GATE/DES_GATE の時点で src に前スプリントのコードが
    存在するため、src の有無だけでゲートすると充填責務（BLD_GATE での trace_fill.py 実行）より
    前のゲートで medium ノイズが出続ける。state の current ノードの
    フェーズが build/test の時のみ発火する。state が読めない（スタンドアロン実行・テスト等）
    場合は従来どおり発火する。
    """
    try:
        import manage_flow_state as _mfs
        state = json.loads(pathlib.Path("state/_flow_state.json").read_text(encoding="utf-8"))
        return _mfs.NODE_PHASE.get(state.get("current")) in ("build", "test")
    except Exception:
        return True


def check_design_code_coverage(led):
    """REQ の design / code 列が空ならば medium で警告する（純関数・テスト可能）。

    充填を AI 依存にすると個体差により全 REQ の design:[] / code:[] が空のまま完走する
    ケースへの対策。充填は trace_fill.py（機械転記）が担うため、この警告は
    転記実行後は発火しない＝恒久ノイズにならない。呼び出し元は src にコードが
    存在する時（＝trace_fill の実行責務がある BLD_GATE 以降）のみ呼ぶこと
    （設計・要件フェーズのゲートで未充填を誤検出しない。check_test_coverage と同型）。
    """
    out = []
    if not led:
        return out
    for key in sorted(led):
        if not key.startswith("REQ-"):
            continue
        node = led[key]
        if not isinstance(node, dict):
            continue
        if not node.get("design"):
            out.append(("medium", "design_traceability",
                        f"{key} の design 列が空（scripts/trace_fill.py で機械充填する）"))
        if not node.get("code"):
            out.append(("medium", "code_traceability",
                        f"{key} の code 列が空（scripts/trace_fill.py で機械充填する）"))
    return out


# ---- 試験トレース（TEST→上流 REQ/DES）----
# 試験計画ファイル名→レベル。総合は要件(REQ)を、結合・単体は設計(DES)を網羅対象とする。
TEST_LEVELS = (("総合", "system"), ("結合", "integration"), ("単体", "unit"))
# 試験項目の見出しから正準ID TEST-NNN を拾う（`### TEST-001` 等。`#NNN` 旧記法は拾わない）。
TEST_HEAD_RE = re.compile(r"\bTEST-\d{3}\b")
# 正準トレース行（`**トレース**: REQ-001, DES-002` / 全角コロン許容）。行頭の `-`/`*` 装飾も許容。
TRACE_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?\*{0,2}トレース\*{0,2}\s*[:：]")
UPSTREAM_ID_RE = re.compile(r"\b((?:REQ|DES)-\d{3})\b")


def _test_level(path):
    """試験計画パスからレベル(system/integration/unit)を判定。未知なら None。"""
    name = pathlib.Path(path).name
    for token, level in TEST_LEVELS:
        if token in name:
            return level
    return None


def parse_test_links():
    """各試験計画の TEST-NNN とその正準トレース行の上流IDを収穫する。

    戻り値: [(test_id, level, [upstream_id,...], path)]。
    試験項目見出し(TEST-NNN を含む行)を現在IDに設定し、以降のトレース行から REQ/DES を集める。
    次の試験項目見出しで現在IDを切り替える。トレース行は項目内に複数あっても合算する。
    """
    links = []
    for p in sorted(_test_plan_files()):
        level = _test_level(p)
        if level is None:
            continue
        cur = None  # 現在の (test_id, ids リスト) を links 内の要素へ参照
        index = {}  # test_id -> links の要素（同一IDの重複見出しに合算）
        for line in pathlib.Path(p).read_text(encoding="utf-8", errors="ignore").splitlines():
            # 見出し行の TEST-NNN のみを試験項目のアンカーとする（散文中の TEST-NNN 言及で
            # current が誤切替するのを防ぐ。定義箇所＝見出し規約と整合）。
            head = TEST_HEAD_RE.search(line) if HEADING_RE.match(line) else None
            if head:
                tid = head.group(0)
                if tid not in index:
                    entry = [tid, level, [], p]
                    links.append(entry)
                    index[tid] = entry
                cur = index[tid]
                continue
            if cur is not None and TRACE_LINE_RE.match(line):
                for up in UPSTREAM_ID_RE.findall(line):
                    if up not in cur[2]:
                        cur[2].append(up)
    return links


def check_test_referential(links, req_set, des_set):
    """各試験の上流IDが実在 REQ/DES か点検（純関数）。dangling→high。"""
    out = []
    valid = req_set | des_set
    for tid, _level, ups, path in links:
        for up in ups:
            if up not in valid:
                out.append(("high", "test_referential",
                            f"{path}: {tid} が参照する {up} が要件/設計に存在しない（dangling）"))
    return out


def check_test_link_presence(links):
    """各試験がレベル相応の上流型を1つ以上持つか点検（純関数）。欠落→high。

    総合(system)→REQ を要求、結合/単体(integration/unit)→DES を要求。
    """
    out = []
    for tid, level, ups, path in links:
        want = "REQ" if level == "system" else "DES"
        if not any(u.startswith(want + "-") for u in ups):
            out.append(("high", "test_link_presence",
                        f"{path}: {tid} に {want} へのトレースが無い"
                        f"（`**トレース**: {want}-NNN` を記す）"))
    return out


def check_req_test_coverage(links, req_set):
    """全 REQ が総合試験で参照されるか点検（純関数）。未網羅→high。

    呼び出し元が総合試験計画の存在を確認してから呼ぶ。
    """
    covered = set()
    for _tid, level, ups, _path in links:
        if level == "system":
            covered.update(u for u in ups if u.startswith("REQ-"))
    return [("high", "req_test_coverage",
             f"未網羅: {r} を確認する総合試験が無い")
            for r in sorted(req_set - covered)]


def check_des_test_coverage(links, des_set):
    """全 DES が単体 or 結合試験で参照されるか点検（純関数）。未網羅→high。

    呼び出し元が単体試験計画の存在を確認してから呼ぶ（単体+結合をプールして判定）。
    """
    covered = set()
    for _tid, level, ups, _path in links:
        if level in ("unit", "integration"):
            covered.update(u for u in ups if u.startswith("DES-"))
    return [("high", "des_test_coverage",
             f"未網羅: {d} を確認する単体/結合試験が無い")
            for d in sorted(des_set - covered)]


def load_ledger():
    p = ROOT/"docs/trace/traceability.json"
    if not p.exists(): return None
    try: return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        add("high","ledger",f"traceability.json が壊れています: {e}"); return None

def check_format(ids, prefix):
    fmt = ID_FMT[prefix]
    for i,(tid,src) in enumerate(ids):
        if not fmt.match(tid):
            add("medium","format",f"{src}: ID書式不正 '{tid}'（期待 {prefix}-NNN）")

def check_unique(ids, prefix):
    seen={}
    for tid,src in ids:
        seen.setdefault(tid,[]).append(src)
    for tid,srcs in seen.items():
        if len(srcs)>1:
            add("high","uniqueness",f"ID重複 '{tid}' が {len(srcs)} 箇所: {', '.join(srcs)}")

# ---- 設計間の依存（feature-dependency-graph）----
DEP_FMT = re.compile(r"^DES-\d{3}$")

def parse_deps(deps_str):
    """data-trace-deps 文字列を DES-ID リストに分解（空白区切り）。"""
    if not deps_str:
        return []
    return [d for d in deps_str.split() if d]

def check_bidirectional(led, req_set, des_set):
    """台帳(led)と要件/設計集合を突合し findings のリストを返す（純関数・テスト可能）。

    台帳トップレベルは接頭辞で種別が決まる（REQ-/DES-/...）。trace-conventions では DES ノードも
    台帳に存在し dependsOn 等を持つため、非REQキーを一律 REQ 扱いして誤報しない。
    REQ 記載漏れ=medium、要件/設計に実在しない台帳ID(幽霊)=high。
    """
    out = []
    led_keys = {k for k in led.keys() if not k.startswith("_")}
    led_reqs = {k for k in led_keys if k.startswith("REQ-")}
    led_dess = {k for k in led_keys if k.startswith("DES-")}
    for r in sorted(req_set - led_reqs):
        out.append(("medium", "bidirectional", f"台帳記載漏れ: {r} が traceability.json に無い"))
    for r in sorted(led_reqs - req_set):
        out.append(("high", "bidirectional", f"幽霊ID: 台帳の {r} が要件に実在しない"))
    for d in sorted(led_dess - des_set):
        out.append(("high", "bidirectional", f"幽霊ID: 台帳の {d} が設計に実在しない"))
    return out


def check_deps_referential(des_deps, des_set):
    """各 DES の依存先を点検し findings のリストを返す。
    des_deps: {des_id: [dep_id,...]}、des_set: 実在 DES の集合。
    書式不正=medium、自己依存/実在しない依存先(dangling)=high。"""
    out = []
    for did in sorted(des_deps):
        for dep in des_deps[did]:
            if not DEP_FMT.match(dep):
                out.append(("medium","dep_format",f"{did} の依存先ID書式不正 '{dep}'（期待 DES-NNN）"))
            elif dep == did:
                out.append(("high","dep_self",f"{did} が自分自身に依存している"))
            elif dep not in des_set:
                out.append(("high","dep_referential",f"{did} が依存する {dep} が設計に存在しない（dangling）"))
    return out

def find_dep_cycles(des_deps):
    """依存グラフの循環を反復DFSで検出し findings のリストを返す（ノード集合で重複排除）。

    - 再帰を使わないため深い依存連鎖でも RecursionError にならない。
    - 自己依存（dep==node）は check_deps_referential の dep_self が扱うため、循環としては
      報告しない（二重報告の回避）。
    """
    out = []
    seen = set()
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {d: WHITE for d in des_deps}
    for root in sorted(des_deps):
        if color[root] != WHITE:
            continue
        # スタックに (node, 次に見る依存先のインデックス) を積む反復DFS
        stack = [(root, 0)]
        color[root] = GRAY
        path = [root]
        while stack:
            node, idx = stack[-1]
            deps = des_deps.get(node, [])
            if idx < len(deps):
                stack[-1] = (node, idx + 1)
                dep = deps[idx]
                if dep == node:
                    continue  # 自己依存は dep_self が扱う
                if dep not in color:
                    continue  # 実在しない依存先は referential 側で報告
                if color[dep] == GRAY:
                    cyc = path[path.index(dep):] + [dep]
                    key = frozenset(cyc)
                    if key not in seen:
                        seen.add(key)
                        out.append(("high","dep_cycle","依存の循環: " + " -> ".join(cyc)))
                elif color[dep] == WHITE:
                    color[dep] = GRAY
                    stack.append((dep, 0))
                    path.append(dep)
            else:
                stack.pop()
                path.pop()
                color[node] = BLACK
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
    reqs = parse_requirements()
    dess = parse_design()
    tests = parse_tests()
    req_ids = [(t,s) for t,s in reqs]
    des_ids = [(t,r) for t,r,_ in dess]
    # 書式・一意
    check_format(req_ids,"REQ"); check_unique(req_ids,"REQ")
    check_format([(t,"設計") for t,_,_ in dess],"DES"); check_unique([(t,"設計") for t,_,_ in dess],"DES")
    check_format(tests,"TEST"); check_unique(tests,"TEST")
    req_set = {t for t,_ in reqs}
    des_set = {t for t,_,_ in dess}
    # 参照整合: 設計の data-trace-req が実在するREQか
    for tid,req,_ in dess:
        for r in re.findall(r"REQ-\w+", req or ""):
            if r not in req_set:
                add("high","referential",f"設計 {tid} が参照する {r} が要件に存在しない（dangling）")
    # カバレッジ: 全REQに対応する設計があるか（設計のreqに現れるか）。
    # **設計書が存在する時のみ**検査する（code_coverage の src 条件・試験カバレッジの計画条件と同型）。
    # 設計フェーズ前の REQ_GATE では設計が無いのが正常であり、無条件に high を出すと
    # gate-runner（exit code 判定のみ・意味解釈しない）が恒久 high ループに陥る
    # （旧 consistency-checker ゲートは意味解釈で吸収していた暗黙依存への対策）。
    design_exists = bool(glob.glob("docs/design/*.html"))
    led = load_ledger()
    if design_exists:
        covered = set()
        for _,req,_ in dess:
            covered.update(re.findall(r"REQ-\w+", req or ""))
        for r in sorted(req_set - covered):
            # デルタフロー猶予: 変更モードで新設された REQ は設計が後工程のため、
            # 台帳ノードに design_pending:true が立っていれば high でなく medium に下げる
            # （REQ_GATE の gate-runner を止めない）。設計反映後に designer がフラグを除去する。
            # 除去忘れは下の coverage_pending_stale が medium で促し、BLD_GATE の
            # consistency-checker（意味整合）が pending 残置を差し戻す。
            node = led.get(r) if isinstance(led, dict) else None
            if isinstance(node, dict) and node.get("design_pending") is True:
                add("medium","coverage_pending",
                    f"設計待ち: {r} は design_pending（変更モードの新規要件）。"
                    f"設計フェーズで設計を追加し、反映後にフラグを除去すること")
            else:
                add("high","coverage",f"未実装: {r} に対応する設計が無い")
        # 設計済みなのに design_pending が残っている（フラグ除去忘れ）→ medium で除去を促す
        if isinstance(led, dict):
            for r in sorted(req_set & covered):
                node = led.get(r)
                if isinstance(node, dict) and node.get("design_pending") is True:
                    add("medium","coverage_pending_stale",
                        f"{r} は設計済みだが design_pending フラグが残置。台帳から除去すること")
    # 設計間の依存（data-trace-deps）: 参照整合 + 循環検出
    des_deps = {tid: parse_deps(deps) for tid,_,deps in dess}
    findings.extend(check_deps_referential(des_deps, des_set))
    findings.extend(find_dep_cycles(des_deps))
    # 実装トレース（DES→code）: src/ のマーカーを収穫して dangling/カバレッジを検査
    marked_des, marker_locs, src_has_code = parse_src()
    findings.extend(check_code(marked_des, marker_locs, des_set, src_has_code))
    # 双方向: 台帳があれば突合。要件/設計があるのに台帳が無ければ fail-closed（未生成=high）。
    # 台帳トップレベルは接頭辞で種別が決まる（REQ-/DES-/TEST-/...）。trace-conventions では
    # DES ノードも台帳に存在し dependsOn 等を持つため、非REQキーを一律 REQ 扱いして
    # 「幽霊ID」と誤報しない。種別ごとに実在性を突合する。
    ledger_path = ROOT/"docs/trace/traceability.json"
    if led is not None:
        findings.extend(check_bidirectional(led, req_set, des_set))
    elif (req_set or des_set) and not ledger_path.exists():
        add("high","ledger","traceability.json が未生成（要件/設計があるのに台帳が無い）")
    # 試験結果が存在する時のみ REQ.test の空チェック（設計/構築フェーズでの誤検出防止）
    test_result_exists = any(pathlib.Path(p).exists()
                            for p in glob.glob("docs/test/*試験結果*.md"))
    if led is not None and test_result_exists:
        findings.extend(check_test_coverage(led))
    # src にコードがあり、かつ build/test フェーズの時のみ REQ.design/code の空チェック
    # （REQ の design/code 列が空のまま完走するのを防ぐ。充填責務は BLD_GATE/T_GATE の consistency-checker＝trace_fill.py 実行。
    # デルタフローの REQ_GATE/DES_GATE では src が既にあるためフェーズでもゲートする）
    if led is not None and src_has_code and _fill_phase_active():
        findings.extend(check_design_code_coverage(led))
    # 試験トレース（TEST→上流）: 正準トレース行を収穫し参照整合・型・カバレッジを検査。
    test_links = parse_test_links()
    findings.extend(check_test_referential(test_links, req_set, des_set))
    findings.extend(check_test_link_presence(test_links))
    # REQ→総合試験 カバレッジは総合試験計画がある時のみ（要件フェーズ以降。レビュー記録は除外）。
    if _test_plan_files("総合試験計画"):
        findings.extend(check_req_test_coverage(test_links, req_set))
    # DES→単体/結合試験 カバレッジは単体試験計画がある時のみ（構築フェーズ以降。単体+結合をプール）。
    if _test_plan_files("単体試験計画"):
        findings.extend(check_des_test_coverage(test_links, des_set))
    # 出力
    order={"high":0,"medium":1,"low":2}
    for sev,kind,msg in sorted(findings,key=lambda x:order.get(x[0],9)):
        print(f"[{sev}] {kind}: {msg}")
    highs=[f for f in findings if f[0]=="high"]
    print(f"\nsummary: high={sum(1 for f in findings if f[0]=='high')} "
          f"medium={sum(1 for f in findings if f[0]=='medium')} "
          f"low={sum(1 for f in findings if f[0]=='low')}")
    sys.exit(1 if highs else 0)

if __name__ == "__main__":
    main()
