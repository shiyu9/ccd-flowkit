#!/usr/bin/env python
"""manage_flow_state.py
開発フローの状態機械（ノード粒度）を管理する。オーケストレーターは current を読んで
対応エージェントを起動するだけ、state 更新は各サブエージェントが advance で行う。

state ファイル: state/_flow_state.json
  {"version":1, "current":"REQ_PLAN", "history":[{"node","outcome","to"},...]}

サブコマンド:
  init                    _flow_state.json を初期ノード(REQ_PLAN)で作成（既存は尊重・冪等。
                          seed 済みディレクトリでのみ実行可＝誤 cwd での state 新規生成を拒否）
  current                 現在ノードと担当エージェントを JSON 出力（ディスパッチャが読む）
  advance <node> <outcome>  node が現在ノードに一致する時のみ、outcome に応じ次ノードへ遷移
  agent <node>            指定ノードの担当エージェント名を出力

設計方針: オーケストレーターは編集権限を持たない（M2）。advance は各サブエージェントが Bash で呼ぶ。
遷移は決定論（TRANSITIONS 表）。LLM 判断は使わない。
"""
import json
import os
import pathlib
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

PROJECT_ROOT = pathlib.Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
STATE_FILE = PROJECT_ROOT / "state" / "_flow_state.json"

# 誤ルート解決（cwd/CLAUDE_PROJECT_DIR 不一致）の無検査書込を防ぐ（project_guard.py 参照）。
# PROJECT_ROOT は env 経由で cwd と別に決まりうるため、cwd 固定でなく解決済みルートを検査する。
try:
    from project_guard import ensure_project_root
except ImportError:  # 配置差異でも fail-open にしない最小フォールバック
    def ensure_project_root(root=None):
        check = pathlib.Path(root) if root is not None else pathlib.Path(".")
        if not (check / "docs").is_dir():
            print(f"エラー: {check} に docs/ が無い（プロジェクトルート外での実行）", file=sys.stderr)
            sys.exit(2)

INITIAL = "REQ_PLAN"
TERMINAL = "DONE"

# ノード → フェーズ（protected-path マーカーの単位）。ディスパッチャがフェーズ境界で
# manage_active_skill.py acquire/release する（成果物書込を許すため）。None はフェーズ外（承認/終端）。
NODE_PHASE = {
    "REQ_PLAN": "requirements", "REQ_APPROVE": None, "REQ_WRITE": "requirements",
    "SYS_PLAN": "requirements", "SYS_REVIEW": "requirements", "SYS_FIX": "requirements",
    "REQ_GATE": "requirements",
    "DES_DRAFT": "design", "DES_VERIFY": "design", "DES_FINAL": "design",
    "DES_REVIEW": "design", "DES_FIX": "design",
    "INT_PLAN": "design", "INT_REVIEW": "design", "INT_FIX": "design", "DES_GATE": "design",
    "BLD_IMPL": "build", "BLD_REVIEW": "build", "BLD_FIX": "build",
    "UNIT_PLAN": "build", "UNIT_REVIEW": "build", "UNIT_FIX": "build", "BLD_GATE": "build",
    "T_UNIT": "test", "T_INT": "test", "T_SYS": "test", "T_GATE": "test",
    "RCA": "test",  # 試験フェーズ内で解析（docs/test/根本原因解析.md を書くため test マーカー下）
    "LEARN": None, "DONE": None,
}

