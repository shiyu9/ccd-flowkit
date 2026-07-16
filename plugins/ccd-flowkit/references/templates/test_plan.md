<!-- template: test_plan v1 -->
<!-- 使い方: -->
<!--   1. 対応するプランナー（system-test-planner / integration-test-planner / unit-test-planner）が本テンプレを Read -->
<!--   2. templates/snippets/trace_line_example.md を Read（**トレース**: 行と上流ID規約） -->
<!--   3. templates/snippets/decision_token_example.md を Read（決定参照トークン [D:id=value] のフェンス規約） -->
<!--   4. 本テンプレを output_path にコピー: -->
<!--        - 総合試験 (level=sys): docs/requirements/総合試験計画.md -->
<!--        - 結合試験 (level=int): docs/design/結合試験計画.md -->
<!--        - 単体試験 (level=unit): docs/build/単体試験計画.md -->
<!--   5. <LEVEL_LABEL> / <UPSTREAM_TYPE> をレベルに応じて置換: -->
<!--        - sys → 「総合試験」/「REQ」 -->
<!--        - int → 「結合試験」/「DES」 -->
<!--        - unit → 「単体試験」/「DES」 -->
<!--   6. <FILL:> / <FILL-COUNT:> / <OPT:> を順次埋める -->
<!--   7. <!-- KEEP: --> マーカー行は削除しない（trace_scan が検査） -->

# <LEVEL_LABEL>計画

## スコープ・前提

<FILL: 対象範囲・試験環境・前提とするシステム状態>

## 共通前提条件

<FILL: 全 TEST 共通の環境・データ準備。「利用者視点」等の視点を明示>

<OPT: 例外的な項目がある場合はここに明記。共通前提と矛盾する試験手法を採る項目がある場合の可監査化:

**例外**: <FILL: 例外的な試験手法を採る TEST 項目 (例: 実装制約の性質上、静的検査を用いる項目)>
>

## 試験項目

<!-- TEST-NNN の見出しは必ず `### **TEST-NNN** タイトル` の形式で書く -->
<!-- **トレース**: 行は必須マーカー。上流IDが実在すること: -->
<!--   - 総合試験は上流 REQ (REQ-NNN) -->
<!--   - 結合/単体試験は上流 DES (DES-NNN) -->
<!-- 詳細: templates/snippets/trace_line_example.md 参照 -->
<!-- 決定値 [D:id=value] を期待結果に含める場合、必ずコードフェンスで囲む -->
<!-- 詳細: templates/snippets/decision_token_example.md 参照 -->

<FILL-COUNT: 各試験項目に対して以下の構造で:

### **TEST-<GEN: 通し番号を採番。既存項目があれば末尾から続ける>** <FILL: タイトル>  <!-- KEEP: trace_scan 必須マーカー -->

**トレース**: <FILL: 上流ID (sys → REQ-NNN / int,unit → DES-NNN)。複数はカンマ区切り>  <!-- KEEP: trace_scan 必須 -->
**分類**: <FILL: 正常 / 異常 / 境界 のいずれか>  <!-- KEEP -->
**前提条件**: <FILL: 具体的状態。「初期状態」等の抽象語を避ける>
**手順**: <FILL: 具体的コマンド。総称語「コマンド」「入力」「操作」は禁止（総称語のままだと実施者が読み出し専用の list を誤選択する等の実行時齟齬を招く）>
**期待結果**: <FILL: 具体的な期待値。決定値を含む場合はコードフェンスで [D:id=value] を明記>
**合否判定基準**: <FILL: 機械的に判定可能な形式。出力照合・終了コード・ハッシュ突合 等>

<OPT: **エビデンス**: <FILL: 手順の妥当性を支える VRR やコードベース参照 [E_n]> >
>

## 合否まとめ（結果記録時に追記）

<OPT: 試験実施後に結果ハーネスが更新する集計欄:

| 合計 | PASS | FAIL | SKIP |
|---|---|---|---|
| N | N | N | N |
>

## 除外事項（該当時のみ）
<OPT: 実施しない試験項目とその理由。「別レベルで検証」「フェーズ外」等の根拠を明示>
