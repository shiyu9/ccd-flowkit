# 開発フェーズ一覧（オーケストレーター用）

司令塔はこの資料を地図として参照する。各ステップを担当エージェントへ委任し、
成果物は docs/ で受け渡す。承認ゲートは要件定義のみ（プランモード承認）。
全工程共通で、担当が「要ユーザー判断」を返したら AskUserQuestion でユーザーへ質問する。

---

## 1. 要件定義フェーズ

| 順 | 担当 | 何をするか | インプット | アウトプット |
|---|---|---|---|---|
| 1 | (司令塔) | プランモードへ自動切替（読み取り専用） | — | プランモード状態 |
| 2 | project-planner | 要件(機能/非機能・要件ID付)と確認事項を要件案として整理 | $ARGUMENTS(概要), docs/conventions/ | 要件案(画面提示) |
| 3 | (ユーザー) | 要件案を承認（=プランモード承認） | 要件案 | 承認/修正指示 |
| 4 | project-planner | 承認内容を要件定義書として書き出し | 承認済み要件案 | docs/requirements/要件定義書.md |
| 5 | system-test-planner | 総合試験計画を作成 | 要件定義書 | docs/requirements/総合試験計画.md |
| 6 | system-test-plan-reviewer | 総合試験計画をレビュー | 総合試験計画+要件定義書 | docs/requirements/総合試験計画レビュー記録.md |

- **完了条件**: 要件定義書がユーザー承認済み、かつ総合試験計画がレビュー合格（未是正の「高」なし）。
- **戻り条件**: 承認時に「修正」→ project-planner へ差し戻し。計画レビューで「高」指摘→ system-test-planner へ差し戻し。

---

## 2. 設計フェーズ

| 順 | 担当 | 何をするか | インプット | アウトプット |
|---|---|---|---|---|
| 1 | designer | 全要件IDを設計へ対応づけて設計 | docs/requirements/要件定義書.md, docs/conventions/ | docs/design/設計書.html |
| 2 | design-reviewer | 設計観点でレビュー（トレーサビリティ等） | 設計書+要件定義書 | docs/design/設計レビュー記録.md |
| 3 | integration-test-planner | 結合試験計画を作成 | 設計書 | docs/design/結合試験計画.md |
| 4 | integration-test-plan-reviewer | 結合試験計画をレビュー | 結合試験計画+設計書 | docs/design/結合試験計画レビュー記録.md |

- **完了条件**: 設計書レビュー合格、かつ結合試験計画レビュー合格（いずれも未是正の「高」なし）。
- **戻り条件**: 設計レビュー指摘→ designer へ差し戻し。計画レビュー指摘→ integration-test-planner へ差し戻し。要件の欠陥が判明したら**要件定義フェーズへ差し戻し（ユーザーに諮る）**。

---

## 3. 構築フェーズ

| 順 | 担当 | 何をするか | インプット | アウトプット |
|---|---|---|---|---|
| 1 | builder | 設計どおりにコード・構築物を実装 | docs/design/設計書.html, docs/conventions/ | 実ソース(src/ 等) |
| 2 | build-reviewer | 構築観点でレビュー（設計整合・規約・脆弱性等） | 構築物+設計書 | docs/build/構築レビュー記録.md |
| 3 | unit-test-planner | 単体試験計画を作成 | 実装+設計書 | docs/build/単体試験計画.md |
| 4 | unit-test-plan-reviewer | 単体試験計画をレビュー | 単体試験計画+設計書 | docs/build/単体試験計画レビュー記録.md |

- **完了条件**: 構築レビュー合格、かつ単体試験計画レビュー合格（いずれも未是正の「高」なし）。
- **戻り条件**: 構築レビュー指摘→ builder へ差し戻し。計画レビュー指摘→ unit-test-planner へ差し戻し。設計との乖離が判明したら**設計フェーズへ差し戻し（ユーザーに諮る）**。

---

## 4. 試験フェーズ

| 順 | 担当 | 何をするか | インプット | アウトプット |
|---|---|---|---|---|
| 1 | unit-tester | 単体試験計画に沿って実施・記録 | docs/build/単体試験計画.md+実装 | docs/test/単体試験結果.md |
| 2 | integration-tester | 結合試験計画に沿って実施・記録 | docs/design/結合試験計画.md+実装 | docs/test/結合試験結果.md |
| 3 | system-tester | 総合試験計画に沿って実施、または利用者実施を支援 | docs/requirements/総合試験計画.md | docs/test/総合試験結果.md |