# ノード → 担当エージェント（None は担当なし＝ユーザー承認/終端）。
NODE_AGENT = {
    "REQ_PLAN": "project-planner",
    "REQ_APPROVE": None,              # ユーザー承認（オーケストレーターが AskUserQuestion で仲介）
    "REQ_WRITE": "project-planner",
    "SYS_PLAN": "system-test-planner",
    "SYS_REVIEW": "system-test-plan-reviewer",
    "SYS_FIX": "system-test-planner",
    "REQ_GATE": "gate-runner",  # 軽量ゲート: 決定論スキャナのみ（意味整合はレビューへ吸収）
    "DES_DRAFT": "designer",
    "DES_VERIFY": "verifier",
    "DES_FINAL": "designer",
    "DES_REVIEW": "design-reviewer",
    "DES_FIX": "designer",
    "INT_PLAN": "integration-test-planner",
    "INT_REVIEW": "integration-test-plan-reviewer",
    "INT_FIX": "integration-test-planner",
    "DES_GATE": "gate-runner",  # 軽量ゲート: 決定論スキャナのみ（DES間依存・decisions来歴の意味整合は DES_REVIEW へ）
    "BLD_IMPL": "builder",
    "BLD_REVIEW": "build-reviewer",
    "BLD_FIX": "builder",
    "UNIT_PLAN": "unit-test-planner",
    "UNIT_REVIEW": "unit-test-plan-reviewer",
    "UNIT_FIX": "unit-test-planner",
    "BLD_GATE": "consistency-checker",
    "T_UNIT": "unit-tester",
    "T_INT": "integration-tester",
    "T_SYS": "system-tester",
    "T_GATE": "consistency-checker",
    "RCA": "root-cause-analyzer",
    "LEARN": "lessons-distiller",
    "DONE": None,
}

# (ノード, outcome) → 次ノード。outcome 語彙:
#   done   : 生成/是正の完了（次ステップへ）
#   pass   : レビュー/ゲート合格
#   high   : レビュー/ゲートで未是正「高」→ 是正へ
#   approve/revise : ユーザー承認/要修正
#   verify : 設計に [E?] があり検証が要る / ok/fail : verifier 結果
#   ng     : 試験 NG → root-cause-analyzer へ
#   impl/design/req/test/unitplan/intplan/sysplan : RCA の原因層判定 → 回帰先
TRANSITIONS = {
    "REQ_PLAN": {"done": "REQ_APPROVE"},
    "REQ_APPROVE": {"approve": "REQ_WRITE", "revise": "REQ_PLAN"},
    "REQ_WRITE": {"done": "SYS_PLAN"},
    "SYS_PLAN": {"done": "SYS_REVIEW"},
    "SYS_REVIEW": {"pass": "REQ_GATE", "high": "SYS_FIX"},
    "SYS_FIX": {"done": "SYS_REVIEW"},
    "REQ_GATE": {"pass": "DES_DRAFT", "high": "SYS_FIX"},
    "DES_DRAFT": {"verify": "DES_VERIFY", "done": "DES_REVIEW"},
    "DES_VERIFY": {"ok": "DES_FINAL", "fail": "DES_DRAFT"},
    "DES_FINAL": {"done": "DES_REVIEW"},
    "DES_REVIEW": {"pass": "INT_PLAN", "high": "DES_FIX"},
    "DES_FIX": {"done": "DES_REVIEW"},
    "INT_PLAN": {"done": "INT_REVIEW"},
    "INT_REVIEW": {"pass": "DES_GATE", "high": "INT_FIX"},
    "INT_FIX": {"done": "INT_REVIEW"},
    "DES_GATE": {"pass": "BLD_IMPL", "high": "DES_FIX"},
    "BLD_IMPL": {"done": "BLD_REVIEW"},
    "BLD_REVIEW": {"pass": "UNIT_PLAN", "high": "BLD_FIX"},
    "BLD_FIX": {"done": "BLD_REVIEW"},
    "UNIT_PLAN": {"done": "UNIT_REVIEW"},
    "UNIT_REVIEW": {"pass": "BLD_GATE", "high": "UNIT_FIX"},
    "UNIT_FIX": {"done": "UNIT_REVIEW"},
    "BLD_GATE": {"pass": "T_UNIT", "high": "BLD_FIX"},
    "T_UNIT": {"pass": "T_INT", "ng": "RCA"},
    "T_INT": {"pass": "T_SYS", "ng": "RCA"},
    "T_SYS": {"pass": "T_GATE", "ng": "RCA"},
    "T_GATE": {"pass": "LEARN", "high": "RCA"},
    # root-cause-analyzer: 原因層に応じ回帰。impl→BLD_FIX / design→DES_FIX / req→REQ_APPROVE(ユーザー判断)
    # test→T_UNIT: 原因が試験の**結果文書・ハーネス**側（書式・集計・ハーネス不具合）で成果物は正しい場合。
    # 該当 tester が結果を是正して試験レベルを再通過する（試験文書起因を impl と誤分類すると
    # 無意味な構築回帰が発生するため、第4の原因層として分離）。
    # unitplan/intplan/sysplan→各 *_FIX: 原因が**試験計画の期待値誤り**の場合、計画の是正→再レビュー→
    # ゲート→全試験再実行の前進経路に乗せる。T_UNIT 回帰では計画文書がフェーズマーカー外で
    # 是正不能になるため、計画層は各 FIX ノードへ直接戻す。
    "RCA": {"impl": "BLD_FIX", "design": "DES_FIX", "req": "REQ_APPROVE", "test": "T_UNIT",
            "unitplan": "UNIT_FIX", "intplan": "INT_FIX", "sysplan": "SYS_FIX"},
    "LEARN": {"done": "DONE"},
    # デルタフロー（変更モード）: 完成後の変更要求は root-cause-analyzer が受理し、
    # RCA と同じ層判定で適切な入口へ回帰する（フルフロー再走でなく影響範囲だけ再工程。
    # 回帰後は既存の前進経路＝再レビュー(3成分diff)・ゲート・全試験(ハッシュ免除可)が働く）。
    "DONE": {"change": "RCA"},
}


