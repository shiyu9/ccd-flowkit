#!/usr/bin/env python3
"""アクティブスキルマーカー（state/_active_skills.json）の管理 CLI。

サブコマンド:
  acquire <skill_name>                   マーカー取得（末尾追加、重複 OK）
  release <skill_name>                   マーカー解放（末尾から1件削除、LIFO）
  list [--warn-stale]                    現在のマーカー一覧表示
  clear [<skill_name>]                   全削除 / 特定名削除
  clear --stale [--hours N]              N時間（既定24）より古いマーカーのみ削除
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Windows コンソール(cp932)で日本語を print してもクラッシュしないよう stdout/stderr を UTF-8 化。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SCRIPT_DIR = Path(__file__).resolve().parent
# 利用側プロジェクトの state/ を参照する。plugin install 環境では
# `__file__.parent.parent` は plugin キャッシュ root を指すため使えない。
# CLAUDE_PROJECT_DIR が立っていれば優先（hook context 等で安定）、なければ cwd。
# 関連: claude-code-hooks.md 実装上の注意点 #1 / lessons.md「protected_path 6 年級バグ」
PROJECT_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
ACTIVE_SKILLS_FILE = PROJECT_ROOT / "state" / "_active_skills.json"
STATE_DIR = PROJECT_ROOT / "state"
DEFAULT_STALE_HOURS = 24

# 誤ルート解決（cwd/CLAUDE_PROJECT_DIR 不一致）の無検査書込を防ぐ（project_guard.py 参照）。
# フェーズマーカーの取得・解放が誤った場所で成立すると protected-path ガードが無効化されうる。
try:
    from project_guard import ensure_project_root
except ImportError:  # 配置差異でも fail-open にしない最小フォールバック
    def ensure_project_root(root=None):
        check = Path(root) if root is not None else Path(".")
        if not (check / "docs").is_dir():
            print(f"エラー: {check} に docs/ が無い（プロジェクトルート外での実行）", file=sys.stderr)
            sys.exit(2)


def _now_iso() -> str:
    """現在時刻を秒精度の ISO 8601 文字列で返す。"""
    return datetime.now().isoformat(timespec="seconds")


def _empty_state() -> dict:
    return {"version": 1, "active": []}


def _parse_iso8601(s):
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        # timezone-aware な ISO 文字列の場合、naive な datetime.now() との演算で
        # TypeError が発生し fail-open になるため、naive に正規化する。
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return None


def _is_stale(entry, threshold_seconds: int) -> bool:
    """エントリが閾値より古い / acquired_at が無効なら True。"""
    if not isinstance(entry, dict):
        return True
    acquired_at = entry.get("acquired_at")
    dt = _parse_iso8601(acquired_at)
    if dt is None:
        return True
    return (datetime.now() - dt).total_seconds() > threshold_seconds


def _quarantine_corrupt_file():
    """破損した _active_skills.json を .corrupt.{ts} にリネームして退避する。

    失敗（OS エラー）でも処理は続行する。stderr に退避した旨を出力。
    """
    if not ACTIVE_SKILLS_FILE.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    target = ACTIVE_SKILLS_FILE.with_name(ACTIVE_SKILLS_FILE.name + f".corrupt.{ts}")
    try:
        os.replace(str(ACTIVE_SKILLS_FILE), str(target))
    except OSError:
        return None
    sys.stderr.write(
        f"[manage_active_skill] マーカーファイルが破損していたため退避しました: {target}\n"
    )
    return target


def load_active_skills_for_write() -> dict:
    """書き込み系コマンド用ロード。破損時は退避して空状態で開始。

    欠損時も空状態を返す。
    """
    if not ACTIVE_SKILLS_FILE.exists():
        return _empty_state()
    try:
        with open(ACTIVE_SKILLS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise json.JSONDecodeError("not a dict", "", 0)
        if "active" not in data or not isinstance(data.get("active"), list):
            data["active"] = []
        if "version" not in data:
            data["version"] = 1
        return data
    except (json.JSONDecodeError, OSError):
        _quarantine_corrupt_file()
        return _empty_state()


def load_active_skills_readonly() -> dict:
    """読み取り系コマンド用ロード。破損時でも退避はせず空状態を返す。"""
    if not ACTIVE_SKILLS_FILE.exists():
        return _empty_state()
    try:
        with open(ACTIVE_SKILLS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _empty_state()
        if "active" not in data or not isinstance(data.get("active"), list):
            data["active"] = []
        if "version" not in data:
            data["version"] = 1
        return data
    except (json.JSONDecodeError, OSError):
        return _empty_state()


def write_active_skills_atomic(state: dict) -> None:
    """tmp ファイルに書き出し、os.replace でアトミックに置換する。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = ACTIVE_SKILLS_FILE.with_name(ACTIVE_SKILLS_FILE.name + ".tmp")
    data_str = json.dumps(state, ensure_ascii=False, indent=2)
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(data_str)
    os.replace(str(tmp_path), str(ACTIVE_SKILLS_FILE))


