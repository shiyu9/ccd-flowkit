#!/usr/bin/env python
"""Claude Code PreToolUse Hook: 状態機械ファイル（state/_*）と**フレームワーク本体**への直書きを全面禁止する。

サブエージェントが advance 失敗（${CLAUDE_PLUGIN_ROOT} 未展開等）の回復として state/_flow_state.json
を Write で手書きすると、遷移履歴の前半が消失するリスクがある。flow-state.md は「state の生ファイル
を直接 Read/Write しない」と定めるが、規約だけでは止まらないため機械化する（_protected_paths.json
の既存ガードは docs/**・src/** のみを対象とし、state/** は保護対象外）。

本フックは PreToolUse(Edit|Write|MultiEdit) で、対象が **state/ ディレクトリ直下の既知の
ガバナンスファイル**（GOVERNANCE_BASENAMES の完全一致）のとき、**メイン/サブを問わず** block する
（委任ガードと異なり agent_type の有無を見ない＝誰であっても手書き禁止）。
「アンダースコア始まり全部」ではなく既知名の完全一致に絞るのは、開発対象プロダクトが
`src/state/__init__.py` のような state パッケージを持つ場合の誤ブロックを避けるため
（ガバナンスファイルを増やしたら GOVERNANCE_BASENAMES に追記する）。正当な更新経路はスクリプトのみ:
  - state 遷移: manage_flow_state.py init/advance
  - フェーズマーカー: manage_active_skill.py acquire/release
アンダースコア無しの state/ ファイル（lessons_extract_result.json・lessons_proposal.md 等の
中間ファイル）は対象外（lessons-distiller が Write する正当フロー）。

あわせて**フレームワーク保護**: 実行中のプラグイン本体（本フック自身の設置場所から導出した
plugin ルート配下＝scripts/・agents/・references/・hooks/ 等）への Write/Edit/MultiEdit を
誰であっても block する。フレームワーク側の規約・スキャナに矛盾を見つけたエージェントが
自身でスナップショットを直接上書きする越権への対策（エスカレーションへ強制誘導するため機械化）。

想定外の例外は fail-open（exit 0）でフローを壊さない。既知の限界: Bash 経由の書込
（echo > / python -c 等）は matcher 外（迂回禁止ルール＋advance actor ガードで予防）。
"""
import json
import os
import pathlib
import posixpath
import sys
import traceback
from datetime import datetime

# Windows(cp932)対策。stdin も含め UTF-8 化（hook 入力 JSON 中の日本語パスを化けさせない）。
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


# state/ 直下のガバナンスファイル（完全一致・小文字比較）。増やしたらここに追記する。
GOVERNANCE_BASENAMES = {
    "_flow_state.json", "_agent_invocations.jsonl", "_advance_audit.jsonl",
    "_active_skills.json", "_continue_guard.json", "_protected_paths.json",
    "_sequence.json",  # 旧世代の正本（撤去済みだが手書き再生成を防ぐ）
}

# 実行中のプラグイン本体のルート（本フックは <plugin>/scripts/ に置かれる）。
# スナップショット実行時はスナップショットのルートになる＝実行中フレームワークを常に保護できる。
PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent.parent


def is_framework_path(file_path, plugin_root=None):
    """パスが実行中プラグイン本体の配下なら True（フレームワーク保護）。

    相対パスは cwd 基準で絶対化してから比較する（Windows の大小無視に合わせ casefold）。
    解決不能な場合は False（fail-open。誤ブロックでフローを壊さない）。
    """
    if not isinstance(file_path, str) or not file_path:
        return False
    root = pathlib.Path(plugin_root) if plugin_root else PLUGIN_ROOT
    try:
        ap = pathlib.Path(os.path.abspath(file_path))
        return str(ap).casefold().startswith(str(root).casefold() + os.sep)
    except (OSError, ValueError):
        return False