# ---- 純関数（テスト可能）----
def node_agent(node):
    """ノードの担当エージェント名（無ければ None）。未知ノードは KeyError を避け None。"""
    return NODE_AGENT.get(node)


def is_terminal(node):
    return node == TERMINAL


def node_phase(node):
    """ノードの protected-path フェーズ（無ければ None）。"""
    return NODE_PHASE.get(node)


def valid_outcomes(node):
    """ノードで受理される outcome の一覧（ソート済み）。"""
    return sorted(TRANSITIONS.get(node, {}).keys())


def next_node(node, outcome):
    """(node, outcome) の遷移先を返す。無効なら None。"""
    return TRANSITIONS.get(node, {}).get(outcome)


def all_nodes():
    return list(NODE_AGENT.keys())


# 収束規律（review-conventions.md）のラウンド上限の正本。review_round がこの値を超えたら
# レビューアは advance せず「要ユーザー判断」でエスカレーションする。超過したままの
# `advance <*_REVIEW> pass` は hook_check_advance_actor が機械的に block する。
# 各エージェント定義・規約文書はこの定数を参照する形で記述する（値の複製を増やさない＝P1）。
REVIEW_ROUND_ESCALATION_THRESHOLD = 3

# フラット化モデル: integrator が同一ラウンド内で観点サブの rerun を要求できる
# 上限の正本。この値を超えた rerun 要求はディスパッチャが AskUserQuestion でユーザーに諮る
# （強制収束はしない＝integrator の判断を上書きしない）。check_rerun_limit.py が
# _advance_audit.jsonl の op:"rerun" レコードを集計し本定数と比較する。
RERUN_LIMIT_PER_ROUND = 2


def review_round(history, node):
    """レビューノードの現在ラウンド数を返す（純関数）。レビューノード以外は None。

    直近の同ノード遷移を新しい方から辿り、outcome=high（差し戻し）の連続回数＋1。
    pass 等で終わった前サイクルには跨がない（ゲート回帰での再入は新サイクル＝round 1）。
    収束規律（review-conventions.md）: round が REVIEW_ROUND_ESCALATION_THRESHOLD を
    超えたらレビューアはエスカレーションする。
    """
    if not isinstance(node, str) or not node.endswith("_REVIEW"):
        return None
    rounds = 1
    for h in reversed(history or []):
        if isinstance(h, dict) and h.get("node") == node:
            if h.get("outcome") == "high":
                rounds += 1
            else:
                break
    return rounds


