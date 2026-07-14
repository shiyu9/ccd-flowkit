<!-- template: requirements v1 -->
<!-- 使い方: -->
<!--   1. project-planner または要件担当エージェントが本テンプレを Read -->
<!--   2. templates/snippets/req_marker_example.md を Read（**REQ-NNN** 太字マーカー必須の理由と例示） -->
<!--   3. templates/snippets/decision_token_example.md を Read（決定参照トークン [D:id=value] のフェンス規約） -->
<!--   4. 本テンプレを docs/requirements/要件定義書.md にコピー -->
<!--   5. <FILL:> / <FILL-COUNT:> / <OPT:> を順次埋める -->
<!--   6. <!-- KEEP: --> マーカー行は削除しない（trace_scan が検査） -->

# 要件定義書

## 概要
<FILL: プロジェクト目的・対象・スコープ 3-5行>

## 用語・略語
<OPT: プロジェクト固有の用語表>

## 決定値方針
<!-- 決定判断由来の値は decisions.md/json に記録し、参照は [D:id=value] トークンで書く。 -->
<!-- 例示（値を実際に参照しない説明目的の記述）は必ずコードフェンスで囲む。 -->
<!-- 詳細: templates/snippets/decision_token_example.md 参照 -->

<FILL: 決定値方針の概要。各決定値の意図と境界ケースの挙動を明示>

<OPT: 例示スニペット（決定値トークンの使い方を要件定義書内で示す場合）:

決定判断由来の値は以下の形式で参照する:

```
[D:default_limit=10]
[D:id_reuse_policy=disallow]
```
>

## 要件一覧

<!-- REQ-NNN の見出しは必ず `### **REQ-NNN** タイトル` の形式で書く -->
<!-- 太字マーカー **REQ-NNN** が無いと trace_scan.py の req_set が空集合になり、 -->
<!-- 総合試験計画のトレース行が全件 dangling high になる（被害範囲最大級） -->
<!-- 詳細: templates/snippets/req_marker_example.md 参照 -->

<FILL-COUNT: 各要件に対して以下の構造で:

### **REQ-<GEN: 通し番号を採番。既存要件があれば末尾から続ける（例: REQ-001, REQ-002, ...）>** <FILL: タイトル>  <!-- KEEP: trace_scan 必須マーカー -->

**概要**: <FILL: 1行で責務>

**詳細**: <FILL: 段落。ユーザーストーリー・想定シナリオ・制約>

**境界**:
- 最小: <FILL: 最小ケースでの挙動>
- 最大: <FILL: 最大ケースでの挙動>
- 空: <FILL: 空データでの挙動>
- 全削除後: <FILL: 全削除後の状態>
<OPT: - その他境界: <FILL> >

**受入条件**:
- AC1: <FILL: 検証可能な条件>
- AC2: <FILL: 検証可能な条件>
- AC3: <FILL: 検証可能な条件>
<OPT: - AC4: <FILL> >

<OPT: **関連する決定値**: [D:id=value]（コードフェンス外に書くのは実参照時のみ。例示はフェンスで囲む）>
>

## 非機能要件

<FILL-COUNT: 性能・セキュリティ・可用性・保守性 等>

## 実装制約
<OPT: 使用ライブラリ・言語制約・環境制約（import 制限などがある場合に明記）>

## 関連ドキュメント
<OPT: 上流資料・参考仕様書へのリンク>
