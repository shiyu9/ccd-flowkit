<!-- template: review_record v2 -->
<!-- 使い方: -->
<!--   1. integrator が委任時に受け取ったノード名（例: BLD_REVIEW）で review_record_props.json を lookup -->
<!--   2. 本テンプレを output_path にコピー（プロパティ表の output_path 参照） -->
<!--   3. ヘッダーの <NODE_LABEL> / <NODE> / <R> / <YYYY-MM-DD> をノード情報で置換 -->
<!--   4. <FILL:> / <OPT:> / <GEN:> を順次埋め、不要な <OPT:> は節ごと削除 -->
<!--   5. <!-- KEEP: --> マーカー行は削除しない（スキャナが検査） -->
<!--   6. 第2ラウンド以降は本ファイル末尾に区切り線 --- を入れて追記 -->

# <NODE_LABEL> レビュー記録

- 対象成果物: <FILL: プロパティ表 target 参照>
- 上流: <FILL: プロパティ表 upstream 参照。無しなら「該当なし」>
- レビューノード: <NODE>
- review_round: <R>（収束規律上限 <THRESHOLD> に対し <R>、上限内/超過）
- レビュー種別: <FILL: 初回フルレビュー(baseline 無し) / 再レビュー(3成分モデル) / 軽量スキップ>
- 作成日: <YYYY-MM-DD>

**起動サブエージェント**: <FILL: state/review_results/<NODE>.jsonl の observer 名をカンマ区切り。軽量スキップ時は「（軽量スキップ・観点サブ起動なし）」>  <!-- KEEP: review_authenticity_scan 必須マーカー -->

<FILL: 補足説明「state/_agent_invocations.jsonl の起動ログと一致することを確認済み。integrator 自身は直接分析を行わず、state/review_results/<NODE>.jsonl に保存された観点サブの結果のみを記録する」など>

## 再レビュー3成分（review-conventions.md）

### (a) 前回レビュー記録の指摘消込
<FILL: 初回時は「該当なし。本レビューが <NODE> の初回レビュー」。再レビュー時は各前回指摘を「1. 指摘X: <解消/未解消> — 現物確認結果」で列挙>

### (b) 機械diff（前回baseline時点との差分）
`<FILL: manage_review_baseline.py diff <NODE> <対象> <上流> コマンド>` の実行結果:

```
<FILL: 標準出力を逐語引用。要約禁止。status/change_ratio/full_reread_recommended を含む JSON をそのまま貼る>
```

<FILL: 変更率と閾値（3割）の判定 + 意図しない変更の有無>

### (c) 決定論チェック（毎回全量・trace_scan.py / ontology_scan.py 実出力）

`<FILL: trace_scan.py 実行コマンド>`:
```
<FILL: 標準出力を逐語引用>
```

`<FILL: ontology_scan.py 実行コマンド>`:
```
<FILL: 標準出力を逐語引用>
```

<FILL: observer の結果と integrator の再実行結果が一致することを明記>

## サブエージェント別の所見

<FILL-COUNT: 各観点サブごと1節。番号は state/review_results/<NODE>.jsonl の記録順:

### <N>. <observer 名>（判定: pass/high）  <!-- KEEP: 集計対象 -->

- **HIGH <指摘ID>**: <指摘内容を jsonl の raw から逐語抽出。要約・改変しない>
- MEDIUM <件数>件: <一覧>
- LOW <件数>件: <一覧>
- INFO <件数>件（非ブロッキング）: <一覧>

>

## 統合結果（integrator の判断）

### 前回指摘の消込判定
<OPT: 再レビュー時のみ。(a) で確認した各前回指摘の消込判定を集約>

### 新規指摘の統合（重複排除・重要度整理）

| # | 重要度 | 該当箇所 | 指摘 | 是正案 |  <!-- KEEP: テーブル形式 -->
|---|---|---|---|---|
<FILL-COUNT: 各指摘を1行で>

### 例外的な HIGH 却下（該当時のみ・裁量の可監査化）
<OPT: observer が HIGH と判定したが integrator が却下する場合。「observer の high を integrator が pass に上書きする」パターンの可監査化のため、以下を明記:

**却下対象**: <observer名> の <指摘ID>
**却下理由**: <設計との整合 / 規約の適用範囲外 / 対象成果物スコープ外> — 根拠となる規約・設計書の該当箇所を逐語引用
**observer への差戻し検討**: rerun 要求として observer に照会したか。しなかった場合の根拠  <!-- KEEP: 裁量問題対策 -->
**却下件数**: N 件

（注記: 却下は例外操作。原則として observer の判定を尊重し、意見が分かれる場合は rerun 要求で照会する）
>

## 判断エスカレーションの妥当性
<FILL: escalation-conventions.md 6基準への該当有無を明示。「該当なし」の場合もその旨を明記>

<OPT: 該当時:
**要ユーザー判断**: 該当基準<N>  <!-- KEEP -->
**状況**: <FILL>
**不明点**: <FILL>
**影響**: <FILL>
**推奨**: <FILL>
>

## 除外事項（該当時のみ）
<OPT: PR/差分前提の指摘・環境依存で本プロジェクト外の指摘等の除外理由を明記。code-reviewer が git diff 前提で動く場合など>

## 免除（ユーザー承諾済みの場合のみ）
<OPT:
**免除**: <FILL: 範囲> — ユーザー承諾（<FILL: 日付>・decisions <FILL: 参照>）  <!-- KEEP: review_authenticity_scan の承諾主張パターン検査対象 -->
>

## 軽量スキップ根拠（SYS/INT/UNIT_REVIEW の該当時のみ）
<OPT:
**継続確認**: <observer名> はスキップ — 対象・上流とも変更なし・前回 PASS 維持のため未起動  <!-- KEEP: review_authenticity_scan の coverage 判定用 -->

```
<FILL: manage_review_baseline.py diff <NODE> <対象> <上流> の標準出力（逐語引用・要約禁止）>
```
>

## 総合判定

**<FILL: pass / high / rerun>**  <!-- KEEP: main が judge -->

<FILL: 判定根拠を短く。pass なら「未是正のHIGHは0件」、high なら「未是正のHIGH<N>件」、rerun なら observer 名と理由>

<OPT: pass/high の場合の baseline 保存記録:
baseline保存: `manage_review_baseline.py save <NODE> <対象> [<上流>]` を判定確定後に実行。
>

## rerun 要求（該当時のみ・末尾配置必須）
<OPT:
**rerun 要求**: <observer名> — <理由>  <!-- KEEP: 末尾配置必須。main が judge -->
>

<!-- 第2ラウンド以降は下記の区切り線を入れて、本ファイルの下に同じ構造をコピーして追記する -->
<!-- ---
# <NODE_LABEL> レビュー記録（第2ラウンド・再レビュー）
（第1ラウンドと同じ構造・review_round=2 で埋める）
-->
