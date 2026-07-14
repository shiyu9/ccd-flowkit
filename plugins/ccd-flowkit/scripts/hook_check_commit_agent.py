#!/usr/bin/env python3
"""Claude Code PreToolUse Hook: agent_type ゲート — git commit の発行主体を判定する。

committer サブエージェント経由の git commit のみを許可し、それ以外をすべて block する。
メインセッション（リーダー）からの直接 commit は agent_type が無いため block される。

検査内容:
1. Bash ツール経由で `git commit` が呼ばれているか判定（それ以外は素通し）
2. agent_type が committer を示すか検査（fail-closed）
3. 一致したときだけ allow（exit 0）、欠落 / 不一致 / 型不正は block（exit 2）
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import traceback
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
# 利用側プロジェクトの state/ を参照する。plugin install 環境では
# `__file__` の親から辿ると plugin キャッシュ root を指してしまい使えない。
# CLAUDE_PROJECT_DIR が立っていれば優先（hook context で安定）、なければ cwd。
PROJECT_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))


def _project_root() -> Path:
    """テスト時に patch しやすいよう関数化。`PROJECT_ROOT` 変数を参照する。"""
    return PROJECT_ROOT


# ----- コマンドトークン判定のための前処理 -----

# heredoc body を除去して shlex トークン化を安全にする（_is_real_git_commit_command 用）
_HEREDOC_BODY_STRIP_RE = re.compile(
    r"\$\(\s*cat\s+<<\s*'?(?P<tag>[A-Za-z_][A-Za-z0-9_]*)'?\s*\n"
    r".*\n[ \t]*(?P=tag)[ \t]*(?:\n|\))",
    re.DOTALL,
)


def _normalize_newlines(text: str) -> str:
    """CRLF および孤立 CR を LF に正規化する（2 段 replace）。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _strip_heredoc_bodies(command: str) -> str:
    """heredoc 構文の body 部分を除去する（トークン検証用前処理）。"""
    if not isinstance(command, str):
        return command
    return _HEREDOC_BODY_STRIP_RE.sub(r"$(@HEREDOC@)", command)


def _is_git_commit_command(command: str) -> bool:
    """対象コマンドが `git commit` をトークン境界で含むか判定（早期リターン用）。

    引用符内の偶発一致（例: `gh pr create --body "...git commit..."`）は
    fast-path のためのみであり、後段の shlex tokenize による厳密判定で
    最終的な誤検出を防ぐ [E_A5]。
    """
    if not isinstance(command, str):
        return False
    # fast-path: 'commit' という語を含むか（過剰検出可。後段の
    # _is_real_git_commit_command で厳密判定する）。'git -c x commit' のように
    # git と commit が隣接しない形も後段で拾えるよう、ここは緩く通す。
    return bool(re.search(r"\bcommit\b", command))


# ----- agent_type ゲート -----

def _safe_agent_type(agent_type: str) -> str:
    """agent_type をファイル名に使える形にサニタイズする。

    hook_subagent_postmortem.py と同一ロジック（移植）[E_A4]。
    Windows / POSIX 双方で安全な文字（A-Z, a-z, 0-9, _, ., -）以外を `_` に置換する。
    特に colon は Windows NTFS Alternate Data Stream 区切りで 0-byte 化の原因になる。

    例: "cc-flowkit-dev:committer" -> "cc-flowkit-dev_committer"

    引数は str 型のみを受け取る（isinstance チェックは呼び出し元 _validate_committer_gate が担当）。
    空文字は "unknown_agent" を返す。
    結果が ".." または "." のみの場合も "unknown_agent" を返す（path traversal 防止）。
    """
    if not agent_type:
        return "unknown_agent"
    sanitized = re.sub(r"[^A-Za-z0-9_.-]", "_", agent_type)
    if sanitized in ("..", ".") or sanitized.startswith(".."):
        return "unknown_agent"
    return sanitized


def _validate_committer_gate(hook_input: dict) -> str | None:
    """agent_type が committer サブエージェントを示すか判定する。

    allow=None / block=エラーメッセージ。

    判定の不変条件:
    1. fail-closed: agent_type 欠落 / 型不正（非 str, null）は block。これにより
       メインセッション（agent_type が無い）からの直接 commit は必ず block される。
    2. サニタイズ後が "committer" または "*_committer" のとき allow。プラグイン接頭辞
       （例 "ccd-flowkit_committer"）は環境依存のため末尾一致で吸収する。
    3. それ以外の agent は block。
    注: 実際の agent_type 値が判明したら（rp-test 等で観測）完全一致へ厳格化してよい。
    """
    raw = hook_input.get("agent_type")
    # 型不正ガード（非 str / null も含む）。メインからの直接 commit はここで block。
    if not isinstance(raw, str) or not raw:
        return "git commit は committer サブエージェント経由でのみ実行できます（メインからの直接コミットは不可）"
    sanitized = _safe_agent_type(raw)
    if sanitized != "committer" and not sanitized.endswith("_committer"):
        return f"git commit は committer 専用です（agent_type={raw!r} は不許可）"
    return None  # allow


# git のグローバルオプションのうち、次のトークンを値として取るもの
_GIT_VALUE_OPTS = {
    "-c", "-C", "--git-dir", "--work-tree", "--namespace",
    "--super-prefix", "--exec-path",
}


