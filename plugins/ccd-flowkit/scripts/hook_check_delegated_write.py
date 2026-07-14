#!/usr/bin/env python
"""Claude Code PreToolUse Hook: 成果物のオーケストレーター直編集を禁止し委任を強制する。

成果物ツリー（docs/** ・ src/**）は designer / builder / 各 tester / consistency-checker 等の
サブエージェントが書くべき成果物である。オーケストレーター（メインセッション）が
これらを直接 Edit するとインライン実行問題（委任せず自己完結してしまう）を招くため、
本フックは PreToolUse(Edit|Write|MultiEdit) で、対象が**成果物ツリー**のとき、agent_type が無い書き込み
（=メインセッション/オーケストレーター）を block する。サブエージェント（agent_type 有り）は許可する。

成果物ツリーの正本は protected-paths.json（docs/{requirements,design,build,test,trace}/** ・ src/**）。
フォルダ集合を hook_check_protected_path と共有し（二重管理を避ける）、役割ガード（誰が＝本フック）と
フェーズガード（いつ＝protected-path のマーカー）が同じツリーを2軸で守る。委任専用 basename
（decisions.json 等）は、パス解決が効かない場合のロバスト fast-path として併存させる。

agent_type の値は環境依存で不確実なため『有無』のみで判定する（特定 agent 名に依存せず、正規の
サブエージェントを誤ブロックしない）。成果物ツリーは『agent_type 欠落＝オーケストレーター』を block する
（fail-closed。commit ゲートと同方針）。成果物ツリー外・想定外の例外は fail-open（exit 0）でフローを壊さない。
既知の限界: Bash 経由の書込（echo > / cp / tee 等）は matcher(Edit|Write|MultiEdit) の外で防げない
（CONTRIBUTING の『迂回禁止』ルールで予防）。
"""
import json
import os
import re
import sys
import traceback

# 成果物フォルダの正本（protected-paths.json）と照合ロジックを共有する。フォルダ定義を二重管理しない。
try:
    import hook_check_protected_path as _pp
except Exception:  # import 失敗時は basename fast-path のみで動作（fail-open 方針）
    _pp = None

# Windows(cp932)対策。stdin も含め UTF-8 化（hook 入力 JSON 中の日本語パスを化けさせない）。
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 委任専用成果物（basename で判定）。decisions.json は designer、*試験結果.md は各 tester、
# traceability.json/audit.log は consistency-checker が書く（オーケストレーター直編集を禁止）。
# 比較は小文字化して大小無視（Windows は DECISIONS.JSON 等を同一視するため素通りを防ぐ）。
DELEGATED_BASENAMES = {"decisions.json", "traceability.json", "audit.log"}
# basename 全体に完全一致させる（^...$）。`要件単体試験結果.md` のような派生名の前方一致誤検出を防ぐ。
TEST_RESULT_RE = re.compile(r"^(?:単体|結合|総合)試験結果\.md$", re.IGNORECASE)


def _basename(file_path):
    return file_path.replace("\\", "/").rstrip("/").split("/")[-1]


def is_delegated_only(file_path):
    """パスが委任専用成果物（decisions.json / traceability.json / audit.log / *試験結果.md）なら
    True（純関数・大小無視）。basename での fast-path（パス解決に依存しないロバスト判定）。"""
    if not isinstance(file_path, str) or not file_path:
        return False
    base = _basename(file_path).lower()
    return base in DELEGATED_BASENAMES or bool(TEST_RESULT_RE.match(base))


def is_protected_artifact(file_path):
    """パスが成果物ツリー（protected-paths.json のいずれかの rule）配下なら True（純関数）。

    フォルダ定義は hook_check_protected_path と共有する。_pp が無い/解決不能なら False（fast-path に委ねる）。
    """
    if _pp is None or not isinstance(file_path, str) or not file_path:
        return False
    try:
        norm = _pp.normalize_path(file_path)
        if not norm:
            return False
        rules = _pp.load_protected_rules()
        return _pp.find_matching_rule(norm, rules) is not None
    except Exception:
        return False


def is_artifact_path(file_path):
    """成果物（委任専用 basename または成果物ツリー配下）なら True。"""
    return is_delegated_only(file_path) or is_protected_artifact(file_path)


def is_orchestrator(payload):
    """payload に agent_type が無ければオーケストレーター（メインセッション）と判定する（純関数）。

    サブエージェント内の操作では payload に agent_type が立つ（hook_check_commit_agent と同方式）。
    """
    if not isinstance(payload, dict):
        return True
    at = payload.get("agent_type")
    return not (isinstance(at, str) and at.strip())


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


def evaluate(file_paths, payload):
    """成果物へのオーケストレーター書込なら block メッセージを返す（純関数・テスト可能）。

    サブエージェント（agent_type 有り）は常に許可。成果物ツリー外のパスも許可。
    """
    if not is_orchestrator(payload):
        return None
    for fp in file_paths:
        if is_artifact_path(fp):
            base = _basename(fp)
            return (
                f"[hook] 委任ガード: {base} は成果物ツリー（docs/** ・ src/**）でオーケストレーターが\n"
                f"  直接編集できません。担当サブエージェント（設計=designer、構築=builder、試験結果=各 tester、\n"
                f"  決定台帳/トレース=consistency-checker、レビュー記録=各 reviewer 等）に委任して書かせてください。\n"
            )
    return None


def _read_hook_input():
    """hook 入力を読み取り (tool_name, tool_input, payload) を返す（stdin JSON 優先）。"""
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
    # 環境変数フォールバック（テスト互換）
    payload = {}
    tn = os.environ.get("TOOL_NAME", "")
    if tn:
        payload["tool_name"] = tn
    ti = {}
    s = os.environ.get("TOOL_INPUT", "")
    if s:
        try:
            parsed = json.loads(s)
            if isinstance(parsed, dict):
                ti = parsed
                payload["tool_input"] = parsed
        except (json.JSONDecodeError, TypeError):
            pass
    # 環境変数フォールバックでは agent_type を載せない（commit ゲートと一貫。env で agent_type を
    # 偶発設定しても委任ガードがすり抜けないよう、フォールバックは常に『欠落＝オーケストレーター』）。
    return tn, ti, payload


def main():
    try:
        tool_name, tool_input, payload = _read_hook_input()
        if tool_name not in ("Edit", "Write", "MultiEdit"):
            return 0
        file_paths = extract_file_paths(tool_input, tool_name)
        if not file_paths:
            return 0
        msg = evaluate(file_paths, payload)
        if msg:
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