def _validate_tables():
    """TRANSITIONS と NODE_AGENT の整合を検証（全ノード網羅・遷移先が実在ノード）。戻り値: 問題リスト。"""
    problems = []
    for node in NODE_AGENT:
        if node not in TRANSITIONS:
            problems.append(f"TRANSITIONS に {node} が無い")
        if node not in NODE_PHASE:
            problems.append(f"NODE_PHASE に {node} が無い")
    for node, outs in TRANSITIONS.items():
        if node not in NODE_AGENT:
            problems.append(f"NODE_AGENT に {node} が無い")
        for outcome, dest in outs.items():
            if dest not in NODE_AGENT:
                problems.append(f"{node} --{outcome}--> {dest} の遷移先が未定義ノード")
    return problems


# ---- state I/O ----
def load_state():
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_advance(state, node, outcome, waiver=False):
    """状態に advance を適用し (new_state, error) を返す（純関数・テスト可能）。

    - node が現在ノードと一致しなければ error。
    - outcome がそのノードで無効なら error。
    - 成功時は current を遷移先にし history に追記した新 state を返す。
    - waiver=True はユーザー承諾済み免除の advance（収束規律のラウンド上限超過を
      ユーザーがスルー承諾した場合等）。history に waiver: true を記録し、事後検証
      （レビュー記録の免除注記・AskUserQuestion 実施との突合）の対象にする。
    """
    if not isinstance(state, dict) or "current" not in state:
        return None, "state が不正（current が無い）"
    cur = state["current"]
    if node != cur:
        return None, f"現在ノードは {cur} です（指定 {node} と不一致）。advance は現在ノードにのみ可"
    dest = next_node(cur, outcome)
    if dest is None:
        return None, f"{cur} で無効な outcome '{outcome}'（有効: {', '.join(valid_outcomes(cur)) or 'なし'}）"
    new = dict(state)
    new["current"] = dest
    hist = list(state.get("history", []))
    entry = {"node": cur, "outcome": outcome, "to": dest}
    if waiver:
        entry["waiver"] = True
    hist.append(entry)
    new["history"] = hist
    return new, None


# ---- サブコマンド ----
def cmd_init():
    if STATE_FILE.exists():
        print(json.dumps({"status": "exists", "current": (load_state() or {}).get("current")},
                         ensure_ascii=False))
        return 0
    # seed マーカー必須: 誤った cwd（例: プラグイン開発リポジトリ）で init すると無関係な場所に
    # state/ を新規生成してしまう（リポジトリ汚染への対策）。seed_project.py が置く
    # state/_protected_paths.json があるディレクトリ＝正しいプロジェクトルートでのみ作成を許す。
    seed_marker = STATE_FILE.parent / "_protected_paths.json"
    if not seed_marker.exists():
        print(f"エラー: seed 未実施のディレクトリです（{seed_marker} がありません）。\n"
              f"  プロジェクトルートで seed_project.py を先に実行するか、cwd / CLAUDE_PROJECT_DIR を\n"
              f"  正しいプロジェクトルートに合わせてください（誤った場所への state 生成を防ぐため拒否）。",
              file=sys.stderr)
        return 1
    state = {"version": 1, "current": INITIAL, "history": []}
    write_state(state)
    print(json.dumps({"status": "created", "current": INITIAL}, ensure_ascii=False))
    return 0