def _is_git_executable(token: str) -> bool:
    """トークンが git 実行体か（'git' / '/usr/bin/git' / 'git.exe' 等）。"""
    if not isinstance(token, str) or not token:
        return False
    name = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name in ("git", "git.exe")


def _git_subcommand_is_commit(tokens: list) -> bool:
    """トークン列に「git ... commit」（間にグローバルオプションを挟む形を含む）が
    あるか判定する。git の後ろを走査し、グローバルオプション（および値を取る
    オプションの値）を読み飛ばした最初の非オプション・トークンが 'commit' なら True。

    `git -c x=y commit` / `/usr/bin/git commit` / `git -C path commit` /
    `git --no-pager commit` を捕捉する。
    限界: alias / 変数展開 / `git$IFS commit` のようなシェル間接化は文字列照合では
    見抜けない（run スキルの迂回禁止ルールで予防する）。
    """
    n = len(tokens)
    i = 0
    while i < n:
        if _is_git_executable(tokens[i]):
            j = i + 1
            while j < n:
                t = tokens[j]
                if t in _GIT_VALUE_OPTS:
                    j += 2  # オプションとその値をスキップ
                    continue
                if t.startswith("-"):
                    j += 1  # 単独フラグ / '--opt=value' 形式
                    continue
                if t == "commit":
                    return True
                break  # git の最初のサブコマンドが commit でない
        i += 1
    return False


def _is_real_git_commit_command(command: str) -> bool:
    """command が実際に git の commit サブコマンドを呼ぶか厳密判定する。

    引用符内の `git commit` 偶発一致（例: `gh pr create --body "...git commit..."`）は
    トークン境界判定で素通しにする。`git -c ... commit` のようにグローバルオプションが
    挟まる形も検出する（_git_subcommand_is_commit）。
    """
    if not isinstance(command, str):
        return False
    command = _normalize_newlines(command)
    command_for_tokens = _strip_heredoc_bodies(command)
    try:
        tokens = shlex.split(command_for_tokens, posix=True)
    except ValueError:
        # shlex 失敗: fallback として粗い regex で判定（過剰検出側に倒す）
        return bool(re.search(r'\bgit\b.*\bcommit\b', command, re.DOTALL))
    return _git_subcommand_is_commit(tokens)


# ----- エントリポイント -----

def _emit_block(message: str) -> int:
    """block を返す。公式仕様: exit code 2 + stderr 出力。

    https://code.claude.com/docs/en/hooks 参照。
    exit code 1 は ignore されるため必ず 2 を返す。
    """
    # Windows 環境で stderr エンコードが CP932 等になる場合に UTF-8 を明示する。
    # reconfigure 不可環境（StringIO 差し替え等で AttributeError / detach 済みで ValueError）
    # は例外を握りつぶして現状維持し、stderr write を継続する。
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    sys.stderr.write("[hook] commit-msg-validator (agent_type gate): コミットを拒否しました\n")
    sys.stderr.write(message + "\n")
    sys.stderr.write(
        "\nコミットは /ccd-flowkit:commit から committer 経由で行ってください\n"
    )
    return 2


def _read_hook_input() -> tuple[str, dict, dict]:
    """hook 入力を読み取る。

    公式プロトコル: stdin に JSON が渡される
        {"tool_name": "Bash", "tool_input": {"command": "..."}, "agent_type": "...", ...}

    agent_type は subagent 内で発火したときのみ存在する [E_S4]。
    main（リーダー）の操作で発火した場合はフィールド自体が無い。

    Returns: (tool_name, tool_input, full_payload)
    backward compat: stdin が空 / tty の場合は環境変数 TOOL_NAME / TOOL_INPUT にフォールバック。
    """
    # stdin JSON を優先（isatty ガード + try/except 慎重型）[E_C4]
    try:
        if not sys.stdin.isatty():
            data = sys.stdin.read()
            if data:
                payload = json.loads(data)
                if isinstance(payload, dict):
                    tool_name = payload.get("tool_name", "")
                    tool_input = payload.get("tool_input", {})
                    if not isinstance(tool_input, dict):
                        tool_input = {}
                    if tool_name:
                        return tool_name, tool_input, payload
    except (json.JSONDecodeError, OSError, ValueError):
        pass

    # 環境変数フォールバック
    tool_name = os.environ.get("TOOL_NAME", "")
    tool_input_str = os.environ.get("TOOL_INPUT", "")
    tool_input: dict = {}
    if tool_input_str:
        try:
            parsed = json.loads(tool_input_str)
            if isinstance(parsed, dict):
                tool_input = parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return tool_name, tool_input, {}


def main() -> int:
    try:
        tool_name, tool_input, full_payload = _read_hook_input()
        if tool_name != "Bash":
            return 0

        if not isinstance(tool_input, dict):
            return 0

        command = tool_input.get("command", "")

        # fast-path: git commit を含まないコマンドは素通し
        if not _is_git_commit_command(command):
            return 0

        # 厳密判定: 引用符内の偶発一致を排除（素通し）[E_A5]
        if not _is_real_git_commit_command(command):
            return 0

        # agent_type ゲート判定 [E_U4]
        err = _validate_committer_gate(full_payload)
        if err:
            return _emit_block(err)

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
