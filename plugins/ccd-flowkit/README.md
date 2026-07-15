# ccd-flowkit

開発フローを専任エージェント群でほぼ自動進行するプラグイン。

## 起動
```
/ccd-flowkit:run <作りたいものの概要>
```

## 工程と担当
| 工程 | 流れ（担当エージェント） | 成果物 |
|---|---|---|
| 要件定義 | project-planner →(ユーザー承認)→ system-test-planner → system-test-plan-reviewer | 要件定義書 / 総合試験計画 |
| 設計 | designer → design-reviewer → integration-test-planner → integration-test-plan-reviewer | 設計書 / 結合試験計画 |
| 構築 | builder → build-reviewer → unit-test-planner → unit-test-plan-reviewer | コード・構築物 / 単体試験計画 |
| 試験 | unit-tester → integration-tester → system-tester(またはユーザー支援) | 単体/結合/総合 試験結果 |

- ユーザー承認ゲートは**要件定義のみ**。
- レビューで未是正の「高」指摘が残ると、その工程で停止して報告。

## 規約（観点集）
`references/` に雛形を同梱。初回実行時に `docs/conventions/` へコピーされ、
プロジェクト側で編集（上書き）すると以降そちらが優先されます。

## 成果物の出力先
プロジェクトの `docs/` 配下（実ソースはリポジトリの src/ 等）。

## 要件定義のプランモード自動投入
要件定義フェーズに入ると、司令塔が自動でプランモードへ切り替える（方式A）。
プランモード中は読み取り専用で要件案を「計画」として提示し、
**プランモードの承認＝要件定義の承認ゲート**。承認後にプランモードを抜けて
要件定義書を書き出し、以降の工程は自動進行する。

自動切替が効かない環境向けの確実版（方式B）として
`examples/settings.plan-mode.json` を同梱。中身をプロジェクトの
`.claude/settings.json` にコピーすると、セッションがプランモードで始まる
（プロジェクト全体に効くため、他作業もプランモード開始になる点に注意）。


## ユーザーへの質問
全工程共通で、担当が判断に迷う点は推測せず、オーケストレーターが AskUserQuestion で
ユーザーに質問してから進める（承認ゲートは要件定義のみ、質問は全工程で発生しうる）。

## 参照資料
`references/phase-guide.md` … フェーズ別の担当・作業・I/O・完了条件・戻り条件の一覧。司令塔が参照。

## クローンして開発・常用する
1. 開発(編集が即反映): リポジトリ直下で
   `claude --plugin-dir ./plugins/ccd-flowkit`
   - SKILL.md は即時反映、agents/ references/ を変えたら `/reload-plugins`。
2. 常用(開くだけで有効): `examples/project-settings.sample.json` の中身を
   対象プロジェクトの `.claude/settings.json` にコピー。
   - ローカルのクローンを使うなら directory ソース、GitHub から取得させるなら github ソースに変更。
   - enabledPlugins は settings.json 側に書く(local.json だけだと無視されることがある)。
3. 反映されないとき: `/plugin marketplace update`、`/reload-plugins`、または再起動。
   キャッシュが残る場合は `rm -rf ~/.claude/plugins/cache` 後に再読込。

## レビューの集約とカスタマイズ（フラット化モデル）
レビュー系エージェント（design-reviewer / build-reviewer / 各試験計画レビュー）は、
**integrator（統合＋判定役）**です。観点サブエージェントの起動は main（ディスパッチャ）が
並列に行い、結果を `state/review_results/<NODE>.jsonl` に保存してから integrator を起動します。
integrator は state を読んで観点結果を統合・判定し、レビュー記録.md を書いて advance します
（起動責務を持たず、Task ツールも持たない）。

使う観点は `docs/conventions/reviewers.json`（初回実行時に references から雛形がコピーされる）で
キー `bld_review` / `des_review` / `sys_review` / `int_review` / `unit_review` の subagents 配列を
編集して増減できます（キー名がノード名の小文字形に統一）。
- 既定の bld_review 観点: build-convention-reviewer（自作）＋ 公式 code-reviewer / silent-failure-hunter / type-design-analyzer / code-simplifier
- 独自観点の追加: プロジェクトの `.claude/agents/` に自作エージェントを置き、その name を reviewers.json の該当キーに追記。
- comment-analyzer / pr-test-analyzer は PR 前提のため既定で無効。PR運用時に有効化。
- **不変条件**: サブエージェントは他のサブエージェントを起動しない（hook_check_subagent_launch が機械的に block）。

## 併用する公式プラグイン（別途インストール）
以下の公式プラグインを**ユーザー側で別途 install** してください（v0.2.2 で自動解決は撤廃）。
- `pr-review-toolkit@claude-plugins-official` … bld_review で main が並列起動する公式観点エージェント
- `security-guidance@claude-plugins-official` … 構築時に自動発火するセキュリティ・ガードレール

```
/plugin marketplace add anthropics/claude-code
/plugin install pr-review-toolkit@claude-plugins-official
/plugin install security-guidance@claude-plugins-official
```

**自動解決を撤廃した理由**: Claude Code デスクトップアプリは有効な全プラグインを
`--plugin-dir` で inline ロードするため、`plugin.json` の `dependencies` を
`{name, marketplace}` 形式で書くと ID が `<name>@inline` と `<name>@<marketplace>`
で一致せず `dependency-unsatisfied` になり ccd-flowkit 全体が読み込まれなくなる。
両環境で成立する記述方法が現状無いため、依存宣言を撤廃してユーザー側で独立に
有効化する方式に切り替えた。

LSP は任意。利用言語のプラグイン（例: `pyright-lsp` / `typescript-lsp` / `gopls-lsp` 等）を
入れ、言語サーバ本体を PATH に用意すると、構築・試験の精度が上がります。

## トレーサビリティ
要件→設計→コード→試験をIDで追跡します（規約: references/trace-conventions.md）。
- ID-first: 各担当が `scripts/next_id.py` で採番してから内容を書く。
- 設計書はHTML（<section data-trace-id ...>）。要件はMarkdownのREQ-、試験はTEST-、コードはコメントタグ。
- 各フェーズ完了時に `scripts/trace_scan.py`（決定論チェック）と consistency-checker（変更の影響分析・矛盾検出）を通す。high があれば fail-closed で停止。
- LSP を入れると、コードの公開シンボル網羅やシグネチャ整合の裏取りに使われます。