- **完了条件**: 単体・結合・総合のすべてが合格（または残不具合がユーザー合意の上でクローズ）。
- **戻り条件**: 試験NG→ builder へ修正依頼し再試験。NGの原因が設計/要件起因なら**該当フェーズへ差し戻し（ユーザーに諮る）**。

---

## 5. 教訓抽出フェーズ

| 順 | 担当 | 何をするか | インプット | アウトプット |
|---|---|---|---|---|
| 1 | (learn skill) | lessons_extract.py で教訓候補を機械抽出 | 全レビュー記録＋試験結果 | state/lessons_extract_result.json |
| 2 | lessons-distiller | 抽出結果を読み、規約への差分提案を生成 | 抽出結果＋docs/conventions/ | state/lessons_proposal.md |
| 3 | (ユーザー) | 各提案を採用/却下 | 差分提案書 | 承認結果 |
| 4 | (learn skill) | 承認された提案を規約に反映、監査ログ追記 | 承認結果 | docs/conventions/ 更新＋docs/lessons.md |

- **完了条件**: 抽出→蒸留→承認→反映が完了（候補0件の場合はスキップで完了）。
- **戻り条件**: なし（教訓抽出は最終工程のため差し戻し先がない）。

---

## 横断ルール（全フェーズ共通）
- **承認ゲート**: 要件定義のみ（プランモードの承認で実施）。
- **判断エスカレーション**: 担当が判断に迷う点を「要ユーザー判断」として返したら、推測せず AskUserQuestion でユーザーへ質問し、回答を担当に戻す。
- **ユーザー判断が要る時（停止しない）**: 各フェーズで未是正の「高」指摘が残る等、ユーザーの判断・確認が
  要る場合は、ターンを終えて待つのではなく **AskUserQuestion でユーザーに報告・判断を仰ぐ**。フェーズ間は
  止まらず自律進行する（裸のターン終了は Stop フック hook_continue_flow が継続強制し、進捗なく滞留した
  場合のみ上限で人手に戻る）。


## レビューの集約方式（全レビュー共通・フラット化モデル）
design-reviewer / build-reviewer / 各試験計画レビューは **integrator**（統合＋判定役）。
観点サブの起動は **main（ディスパッチャ）** が担当し、Task 結果を state/review_results/&lt;NODE&gt;.jsonl
に保存する。integrator は state を読んで所見を統合し、規約形式の「レビュー記録」に書いて advance する。
利用側は docs/conventions/reviewers.json を編集して、使う観点を増減できる。
- キー体系: `sys_review` / `des_review` / `int_review` / `bld_review` / `unit_review`（ノード名の小文字形と一致）
- build 既定 (`bld_review`): build-convention-reviewer（自作）＋ 公式 code-reviewer / silent-failure-hunter / type-design-analyzer / code-simplifier
- 独自の観点サブエージェントを足す場合: プロジェクトの .claude/agents/ にエージェントを置き、その name を reviewers.json の該当キーに追加。
- 無効待機: comment-analyzer / pr-test-analyzer（PR前提。必要時に有効化）。
- **不変条件**: サブエージェントは他のサブエージェントを起動しない（hook_check_subagent_launch が機械的に block）。

## 依存する公式プラグイン
- pr-review-toolkit … build-reviewer が束ねる公式観点エージェントの供給元。
- security-guidance … 構築フェーズで PreToolUse 自動発火するセキュリティ・ガードレール。

## トレーサビリティと整合性（全フェーズ共通）
- **ID-first**: 各担当が内容を書く前に scripts/next_id.py で採番（REQ/DES/TEST、コードはタグ）。
- **設計書はHTML**: <section data-trace-id="DES-NNN" data-trace-req="REQ-xxx"> で単位とIDを表す。
- **対応表**: docs/trace/traceability.json に集約（各ノードに内容ハッシュ、各リンクに整合済み上流ハッシュ）。
- **整合性ゲート（各フェーズ完了条件に追加。担当は flow-state.md「ゲート手順」が正本）**:
  - 決定論スキャナ scripts/trace_scan.py 等 … 未採番・書式・重複・参照整合・双方向・カバレッジ。high で不合格。
  - 軽量ゲート（REQ_GATE/DES_GATE）は gate-runner がスキャナの exit code のみで判定（意味整合は
    各フェーズのレビュー観点に吸収）。重ゲート（BLD_GATE/T_GATE）のみ consistency-checker が
    変更の意味分類・影響調査・矛盾検出（AI主導＋ガード）と suspect/clear 管理を行う。
- **規約**: docs/conventions/trace-conventions.md。