def cmd_current():
    state = load_state()
    if state is None:
        print(f"エラー: {STATE_FILE} がありません。プロジェクトルート（要望書のあるディレクトリ）で\n"
              f"  実行するか、CLAUDE_PROJECT_DIR を設定してください（新規フローなら先に init）。\n"
              f"  ※ state を Write/Edit で手書きしないこと（履歴が壊れます）。advance が失敗する場合は\n"
              f"  手書きせず、失敗をそのまま報告して完了してください。", file=sys.stderr)
        return 1
    cur = state.get("current")
    out = {
        "current": cur,
        "agent": node_agent(cur),
        "phase": node_phase(cur),
        "terminal": is_terminal(cur),
        "valid_outcomes": valid_outcomes(cur),
    }
    rr = review_round(state.get("history", []), cur)
    if rr is not None:
        # ディスパッチャがレビュー委任プロンプトに「第Nラウンド」として含める（収束規律）
        out["review_round"] = rr
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_advance():
    if len(sys.argv) < 4:
        print("使い方: manage_flow_state.py advance <node> <outcome> [waiver]", file=sys.stderr)
        return 1
    node, outcome = sys.argv[2], sys.argv[3]
    # hook 側（_waiver_token_present）は IGNORECASE で通すため、記録側も大小無視で
    # 揃える（WAIVER 表記で hook を通過したのに history に残らない非対称を防ぐ）
    waiver = len(sys.argv) > 4 and sys.argv[4].lower() == "waiver"
    state = load_state()
    if state is None:
        print(f"エラー: {STATE_FILE} がありません。プロジェクトルート（要望書のあるディレクトリ）で\n"
              f"  実行するか、CLAUDE_PROJECT_DIR を設定してください（新規フローなら先に init）。\n"
              f"  ※ state を Write/Edit で手書きしないこと（履歴が壊れます）。advance が失敗する場合は\n"
              f"  手書きせず、失敗をそのまま報告して完了してください。", file=sys.stderr)
        return 1
    new, err = apply_advance(state, node, outcome, waiver=waiver)
    if err:
        print(f"エラー: {err}", file=sys.stderr)
        return 2
    write_state(new)
    print(json.dumps({"advanced": f"{node} --{outcome}--> {new['current']}",
                      "current": new["current"], "agent": node_agent(new["current"])},
                     ensure_ascii=False))
    return 0


def cmd_agent():
    if len(sys.argv) < 3:
        print("使い方: manage_flow_state.py agent <node>", file=sys.stderr)
        return 1
    print(node_agent(sys.argv[2]) or "")
    return 0


INVOCATION_TS_TOLERANCE_SECONDS = 1.0  # hook_log_agent.py の記録が秒精度のため許容する誤差。


def _last_agent_invocation_at(project_root=None):
    """state/_agent_invocations.jsonl 最終行のタイムスタンプ(epoch秒)を返す（無ければ None）。

    hook_log_agent.py が Task/Agent 起動のたびにこのファイルへ追記する。
    **フラット化モデル以降は主に監査用途**: 旧集約役モデルでは cmd_wait の
    `recent_activity` シグナル生成に使われていたが、フラット化後は Task が同期で advance も
    済んだ状態で復帰するため、main が state 変化を polling する必要が消滅し、本関数の値も
    フロー制御には使われない（cmd_wait の docstring 参照）。監査目的では引き続き有用
    （委任履歴の時系列突合など）。
    壊れた行・非UTF-8断片・非dict JSON等はいずれも例外にせず None にする fail-open。
    """
    root = pathlib.Path(project_root) if project_root is not None else PROJECT_ROOT
    path = root / "state" / "_agent_invocations.jsonl"
    try:
        # errors="ignore": review_authenticity_scan.py の load_invoked_agents() と同じ方針。
        # 並行書込による行破損（非UTF-8断片）でも例外にせず読める範囲を使う（fail-open）。
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    last_line = None
    for line in text.splitlines():
        line = line.strip()
        if line:
            last_line = line
    if last_line is None:
        return None
    try:
        rec = json.loads(last_line)
        if not isinstance(rec, dict):
            return None
        at = rec.get("at")
        if not isinstance(at, str):
            return None
        import datetime
        return datetime.datetime.fromisoformat(at).timestamp()
    except (ValueError, TypeError, OverflowError, OSError, json.JSONDecodeError):
        return None


