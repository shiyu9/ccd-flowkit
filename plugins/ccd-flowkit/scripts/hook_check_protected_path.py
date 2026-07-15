#!/usr/bin/env python3
"""Claude Code PreToolUse Hook: Edit/Write/MultiEdit 実行前に保護フォルダ編集ガードを実施する。

保護フォルダ配下のファイル編集時、対応するスキル（/design, /build など）のマーカーが
state/_active_skills.json に立っていなければブロックする。
"""

import json
import os
import re
import sys
import traceback
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
# CLAUDE_PROJECT_DIR が立っていれば優先（hook context で安定）、なければ cwd。
# 関連: claude-code-hooks.md 実装上の注意点 #1
PROJECT_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
PROTECTED_PATHS_FILE = PROJECT_ROOT / "state" / "_protected_paths.json"
ACTIVE_SKILLS_FILE = PROJECT_ROOT / "state" / "_active_skills.json"
STATE_DIR = PROJECT_ROOT / "state"
# プラグイン同梱の保護ルール（seeding 前のフォールバック先）。
# このスクリプトは <plugin_root>/scripts/ にあるので、親が plugin_root。
BUNDLED_PROTECTED_PATHS_FILE = SCRIPT_DIR.parent / "references" / "protected-paths.json"
STALE_THRESHOLD_SECONDS = 24 * 3600
IS_WINDOWS = sys.platform == "win32"


def _project_root() -> Path:
    """PROJECT_ROOT を resolve() した Path を返す（テストでパッチしやすいよう関数化）。"""
    return PROJECT_ROOT.resolve()


def normalize_path(path):
    """任意のパスを正規化してプロジェクトルート相対のスラッシュ区切りパスを返す。

    プロジェクトルート外・空文字・非 str の場合は None を返す。
    プロジェクトルート自身の場合は "" を返す。
    """
    if path is None:
        return None
    if not isinstance(path, str):
        return None
    if path == "":
        return None

    # バックスラッシュをスラッシュに統一
    p = path.replace("\\", "/")

    # 先頭 './' 除去
    while p.startswith("./"):
        p = p[2:]

    # 連続スラッシュを 1 個にまとめる
    p = re.sub(r"/+", "/", p)

    if p == "":
        return None

    path_obj = Path(p)
    if not path_obj.is_absolute():
        path_obj = _project_root() / path_obj

    try:
        resolved = path_obj.resolve(strict=False)
    except (OSError, ValueError):
        return None

    root = _project_root()

    resolved_str = str(resolved)
    root_str = str(root)

    if IS_WINDOWS:
        resolved_cmp = resolved_str.lower()
        root_cmp = root_str.lower()
    else:
        resolved_cmp = resolved_str
        root_cmp = root_str

    # プロジェクトルート自身
    if resolved_cmp == root_cmp:
        return ""

    sep = os.sep
    # root + sep または root + "/" で始まるか判定
    if not resolved_cmp.startswith(root_cmp + sep) and not resolved_cmp.startswith(root_cmp + "/"):
        return None

    # ルートからの相対パスを作る（スラッシュ区切りで返す）
    try:
        rel = resolved.relative_to(root)
        rel_str = str(rel).replace("\\", "/")
    except ValueError:
        # Windows の case insensitive ズレ等で relative_to が失敗する可能性あり
        # 文字列スライスで代替
        rel_str = resolved_str[len(root_str) + 1:]
        rel_str = rel_str.replace("\\", "/")

    if rel_str in (".", ""):
        return ""
    return rel_str


_GLOBSTAR_DIR = "\x00G\x00"  # `**/` または `/**/`: ゼロ階層以上のディレクトリ
_GLOBSTAR_END = "\x00E\x00"  # `/**` 末尾: ゼロ階層以上のパス（ファイル含む）


def _glob_to_regex(pattern: str) -> str:
    """glob パターンを正規表現文字列に変換する。

    変換規則:
    - `**/` 先頭 / `/**/`中間 → ゼロ階層以上のディレクトリにマッチ（gitignore 互換）
    - `/**` 末尾 → ゼロ階層以上のパスにマッチ（ディレクトリ自身も含む）
    - `**` 単独（隣接 `/` 無し） → 任意文字列（`/` 含む）
    - `*` → 単一階層内の任意文字（`/` 含まない）
    - `?` → 単一文字（`/` 含まない）
    - その他はエスケープ
    """
    p = pattern
    # 末尾 `/**` を sentinel に置換（path 区切りも optional 化するため）
    if p.endswith("/**"):
        p = p[:-3] + _GLOBSTAR_END
    # 中間の `/**/` を sentinel に置換（左側 `/` は残す）
    p = p.replace("/**/", "/" + _GLOBSTAR_DIR)
    # 先頭 `**/` を sentinel に置換
    if p.startswith("**/"):
        p = _GLOBSTAR_DIR + p[3:]

    result = []
    i = 0
    n = len(p)
    while i < n:
        # sentinel 検査（3 文字固定）
        if p[i : i + 3] == _GLOBSTAR_DIR:
            result.append("(?:[^/]+/)*")
            i += 3
            continue
        if p[i : i + 3] == _GLOBSTAR_END:
            result.append("(?:/.*)?")
            i += 3
            continue
        c = p[i]
        if c == "*":
            if i + 1 < n and p[i + 1] == "*":
                # 隣接 `/` を伴わない `**`（例: `notes/**.md`）は任意文字列
                result.append(".*")
                i += 2
            else:
                result.append("[^/]*")
                i += 1
        elif c == "?":
            result.append("[^/]")
            i += 1
        else:
            result.append(re.escape(c))
            i += 1
    return "^" + "".join(result) + "$"


