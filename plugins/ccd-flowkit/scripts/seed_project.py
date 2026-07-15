#!/usr/bin/env python
"""seed_project.py
開発フローの前処理（規約・保護設定の用意）を決定論的に行う。

run スキルの前処理を Bash の cp 等で行うと Windows パスのクォートが壊れて
ゴミファイルを生成する事故があったため、この処理を Python に集約する。

やること（いずれも冪等・既存はユーザー上書き版として尊重）:
  - docs/{requirements,design,build,test,trace,conventions} を作成
  - docs/conventions/ にプラグイン同梱 references/*.md|*.json をコピー（既存ファイルは保持）
  - state/ を作成し state/_protected_paths.json を references/protected-paths.json からコピー（既存は保持）
  - 利用プロジェクトの .gitignore に state/_active_skills.json を冪等追記

使い方: python seed_project.py [project_dir]
  project_dir 省略時はカレントディレクトリ。
プラグイン同梱の references/ はこのスクリプトの位置から解決する。

注記（project_guard.ensure_project_root 非適用の理由）: 本スクリプトは docs/ 等の
プロジェクトマーカー自体を**新規に置く**前処理であり、実行時点でプロジェクトルートが
まだ存在しない（マーカーが無い）のが正常系。他の書込系スクリプトと異なり
ensure_project_root() は適用しない。誤ディレクトリでの実行対策は project_dir 引数の
明示指定と CLAUDE_PROJECT_DIR 優先ロジック、および実行順序規約（seed →
manage_flow_state.py init。init 側は seed マーカー必須で防御）に委ねる。
"""
import pathlib
import shutil
import sys

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REFERENCES = SCRIPT_DIR.parent / "references"

DOC_DIRS = ["requirements", "design", "build", "test", "trace", "conventions"]


def seed(project_dir, references=REFERENCES):
    """前処理を実行し、行った操作のサマリ dict を返す（テスト可能）。"""
    project_dir = pathlib.Path(project_dir)
    created_dirs = []
    copied_conventions = []
    copied_protected = None
    gitignore_updated = False

    docs = project_dir / "docs"
    for name in DOC_DIRS:
        d = docs / name
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created_dirs.append(str(d.relative_to(project_dir)))

    # conventions: references の md/json をコピー（protected-paths は state 用なので除外）。既存は保持。
    conv = docs / "conventions"
    if references.exists():
        for ref in sorted(references.iterdir()):
            if ref.name == "protected-paths.json":
                continue
            if ref.suffix not in (".md", ".json"):
                continue
            target = conv / ref.name
            if not target.exists():
                shutil.copy2(str(ref), str(target))
                copied_conventions.append(ref.name)

    # state/_protected_paths.json
    state = project_dir / "state"
    state.mkdir(parents=True, exist_ok=True)
    pp_src = references / "protected-paths.json"
    pp_dst = state / "_protected_paths.json"
    if pp_src.exists() and not pp_dst.exists():
        shutil.copy2(str(pp_src), str(pp_dst))
        copied_protected = str(pp_dst.relative_to(project_dir))

    # .gitignore に state/_active_skills.json を冪等追記
    gi = project_dir / ".gitignore"
    line = "state/_active_skills.json"
    existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if line not in existing.splitlines():
        with open(gi, "a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write(line + "\n")
        gitignore_updated = True

    return {
        "created_dirs": created_dirs,
        "copied_conventions": copied_conventions,
        "copied_protected": copied_protected,
        "gitignore_updated": gitignore_updated,
    }


def main():
    # ルート解決を manage_flow_state.py と一致させる（CLAUDE_PROJECT_DIR 優先 → cwd）。
    # 規則が食い違うと、seed が cwd 側へマーカーを置き init が CLAUDE_PROJECT_DIR 側を探して
    # 「seed 未実施」拒否になる（正順の前処理が通らない）。argv 明示指定は常に最優先。
    import json
    import os
    if len(sys.argv) > 1:
        project_dir = sys.argv[1]
    else:
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    result = seed(project_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
