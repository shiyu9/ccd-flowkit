#!/usr/bin/env python
"""manage_review_baseline.py
再レビュー3成分モデル（review-conventions.md）のための、レビュー時点スナップショットと機械 diff。

再レビューの中立性の原則: 毒は「是正者・仲介者の主張」であって「機械的な変更情報」ではない。
レビューアが**自分で**前回レビュー時点との diff を取得すれば、是正者に改竄できない中立な
変更事実に基づいて (a)指摘消込 (b)回帰確認 を絞って行える（全文再読は初回と大差分時のみ）。

サブコマンド（<node> はレビューノード名。例: DES_REVIEW）:
  save <node> <file...>   対象ファイルを state/_review_baseline/<node>/ へ保存し sha256 を記録
                          （レビュー終了時に pass/high を問わず実行＝次回 diff の基準）
  diff <node> <file...>   baseline と現在の unified diff と変更率を JSON+テキストで出力
                          （baseline が無いファイルは "no_baseline"＝初回扱い）
  hash <file...>          現在の sha256 を出力（再試験免除のsrc無変更確認などに）

保存先はガバナンス領域 state/ 配下だが、書込は本スクリプト経由のみ（レビューアが Write で
直接置かない）。LLM 判断は使わない。
"""
import difflib
import hashlib
import json
import os
import pathlib
import posixpath
import re
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

PROJECT_ROOT = pathlib.Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
BASELINE_ROOT = PROJECT_ROOT / "state" / "_review_baseline"

# 誤ルート解決（cwd/CLAUDE_PROJECT_DIR 不一致）の無検査書込を防ぐ（project_guard.py 参照）。
try:
    from project_guard import ensure_project_root
except ImportError:  # 配置差異でも fail-open にしない最小フォールバック
    def ensure_project_root(root=None):
        check = pathlib.Path(root) if root is not None else pathlib.Path(".")
        if not (check / "docs").is_dir():
            print(f"エラー: {check} に docs/ が無い（プロジェクトルート外での実行）", file=sys.stderr)
            sys.exit(2)

NODE_RE = re.compile(r"^[A-Z][A-Z_]{1,40}$")

# 全文再レビューへ切り替える変更率の閾値（review-conventions.md と一致させる）
FULL_REREAD_RATIO = 0.30


def _safe_node(node):
    """node をディレクトリ名として安全な形に検証する（英大文字とアンダースコアのみ）。"""
    if not isinstance(node, str) or not NODE_RE.match(node):
        return None
    return node


def _key_for(file_path):
    """対象ファイルの baseline 内キー（プロジェクトルート相対に正規化してから __ で潰す）。

    save を絶対パス・diff を相対パスで呼んでも同じキーになるよう、まずプロジェクトルート
    （CLAUDE_PROJECT_DIR / cwd）基準の絶対パスへ正規化し、ルート配下ならルート相対にする
    （不一致だと baseline が永続的に no_baseline になり機械diffが黙って機能しなくなる）。
    比較は Windows の大小無視に合わせ casefold で行う。ルート外のパスはドライブ除去のみ。
    """
    s = str(file_path).replace("\\", "/")
    root = str(PROJECT_ROOT).replace("\\", "/").rstrip("/")
    ap = s if (s.startswith("/") or re.match(r"^[A-Za-z]:", s)) else f"{root}/{s}"
    ap = posixpath.normpath(ap)
    if ap.casefold().startswith(root.casefold() + "/"):
        rel = ap[len(root) + 1:]
    else:
        rel = re.sub(r"^[A-Za-z]:", "", ap).lstrip("/")
    return rel.replace("/", "__")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def change_ratio(old_text, new_text):
    """変更行数の割合（0.0〜1.0）。分母は新旧の大きい方の行数（空同士は 0.0）。"""
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    denom = max(len(old_lines), len(new_lines))
    if denom == 0:
        return 0.0
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    changed = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            changed += max(i2 - i1, j2 - j1)
    return min(1.0, changed / denom)


def cmd_save(node, files):
    ensure_project_root(str(PROJECT_ROOT))
    node = _safe_node(node)
    if not node:
        print("エラー: node はレビューノード名（例 DES_REVIEW）を指定する", file=sys.stderr)
        return 1
    dest = BASELINE_ROOT / node
    dest.mkdir(parents=True, exist_ok=True)
    saved = []
    missing = 0
    for f in files:
        p = pathlib.Path(f)
        if not p.exists():
            saved.append({"file": f, "error": "not_found"})
            missing += 1
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        (dest / _key_for(f)).write_text(text, encoding="utf-8")
        saved.append({"file": f, "sha256": sha256_of(p)})
    print(json.dumps({"node": node, "saved": saved}, ensure_ascii=False, indent=2))
    if missing:
        # 無言の no-op を防ぐ（パスtypo・cwd違いで baseline 更新が漏れると次回 diff が壊れる）
        print(f"エラー: {missing} 件のファイルが見つからず保存されていない", file=sys.stderr)
        return 1
    return 0


def cmd_diff(node, files):
    ensure_project_root(str(PROJECT_ROOT))
    node = _safe_node(node)
    if not node:
        print("エラー: node はレビューノード名（例 DES_REVIEW）を指定する", file=sys.stderr)
        return 1
    dest = BASELINE_ROOT / node
    results = []
    diff_texts = []
    for f in files:
        p = pathlib.Path(f)
        base = dest / _key_for(f)
        if not p.exists():
            results.append({"file": f, "status": "not_found"})
            continue
        if not base.exists():
            results.append({"file": f, "status": "no_baseline"})
            continue
        old = base.read_text(encoding="utf-8", errors="replace")
        new = p.read_text(encoding="utf-8", errors="replace")
        if old == new:
            results.append({"file": f, "status": "unchanged", "change_ratio": 0.0})
            continue
        ratio = change_ratio(old, new)
        results.append({"file": f, "status": "changed", "change_ratio": round(ratio, 3),
                        "full_reread_recommended": ratio > FULL_REREAD_RATIO})
        ud = difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                  fromfile=f"baseline/{f}", tofile=f"current/{f}")
        diff_texts.append("".join(ud))
    print(json.dumps({"node": node, "files": results}, ensure_ascii=False, indent=2))
    for t in diff_texts:
        print("\n" + t)
    return 0


def cmd_hash(files):
    out = []
    for f in files:
        p = pathlib.Path(f)
        out.append({"file": f, "sha256": sha256_of(p) if p.exists() else None})
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    # ensure_project_root() は save/diff（BASELINE_ROOT への読み書き）にのみ適用する
    # （hash はファイルの sha256 を返すだけの汎用ユーティリティで PROJECT_ROOT/BASELINE_ROOT を
    #  一切参照しないため、docs/ の無い場所からの呼び出しを妨げない）。
    cmd = sys.argv[1]
    if cmd == "save":
        return cmd_save(sys.argv[2], sys.argv[3:])
    if cmd == "diff":
        return cmd_diff(sys.argv[2], sys.argv[3:])
    if cmd == "hash":
        return cmd_hash(sys.argv[2:])
    print(f"不明なサブコマンド: {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