def match_glob(pattern, path) -> bool:
    """glob マッチ（**=任意階層、*=1階層内任意、?=1文字）。Windows では大小文字無視。"""
    if pattern is None or path is None:
        return False
    if not isinstance(pattern, str) or not isinstance(path, str):
        return False
    if IS_WINDOWS:
        pattern_cmp = pattern.lower()
        path_cmp = path.lower()
    else:
        pattern_cmp = pattern
        path_cmp = path
    regex = _glob_to_regex(pattern_cmp)
    return re.match(regex, path_cmp) is not None


def _load_rules_file(path: Path) -> dict | None:
    """1 つの保護ルールファイルを読み、正規化した dict を返す。読めなければ None。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    if "rules" not in data or not isinstance(data.get("rules"), list):
        data["rules"] = []
    if "exclusions" not in data or not isinstance(data.get("exclusions"), list):
        data["exclusions"] = []
    return data


def load_protected_rules() -> dict:
    """保護ルールを読み込む。

    プロジェクトの state/_protected_paths.json を優先し、無い／壊れている場合は
    プラグイン同梱の references/protected-paths.json にフォールバックする。これにより
    seeding（/run の前処理）前や、フロー外の操作でも保護が有効になる（常時有効）。
    プロジェクト側に正規の設定を置けば上書きできる。どちらも読めない場合のみ
    空構造を返す（fail-open）。
    """
    for path in (PROTECTED_PATHS_FILE, BUNDLED_PROTECTED_PATHS_FILE):
        data = _load_rules_file(path)
        if data is not None:
            return data
    return {"version": 1, "rules": [], "exclusions": []}


def load_active_skills() -> list:
    """_active_skills.json を読み込み、active 配列を返す。

    破損 / 欠損時は空リスト（fail closed）。hook は破損退避しない。
    """
    try:
        with open(ACTIVE_SKILLS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return []
        active = data.get("active", [])
        if not isinstance(active, list):
            return []
        return active
    except (json.JSONDecodeError, FileNotFoundError, OSError):
        return []


def is_skill_active(skill_name, active) -> bool:
    """active に skill_name が 1 件でも含まれれば True。"""
    if not isinstance(active, list):
        return False
    if not isinstance(skill_name, str) or not skill_name:
        return False
    for entry in active:
        if isinstance(entry, dict) and entry.get("name") == skill_name:
            return True
    return False


def is_any_skill_active(skill_names, active) -> bool:
    """skill_names のいずれかが active に含まれれば True。空 list は False。"""
    if not isinstance(skill_names, list) or not skill_names:
        return False
    for name in skill_names:
        if is_skill_active(name, active):
            return True
    return False


def _parse_iso8601(s):
    """ISO 8601 文字列を datetime に変換する。失敗時は None。

    timezone-aware な ISO 文字列（例: 2026-05-27T12:00:00+09:00）の場合、
    naive な datetime.now() との演算で TypeError が発生し fail-open になるため、
    tzinfo を除去して naive に正規化してから返す。
    """
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return None


def check_stale_markers(active, threshold_seconds: int = STALE_THRESHOLD_SECONDS) -> list:
    """active の中で acquired_at が閾値より古い/パース不能なエントリの name リストを返す。"""
    stale = []
    if not isinstance(active, list):
        return stale
    now = datetime.now()
    for entry in active:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", "")
        acquired_at = entry.get("acquired_at")
        dt = _parse_iso8601(acquired_at)
        if dt is None:
            stale.append(name)
            continue
        delta = (now - dt).total_seconds()
        if delta > threshold_seconds:
            stale.append(name)
    return stale


def _required_skills(rule: dict) -> list:
    """ルールから必要スキル名リスト（rule["skills"]）を取り出す。

    skills が list で非空文字列要素を含めばそれらを返す。無効なら [] を返す
    （必要スキル未定義＝ブロック対象外）。
    """
    if not isinstance(rule, dict):
        return []
    skills = rule.get("skills")
    if isinstance(skills, list):
        return [s for s in skills if isinstance(s, str) and s]
    return []


def find_matching_rule(path, rules_config: dict):
    """正規化済みパスに対して最初にパターンマッチするルールを返す（先勝ち）。
    マッチ無しは None。"""
    if path is None:
        return None
    if not isinstance(rules_config, dict):
        return None

    rules = rules_config.get("rules", []) or []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        pattern = rule.get("pattern")
        if not pattern or not isinstance(pattern, str):
            continue
        if match_glob(pattern, path):
            return rule
    return None


def extract_file_paths(tool_input, tool_name: str) -> list:
    """TOOL_INPUT から対象ファイルパス群を抽出する。

    - Edit / Write: file_path 1件
    - MultiEdit: file_path + edits[*].file_path（将来スキーマ対応）
    - 非 dict 入力・キー欠損は空 list
    """
    paths = []
    if not isinstance(tool_input, dict):
        return paths
    try:
        fp = tool_input.get("file_path")
    except (AttributeError, TypeError, KeyError):
        fp = None
    if isinstance(fp, str) and fp:
        paths.append(fp)

    if tool_name == "MultiEdit":
        try:
            edits = tool_input.get("edits")
        except (AttributeError, TypeError, KeyError):
            edits = None
        if isinstance(edits, list):
            for ed in edits:
                if not isinstance(ed, dict):
                    continue
                try:
                    efp = ed.get("file_path")
                except (AttributeError, TypeError, KeyError):
                    efp = None
                if isinstance(efp, str) and efp:
                    paths.append(efp)
    return paths


def _format_block_message(fp: str, rule: dict, skills: list) -> str:
    """ブロックメッセージを組み立てる。

    単一スキル / 複数スキル（OR 判定）で文面を分ける。
    """
    lines = [f"[hook] 保護ガード: {fp}"]
    rule_message = rule.get("message") if isinstance(rule, dict) else None

    if len(skills) == 1:
        skill = skills[0]
        if rule_message:
            lines.append(str(rule_message))
        else:
            lines.append(
                f"このファイルを編集するには /{skill} スキルを実行してください"
            )
        lines.append(
            f"  対応: python scripts/manage_active_skill.py acquire {skill} を実行してから再度編集してください"
        )
    else:
        joined = " または ".join(f"/{s}" for s in skills)
        if rule_message:
            lines.append(str(rule_message))
        else:
            lines.append(
                f"このファイルを編集するには {joined} スキルを実行してください"
            )
        lines.append("  対応: 以下のいずれかを実行してから再度編集してください")
        for s in skills:
            lines.append(f"    - python scripts/manage_active_skill.py acquire {s}")
    return "\n".join(lines) + "\n"


def _read_hook_input() -> tuple[str, dict]:
    """hook 入力を読み取る。

    公式プロトコル: stdin に JSON が渡される
        {"tool_name": "Edit", "tool_input": {"file_path": "...", ...}, ...}

    backward compat: stdin が空 / tty の場合は環境変数 TOOL_NAME / TOOL_INPUT
    にフォールバック（テストでの patch 互換のため）。
    """
    # stdin JSON を優先
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
                        return tool_name, tool_input
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
    return tool_name, tool_input


def main() -> int:
    """Hook エントリポイント。

    Returns:
        0: 許可（通常終了）
        2: ブロック（Claude Code 公式仕様の block exit code）
    """
    try:
        tool_name, tool_input = _read_hook_input()
        if tool_name not in ("Edit", "Write", "MultiEdit"):
            return 0

        if not isinstance(tool_input, dict):
            return 0

        try:
            file_paths = extract_file_paths(tool_input, tool_name)
        except (KeyError, AttributeError, TypeError):
            return 0

        if not file_paths:
            return 0

        rules_config = load_protected_rules()
        if not rules_config.get("rules"):
            return 0

        active = load_active_skills()

        # stale 警告（マーカーとしては有効扱い、処理継続）
        stale = check_stale_markers(active)
        if stale:
            sys.stderr.write(
                "[hook] 警告: 古いマーカーが残留しています: "
                + ", ".join(stale)
                + "\n  `python scripts/manage_active_skill.py clear --stale` の実行を検討してください\n"
            )

        for fp in file_paths:
            norm = normalize_path(fp)
            if norm is None:
                # プロジェクト外 → 保護対象外
                continue
            rule = find_matching_rule(norm, rules_config)
            if rule is None:
                continue
            required = _required_skills(rule)
            if not required:
                # 必要スキル未定義 → ブロック対象外
                continue
            if is_any_skill_active(required, active):
                continue
            # ブロック（公式仕様: exit 2）
            sys.stderr.write(_format_block_message(fp, rule, required))
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
