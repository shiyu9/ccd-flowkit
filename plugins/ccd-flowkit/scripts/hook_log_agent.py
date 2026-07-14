#!/usr/bin/env python
"""Claude Code PreToolUse Hook: サブエージェント起動（Task/Agent）を記録する。

オーケストレーターが「レビュアーを起動した」と称しながら実際は委任せず自己レビューで
所見を捏造する事象を検出するため、実際のサブエージェント起動を
state/_agent_invocations.jsonl に1行ずつ追記する。review_authenticity_scan.py が
レビュー記録の「起動サブエージェント」と突合し、未起動なのに所見があれば捏造と判定する。

あわせて**委任プロンプト本文**（prompt、先頭2000字）と**起動元**（by: main / 起動側の
agent_type）も記録する。再レビュー委任が中立か（結論・是正内容の転記＝追認誘導が無いか）を
後から検証するための一次記録。

記録のみ。**決してブロックしない**（常に exit 0）。state 書込失敗も握りつぶす。
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Windows(cp932)対策。stdin も含め UTF-8 化（hook 入力 JSON の日本語を化けさせない）。
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

PROJECT_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
INVOCATIONS_FILE = PROJECT_ROOT / "state" / "_agent_invocations.jsonl"


def extract_agent_identity(payload):
    """hook payload から起動サブエージェントの識別子（名前/種別）を取り出す（純関数）。

    環境により Task/Agent ツールの input 形が異なるため複数フィールドを許容する:
    tool_input.subagent_type / tool_input.agent / 上位 agent_type。識別不能なら "unknown"。
    補助情報として description も記録する。
    """
    if not isinstance(payload, dict):
        return "unknown", ""
    ti = payload.get("tool_input")
    ti = ti if isinstance(ti, dict) else {}
    # 'name' は Task 以外で別用途のことがあるため含めない（誤った識別子の記録を防ぐ）。
    for key in ("subagent_type", "agent", "agent_type"):
        v = ti.get(key)
        if isinstance(v, str) and v:
            return v, str(ti.get("description", ""))[:200]
    top = payload.get("agent_type")
    if isinstance(top, str) and top:
        return top, str(ti.get("description", ""))[:200]
    return "unknown", str(ti.get("description", ""))[:200]


def _read_payload():
    try:
        if not sys.stdin.isatty():
            data = sys.stdin.read()
            if data:
                d = json.loads(data)
                if isinstance(d, dict):
                    return d
    except (json.JSONDecodeError, OSError, ValueError):
        pass
    # 環境変数フォールバック（テスト互換）
    out = {}
    tn = os.environ.get("TOOL_NAME")
    if tn:
        out["tool_name"] = tn
    s = os.environ.get("TOOL_INPUT", "")
    if s:
        try:
            out["tool_input"] = json.loads(s)
        except (json.JSONDecodeError, TypeError):
            pass
    return out


def main():
    try:
        payload = _read_payload()
        tool_name = payload.get("tool_name", "")
        if tool_name not in ("Task", "Agent"):
            return 0
        agent, desc = extract_agent_identity(payload)
        ti = payload.get("tool_input")
        ti = ti if isinstance(ti, dict) else {}
        launcher = payload.get("agent_type")
        record = {"agent": agent, "description": desc,
                  "by": launcher if isinstance(launcher, str) and launcher else "main",
                  "prompt": str(ti.get("prompt", ""))[:2000],
                  "at": datetime.now().isoformat(timespec="seconds")}
        INVOCATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(INVOCATIONS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 記録専用フックなので何があってもブロックしない
    return 0


if __name__ == "__main__":
    sys.exit(main())
