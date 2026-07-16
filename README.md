# ccd-flowkit

要件定義 → 設計 → 構築 → 試験 の開発フローを、専任エージェント群によって自動進行する
Claude Code プラグイン。ユーザー承認は要件定義のみ。以降は状態機械（`state/_flow_state.json`）
に従って各担当エージェントが起動され、レビュー・ゲート・回帰試験まで含めて完走する。

## できること

- **要件定義から総合試験まで自動進行**: プロジェクト概要を渡すだけで、要件定義書・設計書・
  試験計画・レビュー記録・実装コード・試験結果までを一貫して生成する。
- **専任エージェントによる責務分離**: 各フェーズに担当エージェントを配置し、越権や役割
  混同を hook で機械的に防ぐ。
- **決定論スキャナによるゲート**: `trace_scan` / `ontology_scan` / `evidence_scan` /
  `result_scan` / `review_authenticity_scan` / `template_residue_scan` / `design_spec_scan`
  が各フェーズゲートで書式・整合・網羅を検査。
- **フラット化レビューモデル**: `main → 観点サブ並列起動 → state/review_results/ →
  integrator が統合＋判定＋advance` の三段構成。サブ→サブの起動は不変条件として禁止。
- **成果物テンプレート化**: `references/templates/` の正準テンプレートを担当エージェントが
  必ず Read してから穴埋めする。書式起因のレビュー差し戻しを構造的に削減。
- **変更モード（デルタフロー）**: 完走後の変更要望書投入で `DONE → change → RCA`
  の入口層判定へ入り、影響範囲だけを再工程する。

## インストール

### 前提

