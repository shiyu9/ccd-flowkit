#!/usr/bin/env python
"""Claude Code SubagentStop Hook: ノード担当サブエージェントの advance 漏れを機械検知して停止を差し戻す。

ノード担当が advance を実行せずに完了すると、ディスパッチャの wait タイムアウト→集約役ごと
再起動、の反復でフローが長時間停滞しうる。エージェント定義への advance 手順明記は
「LLMへのお願い」であり、本フックはそれを機械側から二重担保する（設計方針 P2=機械化の最大化）。

判定（全て決定論・LLM判断なし）:
  - 停止したサブエージェントの素名が _flow_state.json の current ノード担当と一致し、
    かつ advance が未成立（current がそのノードのまま）なら {"decision":"block"} で停止を
    差し戻し、additionalContext で advance の実行を指示する。
  - ノード担当外（観点サブ・committer 等）は対象外＝素通し。
  - 「直前の遷移で current に入ったのが自分自身」（REQ_PLAN→REQ_APPROVE の project-planner 型）
    は advance 成立済みなので許可。
  - stop_hook_active=true（既に本フックの差し戻しで続行した後の停止）は許可＝差し戻しは
    1停止チェーンにつき1回まで（無限ループ防止）。
  - 同一 agent_id への block は累計 BLOCK_CAP_PER_AGENT 回まで
    （block→stop_hook_active許可→新チェーンで再block…の空振りサイクル対策。
    上限超過は allow で強制脱出しディスパッチャの再委任フォールバックへ）。
  - 最終メッセージにエスカレーション正準書式（「要ユーザー判断」＋「該当基準」の複合一致）を
    含む停止は正当（ディスパッチャが AskUserQuestion で仲介する経路）なので許可。
    【既知の限界】このテキストは停止したエージェント自身の出力であり、意図的に書式を
    偽装すれば block を回避できる。受容の根拠: (1)迂回の実害は本フック導入前のフォールバック
    （wait タイムアウト→再委任）への劣化に留まる (2)偽エスカレーションはディスパッチャの
    AskUserQuestion 経由でユーザーに可視化される (3)監査の reason:"escalation" と実際の
    AskUserQuestion 有無を評価時に突合検証できる。構造的解決（専用エスカレーションツール等の
    ランタイム信頼シグナル）は別課題。
  - state 読取不能・担当不明などの不確実ケースは fail-open（exit 0）。

対象評価（担当一致）の判定は verdict/reason 付きで state/_advance_audit.jsonl に
op:"subagent_stop" として追記する（発火機会なしと検知漏れをログから区別する既存方針の踏襲）。
担当外の停止は記録しない（観点サブ並列で record が氾濫するため）。

実ペイロードの注意（公式ドキュメントと一部相違することを実測で確認済み）:
  - last_assistant_message は文字列で届く（ドキュメントの content 配列形式にも防御的に対応）
  - exit_code は実際には載らない（載っていた場合のみ異常終了として block を控える）
  - agent_id は同一 Task 呼び出しの複数回停止で同値＝呼び出し単位の識別子
"""
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

# Windows(cp932)対策。stdin も含め UTF-8 化。
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ノード担当表・遷移表の正本は manage_flow_state.py、素名正規化と担当解決は
# hook_check_advance_actor.py（NONE_AGENT_EXECUTORS 込み）。二重管理しない。
try:
    import manage_flow_state as _fs
except Exception:
    _fs = None
try:
    from hook_check_advance_actor import expected_executor, normalize_agent
except Exception:
    expected_executor = normalize_agent = None

PROJECT_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
STATE_FILE = PROJECT_ROOT / "state" / "_flow_state.json"
AUDIT_FILE = PROJECT_ROOT / "state" / "_advance_audit.jsonl"
INVOCATIONS_FILE = PROJECT_ROOT / "state" / "_agent_invocations.jsonl"

# escalation-conventions.md「要ユーザー判断」の書式（正当な advance なし停止の正準マーカー）。
# 部分文字列の偶発一致（「要ユーザー判断は不要でした」等）で素通りしないよう、
# 書式の必須要素「該当基準」（基準番号の明示）との複合一致を要求する。
ESCALATION_MARKER = "要ユーザー判断"
ESCALATION_CRITERIA = "該当基準"

# hook_log_agent.py の記録が秒精度のため許容する誤差（時刻突合の丸めズレへの余裕）。
LAUNCH_TS_TOLERANCE_SECONDS = 1.0