def is_state_governance_path(file_path):
    """パスが state/ 直下の既知ガバナンスファイルなら True（純関数・大小無視）。

    パス解決に依存せず「親ディレクトリ名が state かつ basename が既知名に完全一致」で判定する
    （相対/絶対/Windows 区切りいずれでも同じ結果。誤 cwd で他リポジトリに向いた
    state/_* への書込も同様にブロックできる）。basename を完全一致に絞ることで、
    プロダクト側の `src/state/__init__.py` 等を誤ブロックしない。
    """
    if not isinstance(file_path, str) or not file_path:
        return False
    # normpath で "." を畳み ".." を解決してから判定する（"state/./_flow_state.json" や
    # "state/x/../_flow_state.json" のようなセグメント細工でのガード素通りを防ぐ。
    # ファイルシステムには触れないため誤 cwd でも判定できる）。
    norm = posixpath.normpath(file_path.replace("\\", "/"))
    parts = [p for p in norm.split("/") if p and p != "."]
    if len(parts) < 2:
        return False
    parent, base = parts[-2].lower(), parts[-1].lower()
    return parent == "state" and base in GOVERNANCE_BASENAMES


def is_review_results_path(file_path):
    """パスが state/review_results/ 配下の .jsonl なら True（純関数・大小無視）。

    フラット化モデルで、観点サブの結果保存は main 専用。integrator や
    観点サブがここへ書き込むと順序制御（main が全観点完了後に integrator を起動する）
    が破れ、rerun 上限判定の対象記録を偽造できる。Write/Edit/MultiEdit 経路が
    Bash 側 (hook_check_advance_actor.REVIEW_RESULTS_WRITE_RE) と対称になるように配置する。
    """
    if not isinstance(file_path, str) or not file_path:
        return False
    norm = posixpath.normpath(file_path.replace("\\", "/"))
    parts = [p for p in norm.split("/") if p and p != "."]
    if len(parts) < 3 or not parts[-1].lower().endswith(".jsonl"):
        return False
    # state/review_results/<basename>.jsonl の直下配置のみを対象（サブディレクトリを挟まない）
    return (parts[-3].lower() == "state"
            and parts[-2].lower() == "review_results")


def _normalize_agent(agent_type):
    """agent_type を素のエージェント名に正規化する（'ccd-flowkit:designer' → 'designer'）。

    hook_check_advance_actor.normalize_agent と同ロジック（cross-import を避けて内製）。
    """
    if not isinstance(agent_type, str) or not agent_type.strip():
        return None
    return agent_type.strip().split(":")[-1].strip() or None


def extract_file_paths(tool_input, tool_name):
    """tool_input から対象ファイルパスのリストを取り出す（Edit/Write/MultiEdit）。"""
    if not isinstance(tool_input, dict):
        return []
    paths = []
    fp = tool_input.get("file_path")
    if isinstance(fp, str) and fp:
        paths.append(fp)
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits")
        if isinstance(edits, list):
            for e in edits:
                if isinstance(e, dict):
                    efp = e.get("file_path")
                    if isinstance(efp, str) and efp:
                        paths.append(efp)
    return paths


def evaluate(file_paths):
    """state ガバナンスファイル／フレームワーク本体への書込なら block メッセージを返す（純関数）。

    agent_type を見ない＝オーケストレーター/サブエージェントの区別なく一律禁止。
    """
    for fp in file_paths:
        if is_state_governance_path(fp):
            base = fp.replace("\\", "/").rstrip("/").split("/")[-1]
            return (
                f"[hook] state ガード: {base} は状態機械のガバナンスファイルで、誰であっても\n"
                f"  Write/Edit で直接書けません（手書きは遷移履歴を破壊します）。\n"
                f"  state の前進は `python <plugin>/scripts/manage_flow_state.py advance <node> <outcome>`、\n"
                f"  マーカーは manage_active_skill.py を使ってください。advance がパスエラーで失敗する場合は\n"
                f"  state を手書きせず、失敗をそのまま報告して完了してください（ディスパッチャが再委任します）。\n"
            )
        if is_framework_path(fp):
            return (
                f"[hook] フレームワーク保護: {fp} は実行中の ccd-flowkit 本体（プラグイン配下）で、\n"
                f"  誰であっても Write/Edit できません。フレームワークの不具合・規約とスキャナの矛盾を\n"
                f"  見つけた場合は、自分で直さず**「要ユーザー判断」としてエスカレーション**してください\n"
                f"  （escalation-conventions.md。ディスパッチャが AskUserQuestion でユーザーに諮ります）。\n"
            )
    return None


