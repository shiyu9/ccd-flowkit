#!/usr/bin/env python
"""Claude Code Stop Hook: ccd-flowkit のフローが未完了なら継続を強制する（自律進行ガード）。

オーケストレーターがゲート後にターンを区切ると、リモートセッションでは次のユーザー発話まで
自律進行しない事象が起きうる。プロンプト指示だけでは保証が無いため、Stop フックで機械的に継続させる。

仕組み: 状態機械 state/_flow_state.json の現在ノードが終端(DONE)でなければ stdout に
`{"decision":"block","reason":...}` を出してエージェントを継続させる。DONE / 状態無し /
滞留・通算上限超過 / 例外 では block しない（fail-open）。ユーザー判断は AskUserQuestion で行う規約
（裸のターン終了で待たない）と対で機能する。

無限ブロック防止（多段・いずれも『迷ったら block しない』へ倒す）:
  - 進捗シグネチャ（現在ノード ＋ _agent_invocations.jsonl の行数）が変われば連続カウンタを 0 にリセット
    （ノード遷移・新規委任＝productive を進捗とみなす）。
  - 同一進捗で連続 STALL_CAP 回継続したら止める（滞留＝人手介入を許可）。
  - 1ラン通算 HARD_CAP 回で必ず止める（最終安全弁）。
  - 永続化フェイルセーフ: guard 破損／atomic 書込失敗時は block しない（無限ブロックを避ける）。
"""
import json
import os
import sys
import traceback
from pathlib import Path

# Windows(cp932)対策。stdin も含め UTF-8 化（payload JSON を化けさせない）。
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

PROJECT_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
FLOW_STATE_FILE = PROJECT_ROOT / "state" / "_flow_state.json"
INVOCATIONS_FILE = PROJECT_ROOT / "state" / "_agent_invocations.jsonl"
GUARD_FILE = PROJECT_ROOT / "state" / "_continue_guard.json"
GUARD_TMP = PROJECT_ROOT / "state" / "_continue_guard.json.tmp"
TERMINAL = "DONE"
STALL_CAP = 25    # 同一進捗で連続継続する上限（進捗で reset）
HARD_CAP = 200    # 1ラン通算の継続上限（reset しない最終安全弁）


def _read_json(path):
    try:
        if path.exists():
            d = json.loads(path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else None
    except Exception:
        return None
    return None


def load_flow_state(path=FLOW_STATE_FILE):
    """_flow_state.json を読む。無い/壊れている/current 欠落なら None（＝フロー無し扱い・block しない）。"""
    d = _read_json(path)
    if not isinstance(d, dict) or not isinstance(d.get("current"), str):
        return None
    return d


def is_done(state):
    """現在ノードが終端(DONE)か。"""
    return isinstance(state, dict) and state.get("current") == TERMINAL


def _read_guard():
    """guard を読む。戻り値 (state, dict)。state: 'absent'|'ok'|'corrupt'。"""
    if not GUARD_FILE.exists():
        return ("absent", {})
    try:
        d = json.loads(GUARD_FILE.read_text(encoding="utf-8"))
        return ("ok", d) if isinstance(d, dict) else ("corrupt", {})
    except Exception:
        return ("corrupt", {})


def _invocation_count(path=INVOCATIONS_FILE):
    try:
        if path.exists():
            return sum(1 for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines()
                       if ln.strip())
    except Exception:
        pass
    return 0


def _progress_signature(state, inv_count):
    """進捗の指紋。現在ノード＋委任数が変われば『進捗あり』とみなす（純関数・テスト用に inv_count 注入可）。"""
    return f"cur={state.get('current')}|inv={inv_count}"


def decide(state, guard, inv_count=0):
    """(block: bool, reason: str|None, new_guard: dict) を返す（純関数・テスト可能）。

    state=None（フロー無し）/ DONE → block しない。未完了かつ滞留・通算上限内 → block（継続強制）。
    """
    g = guard if isinstance(guard, dict) else {}
    if state is None or is_done(state):
        return (False, None, g)
    sig = _progress_signature(state, inv_count)
    count = g.get("count") if isinstance(g.get("count"), int) else 0
    total = g.get("total") if isinstance(g.get("total"), int) else 0
    if sig != g.get("sig"):
        count = 0  # 進捗あり（ノード遷移 or 新規委任）→ リセット
    count += 1
    total += 1
    new_guard = {"sig": sig, "count": count, "total": total}
    if count > STALL_CAP or total > HARD_CAP:
        return (False, None, new_guard)  # 滞留 or 通算上限 → 継続をやめ人手へ
    reason = (
        f"ccd-flowkit のフローが未完了です（現在ノード: {state.get('current')}）。"
        "ターンを終えず、状態機械に従い次のノードの担当エージェントを起動してください"
        "（manage_flow_state.py current を読み、対応エージェントを委任）。ユーザーの判断・確認が要る点のみ"
        " AskUserQuestion で問うこと（裸のターン終了で待たない）。現在ノードが DONE になったら終了してよい。"
    )
    return (True, reason, new_guard)


def _save_guard(guard):
    """guard を atomic（tmp+replace）に保存し、成功なら True。失敗なら False（安全側に倒す）。"""
    try:
        GUARD_FILE.parent.mkdir(parents=True, exist_ok=True)
        GUARD_TMP.write_text(json.dumps(guard, ensure_ascii=False), encoding="utf-8")
        os.replace(GUARD_TMP, GUARD_FILE)
        return True
    except Exception:
        return False


def main():
    try:
        try:
            if not sys.stdin.isatty():
                sys.stdin.read()
        except Exception:
            pass
        state = load_flow_state()
        if state is None or is_done(state):
            return 0  # フロー無し/完了 → 継続強制しない

        gstate, guard = _read_guard()
        if gstate == "corrupt":
            _save_guard({"sig": "", "count": 0, "total": 0})
            return 0

        block, reason, new_guard = decide(state, guard, _invocation_count())
        if not block:
            _save_guard(new_guard)
            return 0
        if not _save_guard(new_guard):
            return 0
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0
    except Exception:
        try:
            traceback.print_exc(file=sys.stderr)
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