def cmd_wait():
    """[deprecated] state が変化するまでブロックして待つ。ディスパッチャの旧待機用。

    使い方: manage_flow_state.py wait [timeout_sec=600] [interval_sec=2]

    **フラット化モデルへの移行で deprecated**: フラット化モデル以降、Task は本質的に
    同期呼び出しであり Task 復帰時点でサブエージェントは完了し advance も済んでいる。したがって
    main が state 変化を polling する必要はなくなり、Task 復帰直後に `manage_flow_state.py current`
    を1回呼ぶだけで新しい current が取れる。本関数は後方互換のため残し、呼び出しに対して stderr へ
    deprecation 警告を出す（実行は継続。旧セッションでも問題なく動くため）。

    背景（歴史的経緯）: 集約役モデルでは main が集約役を Task 起動→集約役が観点サブを background で
    起動→集約役が即座に return する非同期構造だったため、main は集約役の Task 復帰時点で「観点サブ
    完了もしていなければ advance もされていない」状態を目にした。この状況で state 変化を待つ手段が
    必要だった。フラット化後は main が直接観点サブ・integrator を Task 起動し、それぞれ同期で返る
    ため、この状況は構造的に消滅した。

    実装は従来通り: state が変化するまで interval_sec ごとに polling し timeout_sec で打ち切る
    （呼び出し時に state が既に変化していれば interval 経過後の1回目の check で return する）。
    出力の `recent_activity` は _agent_invocations.jsonl の直近シグナルで、旧モデルでは
    「観点サブが起動中か」の判定材料として使ったが、新モデルでは main が Task を並列起動する形なので
    参考情報として残す（判定には使わない）。
    """
    import sys as _sys
    _sys.stderr.write(
        "[deprecation] manage_flow_state.py wait はフラット化モデルで不要になりました。\n"
        "  Task は同期呼び出しで復帰時点で advance 済みのため、代わりに `current` を1回呼んでください。\n"
        "  詳細: SKILL.md 手順 5 / cmd_wait docstring。実行は継続します（後方互換）。\n"
    )
    try:
        timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 600.0
        interval = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0
    except ValueError:
        print("使い方: manage_flow_state.py wait [timeout_sec] [interval_sec]", file=sys.stderr)
        return 1
    # Bash ツールの1回呼び出し上限(600000ms)内に収める。
    timeout = max(1.0, min(timeout, 590.0))
    interval = max(0.2, min(interval, 60.0))
    state = load_state()
    if state is None:
        print("エラー: _flow_state.json がありません。先に init してください。", file=sys.stderr)
        return 1
    initial = state.get("current")
    import time
    segment_start_wall = time.time()
    deadline = time.monotonic() + timeout
    cur = initial
    while time.monotonic() < deadline:
        time.sleep(interval)
        state = load_state() or {}
        cur = state.get("current", initial)
        if cur != initial:
            break
    out = {
        "changed": cur != initial,
        "current": cur,
        "agent": node_agent(cur),
        "phase": node_phase(cur),
        "terminal": is_terminal(cur),
        "valid_outcomes": valid_outcomes(cur),
    }
    if cur == initial:
        last_at = _last_agent_invocation_at()
        out["recent_activity"] = (
            last_at is not None and last_at >= segment_start_wall - INVOCATION_TS_TOLERANCE_SECONDS
        )
    else:
        out["recent_activity"] = None
    rr = review_round((load_state() or {}).get("history", []), cur)
    if rr is not None:
        out["review_round"] = rr
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    ensure_project_root(str(PROJECT_ROOT))
    cmd = sys.argv[1]
    dispatch = {"init": cmd_init, "current": cmd_current, "advance": cmd_advance,
                "agent": cmd_agent, "wait": cmd_wait}
    fn = dispatch.get(cmd)
    if not fn:
        print(f"不明なサブコマンド: {cmd}", file=sys.stderr)
        return 1
    return fn()


if __name__ == "__main__":
    sys.exit(main())
