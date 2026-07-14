#!/usr/bin/env python
"""Claude Code PreToolUse Hook: サブエージェントによる子サブ起動を一律禁止する（フラット化不変条件）。

フラット化不変条件: **サブエージェント（agent_type あり）は他のサブエージェントを起動しない**。
ノード担当も観点サブも例外なく main の専権として起動する。
main（agent_type なし）による Task|Agent 起動は制限なし。

背景（設計上の理由）:
集約役サブが観点サブを Task 非同期で起動する構造は、子完了通知が親でなく main に届く
Task モデルと階層構造が整合せず、子タスク孤児化や集約役の早期終了→反復が起きうる。
また観点サブが逆に集約役を Task 起動するとフロー外の停止チェーンと block サイクルを
生む。これらを構造的に防ぐため、launcher が -reviewer か否か / target がノード担当か
観点サブか / run_in_background 真偽に関係なく、サブが Task|Agent を呼んだら常に block する。

想定外の例外は fail-open（exit 0）でフローを壊さない。
"""
import json
import os
import sys
import traceback

# Windows(cp932)対策。
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _bare(name):
    """素名に正規化する（プラグイン接頭辞除去＋小文字化）。

    小文字化は必須: Windows のエージェント解決はファイルシステム由来で大小無視のため、
    表記揺れによるすり抜けを防ぐ（hook_check_advance_actor の ADVANCE_RE と同じ理由）。"""
    return name.strip().split(":")[-1].strip().lower() if isinstance(name, str) else ""


def _target_agent(tool_input):
    """起動対象エージェント名を tool_input から取り出す（hook_log_agent.py と同じ優先順）。"""
    if not isinstance(tool_input, dict):
        return ""
    for key in ("subagent_type", "agent", "agent_type"):
        v = tool_input.get(key)
        if isinstance(v, str) and v.strip():
            return _bare(v)
    return ""


def evaluate(payload):
    """サブエージェントによる Task|Agent 起動なら一律 block（純関数）。

    フラット化不変条件: launcher（agent_type）が非空＝サブエージェント なら、
    Task|Agent 起動を無条件 deny する。main（launcher 空）は制限なし。
    launcher/target ともに素名で判定する（表記揺れによるすり抜け防止）。
    """
    if not isinstance(payload, dict):
        return None
    launcher = _bare(payload.get("agent_type") or "")
    if not launcher:
        return None  # main は許可
    ti = payload.get("tool_input")
    ti = ti if isinstance(ti, dict) else {}
    target = _target_agent(ti)
    if not target:
        return None  # 起動対象を特定できないケースは fail-open
    return (
        f"[hook] 起動ガード: サブエージェント（{launcher}）は他のサブエージェント"
        f"（{target}）を起動できません。\n"
        f"  フラット化モデルではサブは他サブを起動しない不変条件です。\n"
        f"  観点サブ・ノード担当いずれも起動できるのはディスパッチャ（main）のみです。\n"
        f"  他エージェントへの依頼が必要なら、完了報告にその旨を記して終了してください\n"
        f"  （main が委任します。バックグラウンド起動・逆委任・サブから別サブへの並列一括起動のいずれも同様）。\n"
    )


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
        if payload.get("tool_name") not in ("Task", "Agent"):
            return 0
        msg = evaluate(payload)
        if msg:
            sys.stderr.write(msg)
            return 2
        return 0
    except Exception:
        try:
            traceback.print_exc(file=sys.stderr)
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
