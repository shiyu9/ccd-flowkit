# ccd-flowkit プラグイン概要（Claude Code 向け）

このリポジトリは Claude Code プラグイン `ccd-flowkit` の配布リポジトリ。要件定義から
総合試験までの開発フローを、専任エージェント群と状態機械により自動進行するプラグインを
提供する。

## できること

- **要件定義から総合試験まで自動進行**: 要望書を投入して `/ccd-flowkit:run` を実行すると、
  以下が自動で走る:
  - 要件定義（ユーザー承認あり）
  - 総合試験計画（要件フェーズ）
  - 設計（設計書 HTML・決定台帳・VRR）
  - 結合試験計画（設計フェーズ）
  - 実装（コード生成）
  - 単体試験計画（構築フェーズ）
  - 単体 → 結合 → 総合 の各試験実施
  - 教訓抽出
- **専任エージェントによる責務分離**: 各フェーズに担当エージェントを配置し、越権や
  役割混同を hook で機械的に防ぐ。
- **決定論スキャナによるゲート**: 書式・整合・網羅を毎フェーズで検査し、未解決の高指摘が
  残る限り前進しない。
- **フラット化レビュー**: `main → 観点サブ並列 → state/review_results/ → integrator`
  の三段構成。integrator は state を読んで統合・判定するのみで観点サブを起動しない
  （サブ→サブ起動は不変条件として禁止）。
- **成果物テンプレート化**: `references/templates/` の正準テンプレートを担当エージェントが
  必ず Read してから穴埋めする。書式起因の差し戻しを構造的に削減。
- **変更モード（デルタフロー）**: 完走後の変更要望書投入で `DONE → change → RCA` の
  入口層判定へ入り、影響範囲だけを再工程する。

## フローチャート

`plugins/ccd-flowkit/references/diagrams/flowchart_v3_current.html` にフロー全体・レビュー
内部構造・フック配置を可視化した公式ドキュメントを同梱している（単一 HTML・外部依存なし・
オフライン閲覧可）。

このフローチャートは以下の 6 図で構成される:

1. **メインパイプライン**: 状態機械の全ノード（REQ_PLAN → REQ_APPROVE → REQ_WRITE →
   SYS_PLAN → SYS_REVIEW → REQ_GATE → DES_DRAFT → DES_VERIFY → DES_FINAL → DES_REVIEW
   → INT_PLAN → INT_REVIEW → DES_GATE → BLD_IMPL → BLD_REVIEW → UNIT_PLAN → UNIT_REVIEW
   → BLD_GATE → T_UNIT → T_INT → T_SYS → T_GATE → LEARN → DONE）と遷移語彙
   （done / pass / high / verify / ok / approve / revise / change 等）。
2. **設計サブフロー**: DES_DRAFT（第1ラウンド） → DES_VERIFY（`[E?]` 未検証マーカー解決）
   → DES_FINAL（第2ラウンド確定） の 3 段階。エビデンス参照の実在性を verifier が独立検証。
3. **RCA / デルタフロー**: 試験 NG 時に root-cause-analyzer が原因層を判定
   （impl / design / req / test / unitplan / intplan / sysplan）、回帰先ノードを選択し、
   src 変更ありなら全レベル機械再実行、無変更ならハッシュ突合で「再試験免除」を明示記録。
   完走後の変更要望書投入では `DONE → change → RCA` から同経路で再工程。
4. **フック配置**: PreToolUse（Bash / Edit / Write / MultiEdit / Task / Agent）・
   SubagentStop（advance 漏れ検知）・Stop（自律進行強制・裸のターン終了防止） 各フックの
   発火ポイントとガード内容（保護パス / advance 実行主体検証 / state 手書き禁止 /
   template 残置検出 / サブ→サブ起動禁止 等）。
5. **レビュー内部フロー（フラット化モデル）**: 5 レビューノード（SYS/DES/INT/BLD/
   UNIT_REVIEW）の内部フロー。main が `reviewers.json` の該当キー
   （sys_review / des_review / int_review / bld_review / unit_review）から観点サブを
   並列 Task 起動→ state/review_results/&lt;NODE&gt;.jsonl に append → integrator を Task 起動
   → integrator は state を読んで統合＋判定＋advance。
6. **集約役 vs integrator モデル 構造差分**: 旧集約役モデル（main → 集約役 → 観点サブ×N）
   の構造欠陥（Task 非同期モデルにおいて子完了通知が親でなく main へ届く仕様と階層構造の
   不整合）と、フラット化モデルによる解消（サブ→サブ起動禁止の不変条件で構造的に根治）
   の対比。

## 主要な参照ドキュメント

- `plugins/ccd-flowkit/references/flow-state.md`: 状態機械のノード担当・遷移語彙・ゲート手順
- `plugins/ccd-flowkit/references/phase-guide.md`: フェーズ別の担当・入出力・完了条件
- `plugins/ccd-flowkit/references/review-conventions.md`: 再レビュー3成分モデル・収束規律・中立性
- `plugins/ccd-flowkit/references/trace-conventions.md`: トレース行の書式・上流ID規約
- `plugins/ccd-flowkit/references/templates/`: 成果物テンプレート集
- `plugins/ccd-flowkit/skills/run/SKILL.md`: メインの実行スキル（ディスパッチャ）

## Claude Code への注意

- Python 3.13 以上（標準ライブラリのみ）
- Claude Code CLI が Python 実行子として `python` を PATH に持つこと（Windows / Unix）
- 依存する公式プラグイン: `pr-review-toolkit` / `security-guidance`