def evaluate_review_results(file_paths, actor):
    """state/review_results/*.jsonl への Write/Edit/MultiEdit は main 専用（純関数）。

    hook_check_advance_actor.evaluate_rerun_actor（Bash側）と対称の Write 側ガード。
    Bash 側のみ保護されていると Write 経路が素通しで観点結果を偽造できるため、
    両経路を同じ主体制約で塞ぐ。actor is None（main）は許可、サブエージェントは deny。
    """
    if actor is None:
        return None
    for fp in file_paths:
        if is_review_results_path(fp):
            return (
                f"[hook] review_results ガード: {fp} は main（ディスパッチャ）専用の\n"
                f"  観点結果保存ファイルです（実行者: {actor}）。\n"
                f"  フラット化モデルでは main が観点サブの結果を Bash append で\n"
                f"  保存し、integrator が state を読んで統合します。integrator/観点サブが\n"
                f"  Write/Edit で書き込むと順序制御が破れ rerun 上限判定を偽造できます。\n"
                f"  観点結果は Task の戻り値として main に返し、main に append させてください。\n"
            )
    return None



def _read_hook_input():
    """hook 入力を読み取り (tool_name, tool_input, payload) を返す（stdin JSON 優先・env フォールバック）。"""
    try:
        if not sys.stdin.isatty():
            data = sys.stdin.read()
            if data:
                payload = json.loads(data)
                if isinstance(payload, dict):
                    tn = payload.get("tool_name", "")
                    ti = payload.get("tool_input", {})
                    if not isinstance(ti, dict):
                        ti = {}
                    if tn:
                        return tn, ti, payload
    except (json.JSONDecodeError, OSError, ValueError):
        pass
    tn = os.environ.get("TOOL_NAME", "")
    ti = {}
    s = os.environ.get("TOOL_INPUT", "")
    if s:
        try:
            parsed = json.loads(s)
            if isinstance(parsed, dict):
                ti = parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return tn, ti, {}


def _log_deny(payload, file_paths):
    """deny を state/_guard_log.jsonl へ追記する（best-effort・失敗は無視）。

    発火記録が無いと『発火機会なし』と『検知漏れ』を後から区別できない。
    """
    try:
        root = pathlib.Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
        at = payload.get("agent_type") if isinstance(payload, dict) else None
        rec = {"at": datetime.now().isoformat(timespec="seconds"),
               "hook": "state_write",
               "actor": at if isinstance(at, str) and at else "main",
               "paths": [p for p in file_paths
                         if (is_state_governance_path(p) or is_framework_path(p)
                             or is_review_results_path(p))],
               "verdict": "deny"}
        log = root / "state" / "_guard_log.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main():
    try:
        tool_name, tool_input, payload = _read_hook_input()
        if tool_name not in ("Edit", "Write", "MultiEdit"):
            return 0
        file_paths = extract_file_paths(tool_input, tool_name)
        if not file_paths:
            return 0
        actor = _normalize_agent(
            payload.get("agent_type") if isinstance(payload, dict) else None)
        msg = evaluate(file_paths) or evaluate_review_results(file_paths, actor)
        if msg:
            _log_deny(payload, file_paths)
            sys.stderr.write(msg)
            return 2
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