# 同一 agent_id への block の累計上限。
# 「短い照会ツール呼び→停止→block→再開→また照会→停止」のようなサイクルが起きた場合、
# stop_hook_active が停止チェーン単位でリセットされるため block→許可→block…のサイクルを
# 繰り返しうる。フラット化モデルによりサブ→サブ起動が禁止された現在は集約役サイクリング
# 構造は消滅しているが、integrator も advance 漏れ得る（実装漏れやプロンプト解釈揺れ）
# ため、空振り差戻しに対する保険として本上限を維持する。
# 同一呼び出しへの差し戻しは累計2回で打ち切り、以降は allow で強制脱出させて
# ディスパッチャの再委任フォールバックに委ねる。
# 値の根拠: 1停止チェーンにつき block は1回（stop_hook_active）× 正当に新チェーンが
# 生じるのは高々1回（block→advance失敗等での仕切り直し）= 2。3回目以降の block が
# 必要な状況は同一呼び出し内の空振り反復であり、差し戻しでは直らない。
BLOCK_CAP_PER_AGENT = 2


def last_message_text(payload):
    """last_assistant_message からテキストを取り出す（実測=文字列／ドキュメント=オブジェクトの両対応。
    content 配列が直接届く形にも防御的に対応する）。"""
    lam = payload.get("last_assistant_message") if isinstance(payload, dict) else None
    if isinstance(lam, str):
        return lam
    if isinstance(lam, dict):
        lam = lam.get("content")
    if isinstance(lam, list):
        parts = []
        for c in lam:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
        return "\n".join(parts)
    return ""


def is_escalation(text):
    """エスカレーション正準書式の停止か（マーカー＋基準番号明示の複合一致）。

    偶発一致で block を控えても、差し戻しの additionalContext が正書式での再提出を
    指示するため自己回復する（誤 block のコストは1ターン）。"""
    return ESCALATION_MARKER in text and ESCALATION_CRITERIA in text


