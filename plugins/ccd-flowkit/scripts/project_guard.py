#!/usr/bin/env python
"""project_guard.py
スキャナ・状態機械スクリプト共通の「プロジェクトルート検証」。

誤った cwd（プラグインの scripts/ ディレクトリなど）からスキャナ群が実行されると、
docs/ が無いため各 glob が空振り＝**何も検査せずに green** を返してしまい、フェーズゲートの
幽霊 pass が成立する（判定の真正性リスク）。カバレッジ系の「対象が無ければ検査しない」
という正しい保守性が、「プロジェクト外では何も無い」ケースと区別できないのが根因。

対策: 検査系スクリプトは実行前に ensure_project_root() を呼び、プロジェクトマーカー
（docs/ ディレクトリ）が cwd に無ければ **exit 2 で hard-fail** する（green を返さない）。
"""
import pathlib
import sys


def is_project_root(cwd="."):
    """cwd がプロジェクトルートらしいか（docs/ ディレクトリの存在で判定・純関数）。"""
    return (pathlib.Path(cwd) / "docs").is_dir()


def ensure_project_root(root=None):
    """プロジェクトルート外なら stderr にエラーを出して exit 2（検査せず green を防ぐ）。

    root を省略すると cwd（"."）を検査する（スキャナ群の従来動作と互換）。
    CLAUDE_PROJECT_DIR 環境変数等で cwd と別にルートを解決するスクリプト
    （manage_flow_state.py 等）は、解決済みのルートを root に渡すこと
    （cwd 固定検査では env 経由の誤ルート解決を見逃すため）。
    """
    check = root if root is not None else "."
    if is_project_root(check):
        return
    print(f"エラー: {check} に docs/ が見つかりません（プロジェクトルート外での実行）。\n"
          "  プロジェクトルート（要望書・docs/ のあるディレクトリ）へ cd するか、\n"
          "  CLAUDE_PROJECT_DIR を正しいプロジェクトルートに設定してください。\n"
          "  ※プロジェクト外での実行は『検査対象なし＝green』ではなく実行エラーです"
          "（誤 cwd の無検査 pass を防ぐ）。", file=sys.stderr)
    sys.exit(2)
