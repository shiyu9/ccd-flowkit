# Changelog

本プロジェクトのすべての注目すべき変更は、このファイルに記録される。
形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に準拠し、
バージョニングは [Semantic Versioning](https://semver.org/lang/ja/) に従う。

## [0.2.1] - 2026-07-15

依存プラグイン解決の記述誤りを修正するホットフィックス。v0.2.0 では `dependencies` の記述形式が公式仕様と食い違っており、cross-marketplace 依存の解決が失敗して `/ccd-flowkit:run` を含むプラグイン機能全体が無効化される事象があった。

### Fixed

- **`plugin.json` の `dependencies` 記述形式**: `"pr-review-toolkit@claude-plugins-official"` の結合文字列形式（`enabledPlugins` や CLI 用の記法）から、公式仕様に沿ったオブジェクト形式 `{ "name": "pr-review-toolkit", "marketplace": "claude-plugins-official" }` に修正。誤った形式では `@claude-plugins-official` 部分がプラグイン名として解釈され、依存解決に失敗していた。
- **`marketplace.json` の cross-marketplace 依存許可**: `allowCrossMarketplaceDependenciesOn: ["claude-plugins-official"]` を追加。別マーケットプレイス（`claude-plugins-official`）にある `pr-review-toolkit` / `security-guidance` への依存を許可する必須設定で、無いと `cross-marketplace` エラーで install が失敗する。

### Changed

- `author.name` / `owner.name`: プレースホルダの `Your Team` を `Aslan` に置換。

## [0.2.0] - 2026-07-14

初の公開リリース。フラット化モデル・成果物テンプレート化・非同期待機機構撤去の3大改善を含む。

### Added

- **成果物テンプレート集** (`references/templates/`): レビュー記録・要件定義書・試験計画の共通テンプレートと、決定参照トークン (`[D:id=value]`)・REQ 太字マーカー・トレース行・エビデンス参照のスニペット集を追加。担当エージェントは書き出し前にテンプレを必ず Read する。
- **template_residue_scan.py**: テンプレコピー時のプレースホルダ削除忘れ（`<FILL:>` `<OPT:>` `<GEN:>` 等）を全ゲートで機械検出。
- **integrator モデル**: `state/review_results/<NODE>.jsonl` を介した「main → 観点サブ並列起動 → state 保存 → integrator が統合＋判定＋advance」の三段構成。5レビューノード全てで採用。
- **check_rerun_limit.py**: レビュー内 rerun 要求の上限判定（同一ラウンド 2 回まで）。超過時は AskUserQuestion で諮る（強制収束はしない）。
- **hook_check_state_write.evaluate_review_results**: `state/review_results/*.jsonl` への Write/Edit を main 専用に限定（Bash 経路と対称のガード）。
- **hook_check_advance_actor.evaluate_rerun_actor**: rerun 上限判定と `state/review_results/` 書込の実行主体を main に限定。
- **sub → sub 起動の無条件禁止**: `hook_check_subagent_launch` を「agent_type 付き Task|Agent は無条件 deny」に一般化。
- **並列レビュー**: 観点サブは単一メッセージで並列 Task 起動を推奨（BLD_REVIEW の 5 観点並列で所要時間を最も重い観点の実行時間に短縮）。

### Changed

- **reviewers.json のキー体系**: `sys_review` / `des_review` / `int_review` / `bld_review` / `unit_review`（ノード名の小文字形と一致・`node.lower()` で直接キー引き）。
- **manage_flow_state.py の `wait` サブコマンドを deprecated 化**: フラット化モデルでは Task が同期呼び出しで復帰時点で advance 済みのため polling 不要。SKILL.md 手順は `current` を1回呼び出す方式に単純化。
- **静的検査も evidence JSON に登録**: 試験終了直前に result_scan 相当の自己検証で宣言合否と evidence JSON の合計を突合する。

### Fixed

- **template_residue_scan の偽陽性**: `docs/conventions/` `docs/references/` `docs/**/templates/` はデフォルト走査から除外（規約文書がプレースホルダの書き方を例示することを許容）。

### Removed

- **旧集約役モデルの階層構造**: `main → 集約役 → 観点サブ×N` の構造は Claude Code Task 非同期モデル（子完了通知が親でなく main へ届く仕様）と不整合であり構造欠陥を持っていたため撤去。フラット化モデルへ移行。
- **`children_running` allow 分岐**（hook_enforce_advance）・**`-reviewer` の `run_in_background` 特例**・**逆委任個別ガード**（hook_check_subagent_launch）: サブ→サブ起動の無条件禁止に一般化することで不要になったため削除。

### Documentation

- **フラット化モデルフローチャート** (`references/diagrams/flowchart_v3_current.html`): 状態機械・レビュー内部フロー・フック配置・integrator モデル構造差分を単一 HTML で可視化。

[0.2.1]: https://github.com/shiyu9/ccd-flowkit/releases/tag/v0.2.1
[0.2.0]: https://github.com/shiyu9/ccd-flowkit/releases/tag/v0.2.0