- Claude Code CLI（`claude` コマンド）
- Python 3.13 以上（標準ライブラリのみ・追加パッケージ不要）
- 併用する公式プラグイン（**別途インストール必須**）: `pr-review-toolkit` / `security-guidance`
- **osv-scanner**（依存パッケージ脆弱性スキャンで必須。BLD_GATE で自動実行される）
  - Windows: `winget install Google.OSVScanner`
  - Mac (Homebrew): `brew install osv-scanner`
  - Linux (scoop): `scoop install osv-scanner`
  - その他: [Releases](https://github.com/google/osv-scanner/releases) から SLSA3 署名済みバイナリ

### 併用プラグインの追加（必須）

v0.2.2 では自動解決を撤廃したため、以下2つを**ユーザー側で個別に install** する必要があります。

```
/plugin marketplace add anthropics/claude-code
/plugin install pr-review-toolkit@claude-plugins-official
/plugin install security-guidance@claude-plugins-official
```

### 本プラグインの追加

```
/plugin marketplace add shiyu9/ccd-flowkit
/plugin install ccd-flowkit@shiyu9/ccd-flowkit
```

または `.claude/settings.json` で設定:

```json
{
  "enabledPlugins": ["ccd-flowkit@shiyu9/ccd-flowkit"]
}
```

## 使い方

作業ディレクトリの `docs/requirements/要望書.md` にプロジェクトの要望を書いてから、以下を実行する:

```
/ccd-flowkit:run
```

または引数で概要を渡す:

```
/ccd-flowkit:run タスク管理 CLI を作りたい。JSON でファイル永続化
```

### 工程と担当

| 工程 | 流れ（担当エージェント） | 成果物 |
|---|---|---|
| 要件定義 | project-planner →（ユーザー承認）→ system-test-planner → system-test-plan-reviewer | 要件定義書 / 総合試験計画 |
| 設計 | designer → design-reviewer → integration-test-planner → integration-test-plan-reviewer | 設計書 / 結合試験計画 |
| 構築 | builder → build-reviewer → unit-test-planner → unit-test-plan-reviewer | コード / 単体試験計画 |
| 試験 | unit-tester → integration-tester → system-tester | 単体 / 結合 / 総合 試験結果 |

- ユーザー承認ゲートは**要件定義のみ**。以降は自動進行し、判断に迷う点だけ AskUserQuestion で
  ユーザーに質問する。
- レビューで未是正の「高」指摘が残ると、その工程で停止して報告し、是正→再レビューを
  繰り返す（`review_round` 上限 3 で収束規律を機械強制）。

### プランモード

要件定義フェーズは自動でプランモードへ切り替わり、「プランモードの承認＝要件定義の承認」となる。
承認後にプランモードを抜けて要件定義書を書き出し、以降の工程は自動進行する。

自動切替が効かない環境では、`plugins/ccd-flowkit/examples/settings.plan-mode.json` を
`.claude/settings.json` にコピーするとセッションがプランモードで始まる。

## フローチャート

開発フロー全体・レビュー内部構造・フック配置を可視化した公式ドキュメントを
`plugins/ccd-flowkit/references/diagrams/flowchart_v3_current.html` に同梱している
（単一 HTML・外部依存なし・オフライン閲覧可）。

このフローチャートは以下の 6 図で構成される:

1. **メインパイプライン**: 状態機械の全ノード（REQ_PLAN 〜 DONE）と遷移。
2. **設計サブフロー**: DES_DRAFT → DES_VERIFY → DES_FINAL の設計 3 ラウンド。
3. **RCA / デルタフロー**: 試験 NG 時の根本原因解析、変更モードの再工程。
4. **フック配置**: PreToolUse・SubagentStop・Stop 各フックの発火ポイント。
5. **レビュー内部フロー**: フラット化モデル（main → 観点サブ並列 → state → integrator）。
6. **集約役 vs integrator モデル構造差分**: 旧集約役モデルの構造欠陥と、フラット化に
   よる解消の対比。

## レビューのカスタマイズ

`docs/conventions/reviewers.json`（初回実行時に `references/reviewers.json` から
コピーされる）を編集して、各レビューノードで起動する観点サブを増減できる。

キー体系はノード名の小文字形と一致（`bld_review` / `des_review` / `sys_review` /
`int_review` / `unit_review`）。

- 既定の `bld_review` 観点: `build-convention-reviewer` + 公式 `code-reviewer` /
  `silent-failure-hunter` / `type-design-analyzer` / `code-simplifier`
- 独自観点の追加: プロジェクトの `.claude/agents/` に自作エージェントを置き、その name を
  reviewers.json の該当キーに追記。
- `comment-analyzer` / `pr-test-analyzer` は PR 前提のため既定で無効。PR 運用時に有効化。

**不変条件**: サブエージェントは他のサブエージェントを起動しない（`hook_check_subagent_launch`
が agent_type 付きの Task|Agent を無条件 deny）。この不変条件により、旧モデルで発生した
集約役サイクリング（子完了通知が親でなく main へ届く仕様と階層構造の不整合）は構造的に消滅する。

## 成果物の出力先

作業ディレクトリの `docs/` 配下（実ソースはリポジトリ規約に従い `src/` 等）。

- `docs/requirements/` … 要件定義書 / 総合試験計画 / 変更要望書
- `docs/design/` … 設計書（HTML）/ 決定台帳 / VRR（Verify by Reading Reference）/ 結合試験計画
- `docs/build/` … 単体試験計画 / 構築レビュー記録
- `docs/test/` … 単体 / 結合 / 総合 試験結果 / 根本原因解析
- `docs/trace/` … トレーサビリティ台帳 / 監査ログ
- `docs/conventions/` … 規約類（初回実行時に `references/` から自動コピー）

## トレーサビリティ

要件 → 設計 → コード → 試験 を ID で追跡する（規約: `references/trace-conventions.md`）。

- ID-first: 各担当が `scripts/next_id.py` で採番してから内容を書く。
- 設計書は HTML（`<section data-trace-id="...">`）。要件は Markdown の REQ- 見出し、
  試験は TEST-、コードはコメントタグ（`# DES-NNN [REQ-...]`）。
- 各フェーズ完了時に `scripts/trace_scan.py`（決定論チェック）と consistency-checker
  （変更の影響分析・矛盾検出）を通す。high があれば fail-closed で停止。

## ライセンス

MIT License — 詳細は [LICENSE](LICENSE) 参照。

## Claude Code は Anthropic の商標

Claude Code は [Anthropic](https://www.anthropic.com/) の製品・商標です。本プラグインは
Claude Code のプラグインエコシステムに従って動作するが、Anthropic が公式に配布・保証する
ものではありません。