def _warn_stale(active, threshold_seconds: int = DEFAULT_STALE_HOURS * 3600) -> list:
    """stale エントリを stderr に警告し、スキル名リストを返す。"""
    stale_names = []
    if not isinstance(active, list):
        return stale_names
    for entry in active:
        if not isinstance(entry, dict):
            continue
        if _is_stale(entry, threshold_seconds):
            stale_names.append(entry.get("name", ""))
    if stale_names:
        sys.stderr.write(
            "[manage_active_skill] 警告: 古いマーカーが残留しています: "
            + ", ".join(stale_names)
            + "\n"
        )
    return stale_names


def cmd_acquire(args) -> int:
    ensure_project_root(str(PROJECT_ROOT))
    state = load_active_skills_for_write()
    active = state.get("active", [])
    # stale 警告（処理は継続）
    _warn_stale(active)
    try:
        pid = os.getpid()
    except Exception:
        pid = 0
    entry = {
        "name": args.skill_name,
        "acquired_at": _now_iso(),
        "pid": pid,
    }
    active.append(entry)
    state["active"] = active
    write_active_skills_atomic(state)
    print(f"acquired: {args.skill_name}")
    return 0


def cmd_release(args) -> int:
    ensure_project_root(str(PROJECT_ROOT))
    if not ACTIVE_SKILLS_FILE.exists():
        sys.stderr.write("[manage_active_skill] リリース対象が無い（ファイル不在）\n")
        return 0
    state = load_active_skills_for_write()
    active = state.get("active", [])
    # 末尾から走査して最初の一致 1 件を削除（LIFO）
    removed = False
    for i in range(len(active) - 1, -1, -1):
        entry = active[i]
        if isinstance(entry, dict) and entry.get("name") == args.skill_name:
            del active[i]
            removed = True
            break
    if not removed:
        sys.stderr.write(
            f"[manage_active_skill] リリース対象が無い: {args.skill_name}\n"
        )
    state["active"] = active
    write_active_skills_atomic(state)
    if removed:
        print(f"released: {args.skill_name}")
    return 0


def cmd_list(args) -> int:
    state = load_active_skills_readonly()
    active = state.get("active", [])
    if not active:
        print("(アクティブスキルなし)")
        return 0
    threshold = DEFAULT_STALE_HOURS * 3600
    for entry in active:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", "?")
        acquired_at = entry.get("acquired_at", "?")
        pid = entry.get("pid", 0)
        is_stale = _is_stale(entry, threshold)
        marker = " [STALE]" if (args.warn_stale and is_stale) else ""
        print(f"- {name}  acquired_at={acquired_at}  pid={pid}{marker}")
    if args.warn_stale:
        _warn_stale(active)
    return 0


def cmd_clear(args) -> int:
    state = load_active_skills_for_write()
    active = state.get("active", [])
    if args.stale:
        threshold_seconds = int(args.hours) * 3600
        new_active = [
            e for e in active
            if isinstance(e, dict) and not _is_stale(e, threshold_seconds)
        ]
        removed = len(active) - len(new_active)
        state["active"] = new_active
        write_active_skills_atomic(state)
        print(f"cleared stale: {removed} 件削除")
        return 0
    if args.skill_name:
        new_active = [
            e for e in active
            if not (isinstance(e, dict) and e.get("name") == args.skill_name)
        ]
        removed = len(active) - len(new_active)
        state["active"] = new_active
        write_active_skills_atomic(state)
        print(f"cleared {args.skill_name}: {removed} 件削除")
        return 0
    # 引数なし: 全削除
    state["active"] = []
    write_active_skills_atomic(state)
    print("cleared all")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="アクティブスキルマーカー管理 CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_acq = sub.add_parser("acquire", help="マーカー取得")
    p_acq.add_argument("skill_name", help="スキル名")
    p_acq.set_defaults(func=cmd_acquire)

    p_rel = sub.add_parser("release", help="マーカー解放（LIFO）")
    p_rel.add_argument("skill_name", help="スキル名")
    p_rel.set_defaults(func=cmd_release)

    p_list = sub.add_parser("list", help="マーカー一覧")
    p_list.add_argument("--warn-stale", action="store_true", help="stale なエントリを強調表示")
    p_list.set_defaults(func=cmd_list)

    p_clear = sub.add_parser("clear", help="マーカー削除")
    p_clear.add_argument("skill_name", nargs="?", default=None, help="特定スキル名（省略時は全削除）")
    p_clear.add_argument("--stale", action="store_true", help="stale エントリのみ削除（--stale 指定時は skill_name は無視）")
    p_clear.add_argument("--hours", type=int, default=DEFAULT_STALE_HOURS, help="stale 閾値（時間）")
    p_clear.set_defaults(func=cmd_clear)

    return parser


def main(argv=None) -> int:
    # ensure_project_root() は書込系サブコマンド（acquire/release）の各 cmd_* 内で個別に呼ぶ
    # （main() 冒頭で一括適用すると、docs/ 不要な読み取り系
    #  list・remediation 用の clear、および argparse 自身の --help 処理まで巻き込んで
    #  ブロックしてしまう。list/clear は誤ルートでの markers 診断・救済用途にも使われるため
    #  意図的に対象外とする）。
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
