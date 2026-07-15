---
description: コミットフロー。staged 変更（または指定ファイル）を committer サブエージェントに渡し、diff から自律導出・関連ID付与・git commit -F まで一貫実行させる。committer 以外の git commit は agent_type ゲートが block する。
disable-model-invocation: true
allowed-tools: Read, Glob, Grep, Bash, Task
---

# コミットフロー

引数なし: staged 全件を対象。引数あり: そのファイル群を対象（committer が git add する）。

1. `git status --short` と `git diff --cached --name-only` で staged を確認する。
   引数があればそのファイル群を対象スコープにする。staged も指定も無ければ
   「コミット対象がありません。`git add <file>` するか対象ファイルを指定してください」と
   報告して終了する。
2. **committer** に Task で委任し、**スコープのみ**を渡す（メッセージ本文・subject は渡さない。
   committer の自律導出を維持するため）:
   ```
   コミット対象スコープ: <ファイル群 or "staged 全件">
   ```
3. committer の結果を確認し、コミットのハッシュと対象ファイルを報告する。
   committer が中止を返したら理由を報告する。

## 注意
- リーダー（あなた）が直接 `git commit` してはいけない（agent_type ゲートに block される）。
  コミットは必ず committer 経由で行う。
- `-A` / `--no-verify` / `--amend` を勝手に使わない。
- ユーザーの明示的な指示なくコミットしない。