def last_launch_epoch(actor, path=None):
    """_agent_invocations.jsonl からこの actor の最終起動時刻（epoch秒）を返す。不明なら None。"""
    try:
        from datetime import datetime as _dt
        latest = None
        with open(path or INVOCATIONS_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(rec, dict):
                    continue
                if normalize_agent and normalize_agent(rec.get("agent")) == actor:
                    try:
                        ts = _dt.fromisoformat(rec["at"]).timestamp()
                    except (KeyError, TypeError, ValueError):
                        continue
                    latest = ts if latest is None else max(latest, ts)
        return latest
    except OSError:
        return None


def count_prior_blocks(agent_id, path=None):
    """_advance_audit.jsonl からこの agent_id への過去の block 回数を数える。読めなければ 0。"""
    if not agent_id:
        return 0
    n = 0
    try:
        with open(path or AUDIT_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if (isinstance(rec, dict) and rec.get("op") == "subagent_stop"
                        and rec.get("verdict") == "block" and rec.get("agent_id") == agent_id):
                    n += 1
    except OSError:
        return 0
    return n


def advanced_in_this_invocation(state_mtime, launch_epoch):
    """「advance で current に入った」のがこの呼び出し自身か（純関数・fail-open）。

    _flow_state.json は advance/init 以外に書かれない（state ガバナンス）ため、
    state の mtime が actor の最終起動時刻より新しければ、この呼び出し中に advance が
    成立している。古ければ「advance 済みノードへの再委任後、何も advance せず停止」
    （REQ_APPROVE→REQ_WRITE 等、同一エージェントが連続担当するノードの検知漏れ対策）。
    どちらかの時刻が取れなければ True（fail-open＝従来どおり許可）。"""
    if state_mtime is None or launch_epoch is None:
        return True
    return state_mtime >= launch_epoch - LAUNCH_TS_TOLERANCE_SECONDS


def evaluate(payload, state, state_mtime=None, launch_epoch=None, prior_blocks=0):
    """SubagentStop ペイロードと state を突合し判定を返す（純関数・テスト可能）。

    戻り値: None（対象外＝担当不一致や不確実ケース）または (verdict, node, actor, reason)。
    verdict は "allow" | "block"。state_mtime / launch_epoch は再委任判別用、
    prior_blocks はこの agent_id への過去の block 回数（main が実ファイルから解決。
    不明値は fail-open 側の既定に倒す）。
    """
    if expected_executor is None or normalize_agent is None:
        return None
    if not isinstance(payload, dict) or not isinstance(state, dict):
        return None
    actor = normalize_agent(payload.get("agent_type"))
    if actor is None:
        return None
    current = state.get("current")
    expected = expected_executor(current)
    if not expected or actor != expected:
        return None
    # 直前の遷移で current に入ったのが自分自身で、かつその advance がこの呼び出し中に
    # 成立したなら正当な停止（例: project-planner が REQ_PLAN done → REQ_APPROVE
    # （担当 project-planner）へ進めた直後の停止）。mtime 判別が無いと、advance 済み
    # ノードへ再委任された同一エージェントが何もせず停止しても素通りしてしまう。
    history = state.get("history") or []
    last = history[-1] if history and isinstance(history[-1], dict) else None
    if (last and last.get("to") == current and expected_executor(last.get("node")) == actor
            and advanced_in_this_invocation(state_mtime, launch_epoch)):
        return ("allow", current, actor, "advanced-into-current")
    # フラット化モデルではサブ→サブ起動が禁止されているため、担当エージェントが
    # 「実行中の子タスク」を持って停止するケースは構造的に発生しない。
    if payload.get("stop_hook_active"):
        return ("allow", current, actor, "stop-hook-active")
    ec = payload.get("exit_code")
    if isinstance(ec, int) and ec != 0:
        return ("allow", current, actor, "abnormal-exit")
    if is_escalation(last_message_text(payload)):
        return ("allow", current, actor, "escalation")
    if prior_blocks >= BLOCK_CAP_PER_AGENT:
        # サイクリングの打ち切り: これ以上の差し戻しは空振りとみなし
        # ディスパッチャの再委任フォールバックへ委ねる。
        return ("allow", current, actor, "block-cap-exceeded")
    return ("block", current, actor, "advance-missing")


def block_output(node):
    """block 判定時に stdout へ出す JSON（reason はディスパッチャ向け、additionalContext は当人向け）。"""
    outcomes = ""
    try:
        vocab = sorted((_fs.TRANSITIONS.get(node) or {}).keys()) if _fs else []
        if vocab:
            outcomes = f"（outcome 候補: {' / '.join(vocab)}）"
    except Exception:
        pass
    return {
        "decision": "block",
        "reason": f"[hook] advance ガード: ノード {node} の担当が advance 未実行のまま停止したため差し戻しました。",
        "hookSpecificOutput": {
            "hookEventName": "SubagentStop",
            "additionalContext": (
                f"[hook] 担当ノード {node} の advance が未実行です。作業が完了しているなら "
                f"manage_flow_state.py（${{CLAUDE_PLUGIN_ROOT}}/scripts/ 配下）で "
                f"`advance {node} <outcome>` を実行してから完了してください{outcomes}。"
                f"ユーザー判断が必要で停止する場合は、エスカレーション規約の書式"
                f"（「{ESCALATION_MARKER}」＋該当基準・状況・不明点・影響・推奨）で返してください。"
            ),
        },
    }


def audit(node, actor, agent_id, verdict, reason):
    """判定を state/_advance_audit.jsonl へ追記する（best-effort。記録失敗でブロックしない）。"""
    try:
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "op": "subagent_stop", "node": node, "actor": actor,
                "agent_id": agent_id, "verdict": verdict, "reason": reason,
                "at": datetime.now().isoformat(timespec="seconds"),
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_state(state_file=None):
    """_flow_state.json を読む。読めなければ None（fail-open の判断材料）。"""
    try:
        with open(state_file or STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def main():
    try:
        data = sys.stdin.read()
        if not data:
            return 0
        payload = json.loads(data)
        if not isinstance(payload, dict) or payload.get("hook_event_name") != "SubagentStop":
            return 0
        if expected_executor is None or normalize_agent is None:
            # fail-open だが無音にしない（『発火機会なし』と『import失敗による無効化』の区別）
            sys.stderr.write("[hook_enforce_advance] モジュール import 失敗のため検査せず素通し\n")
            return 0
        state = load_state()
        if state is None:
            return 0
        # 担当外（観点サブ・committer 等）の停止は大半を占めるため、jsonl 全走査
        # （last_launch_epoch / count_prior_blocks）の前に担当一致で事前フィルタする。
        actor = normalize_agent(payload.get("agent_type"))
        if actor is None or actor != expected_executor(state.get("current")):
            return 0
        try:
            state_mtime = os.path.getmtime(STATE_FILE)
        except OSError:
            state_mtime = None
        launch_epoch = last_launch_epoch(actor)
        prior_blocks = count_prior_blocks(payload.get("agent_id"))
        result = evaluate(payload, state, state_mtime=state_mtime, launch_epoch=launch_epoch,
                          prior_blocks=prior_blocks)
        if result is None:
            return 0
        verdict, node, actor, reason = result
        audit(node, actor, payload.get("agent_id"), verdict, reason)
        if verdict == "block":
            print(json.dumps(block_output(node), ensure_ascii=False))
        return 0
    except Exception:
        # フェイルセーフ: 予期せぬ例外でブロックしない
        try:
            traceback.print_exc(file=sys.stderr)
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
