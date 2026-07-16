# Changelog

本プロジェクトのすべての注目すべき変更は、このファイルに記録される。
形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に準拠し、
バージョニングは [Semantic Versioning](https://semver.org/lang/ja/) に従う。

## [0.3.0] - 2026-07-16

依存パッケージ既知脆弱性スキャン機能を追加。BLD_GATE で `osv-scanner` を自動実行し、high 深刻度が 1 件でもあれば fail-close で試験フェーズへ進まない。単一正本は `evidence/security/osv-scan.json`（osv-scanner の raw JSON をそのまま保存、二次ドキュメントは作らない）。

### Added

- **`scripts/security_scan.py`**: プロジェクト配下の lockfile（requirements.txt / package-lock.json / go.mod / Cargo.lock / pom.xml など 20+ 種）を osv-scanner で再帰スキャンし、`evidence/security/osv-scan.json` に raw JSON を保存する決定論スキャナ。severity 分類は `database_specific.severity` を優先し、無ければ CVSS スコア（>=7.0/high, >=4.0/medium, else/low）にフォールバック、それも無ければ medium（fail-safe）。同一 lockfile を `type=lockfile`（直接依存）と `type=unknown`（transitive 込み）で 2 度報告する osv-scanner の挙動に対して `(package@version, vuln_id)` で dedup。high があれば exit 1 で fail-close、それ以外は exit 0。osv-scanner 未 install / 実行エラー / プロジェクトルート外は exit 2 で hard-fail（未検査 green を返さない、他スキャナと同じ規約）。
- **`references/flow-state.md` の BLD_GATE スキャナ列挙に `security_scan.py` を追加**: consistency-checker が既存の review_authenticity_scan / ontology_scan / template_residue_scan / trace_scan / design_spec_scan と並列に security_scan.py を実行する。他スキャナと同じく high があれば `advance BLD_GATE high` で BLD_IMPL へ差し戻される（builder が依存を更新するか mitigation を実装）。
- **`skills/security-audit/SKILL.md`**: フェーズ外の手動再スキャン用スキル。`/security-audit` で呼び出すと `evidence/security/osv-scan.json` を `.prev.json` に退避してから `security_scan.py` を再実行し、前回スキャンとの差分（新規/解消された findings、severity 別集計の推移）を報告する。state を触らず読み取り専用の情報提供に徹する。定期監査・BLD_GATE 通過後の再確認・依存パッケージ手動更新後の即時確認に使用。
- **`tests/test_security_scan.py`**: `classify_severity` と `summarize` の純関数回帰テスト 15 件（severity 5 種の分類、CVSS fallback 3 種、dedup、バージョン差別化、mixed severity 集計、欠落フィールド防御）。

### Changed

- **README（プラグイン内・公開側）**: 前提リストに osv-scanner を追加（Windows/Mac/Linux のインストールコマンド 4 種を明記）。プラグイン内 README には「依存パッケージ脆弱性スキャン（必須の外部ツール: osv-scanner）」節を新設し、evidence 保存先・判定ルール・未インストール時の hard-fail 挙動を説明。

### 設計上の背景

- **osv-scanner を選定した理由**: Google/OSV.dev 公式（Apache-2.0・単一 Go バイナリ）、OSV.dev = GHSA + PyPA + RustSec + Go + npm + distros + OpenSSF Malicious Packages を統合するデータ源、API 無料・レート制限なし、20+ 種の lockfile を一括対応、winget/scoop/brew で導入可、Windows 対応。NVD は 2026-04 以降 CVE 濃縮を縮退させており、NVD 直取りのツールは今後品質劣化リスクがあるが、OSV はエコシステム直取りで影響が小さい。
- **単一正本は evidence/security/*.json（機械正本）**: cc-flowkit の `docs/packages/{pkg}.md` 相当は作らない。二重管理を避け、AI 判断が要る場合は既存の構築レビュー記録・設計書に埋め込む。
- **osv-scanner の install を必須とした理由**: 未検査 green を防ぐため。オプトインにすると「入れ忘れて未スキャンで通す」リスクがあり、機械化優先の原則（`references/flow-state.md` の scanner 責務分離）に反する。`winget install Google.OSVScanner` など 1 コマンドで導入できるため、必須化のコストが小さい。

## [0.2.2] - 2026-07-15

デスクトップアプリでの `dependency-unsatisfied` によるプラグイン全体除外を解消するホットフィックス。v0.2.1 での書式修正では解決しきれなかった真因（inline ロード時の ID 不一致）に対処。

### Removed

- **`plugin.json` の `dependencies` 欄**: Claude Code デスクトップアプリは有効な全プラグインを `--plugin-dir <installPath>` で inline ロードし、プラグイン ID が `<name>@inline` となる。依存解決は marketplace 修飾 ID の完全一致で行われるため、`{ "name": ..., "marketplace": "claude-plugins-official" }` と宣言しても実体の `pr-review-toolkit@inline` とは一致せず `dependency-unsatisfied` となり ccd-flowkit 全体が読み込まれない事象があった（[shiyu9/ccd-flowkit#1](https://github.com/shiyu9/ccd-flowkit/issues/1)）。ターミナル CLI とデスクトップ両環境で成立する記述方法が現状無いため、依存宣言自体を撤廃し、ユーザー側で `pr-review-toolkit` / `security-guidance` を独立に有効化する運用に切り替えた。
- **`marketplace.json` の `allowCrossMarketplaceDependenciesOn`**: `dependencies` 撤廃に伴い不要となったため削除。

### Changed

- **README（プラグイン内・公開側）**: 「依存する公式プラグイン（自動解決）」から「併用する公式プラグイン（別途インストール必須）」に文面を更新し、`/plugin install` コマンド例を追記。

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

[0.3.0]: https://github.com/shiyu9/ccd-flowkit/releases/tag/v0.3.0
[0.2.2]: https://github.com/shiyu9/ccd-flowkit/releases/tag/v0.2.2
[0.2.1]: https://github.com/shiyu9/ccd-flowkit/releases/tag/v0.2.1
[0.2.0]: https://github.com/shiyu9/ccd-flowkit/releases/tag/v0.2.0
