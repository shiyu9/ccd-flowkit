# 成果物フォーマット規約

すべての成果物は Markdown で `docs/` 配下に出力する。各文書は冒頭に
「文書名 / 版 / 作成エージェント / 作成日 / 参照した上流文書」を記載する。

| フェーズ | 成果物 | 出力先 |
|---|---|---|
| （入力） | 要望書（利用者の要望・要件定義の入力） | docs/requirements/要望書.md |
| 要件定義 | 要件定義書 | docs/requirements/要件定義書.md |
| 要件定義 | 総合試験計画 | docs/requirements/総合試験計画.md |
| 要件定義 | 総合試験計画レビュー記録 | docs/requirements/総合試験計画レビュー記録.md |
| 設計 | 設計書(HTML・人間向けビュー) | docs/design/設計書.html |
| 設計 | 設計仕様ソース(機械の正本) | docs/design/design_spec.json |
| 設計 | 設計レビュー記録 | docs/design/設計レビュー記録.md |
| 設計 | 結合試験計画 | docs/design/結合試験計画.md |
| 設計 | 結合試験計画レビュー記録 | docs/design/結合試験計画レビュー記録.md |
| 構築 | 単体試験計画 | docs/build/単体試験計画.md |
| 構築 | 単体試験計画レビュー記録 | docs/build/単体試験計画レビュー記録.md |
| 構築 | 構築レビュー記録 | docs/build/構築レビュー記録.md |
| 構築 | コード・構築物 | リポジトリの実ソース（src/ 等） |
| 試験 | 単体試験結果 | docs/test/単体試験結果.md |
| 試験 | 結合試験結果 | docs/test/結合試験結果.md |
| 試験 | 総合試験結果 | docs/test/総合試験結果.md |

## 成果物の最新性とレビュー記録の分離
- **設計書・各種計画書は常に最新状態のみを保持する**。修正履歴・過去版・レビュー記録の節を本体に
  持たせない（履歴の正本は Git のコミット履歴）。
- **レビュー記録は専用ファイルに分離する**（上表のレビュー記録ファイル）。各 reviewer が指摘・対応・
  再判定の表と、正準の `**起動サブエージェント**: ...` 宣言行（review-conventions.md）を記す。


## 設計のソースとビューの分離（design_spec.json＝機械の正本）
整合性の壊れやすい事実（IF・決定参照）は、散文でなく**構造化ソース** `docs/design/design_spec.json` を
正本として持つ（P1 単一正本・P2 機械化。同じ事実の複数記述が独立に編集されて乖離するのを構造で防ぐ）。
```json
{"version": 1, "components": {
   "DES-002": {"decisions": ["UD-05"],
               "functions": [{"name": "load_tasks", "params": ["path"],
                               "returns": "tuple[list, int]", "raises": ["SystemExit"]}]}}}
```
- **functions**: その DES が定める公開 IF（関数名・引数名列・戻り値・送出）。実装との一致を
  design_spec_scan.py（build モード）が BLD_GATE で決定論検査する（IF 乖離・設計外の公開関数を機械検出）。
- **decisions**: その DES が依拠する決定 ID（decisions.json）。dangling / provisional 参照は
  design_spec_scan.py（design モード）が DES_GATE で high とする。
- **人間向けビューは生成する**: `design_render.py render` が設計書.html の
  `<!-- GENERATED:DESIGN-SPEC -->` ブロックに IF仕様一覧を**決定値を解決済みの形**で埋め込む
  （人間はトークンでなく実値を読む）。生成ブロックは**手で編集しない**（陳腐化は design_spec_scan が
  high 検出。spec/decisions を直して render で再生成する）。
- 設計書.html の散文（アーキ・責務・「なぜ」の説明）は従来どおり designer が人間向けに書く。
  spec は散文を置き換えるものではなく、壊れやすい事実の正本を一箇所にするもの。

## トレーサビリティ（全成果物共通）
全成果物に ID を付ける（規約: trace-conventions.md）。ID-first で、内容を書く前に
scripts/next_id.py で次IDを採番する。要件は **REQ-001** 形式、設計はHTMLの
<section data-trace-id="DES-001" data-trace-req="REQ-001">、コードはコメントに DES- 注記、
試験は TEST-001。対応関係は docs/trace/traceability.json に集約する。
各フェーズ完了時に整合性ゲートを通すこと（軽量ゲート=gate-runner が決定論スキャナのみ、
重ゲート BLD_GATE/T_GATE=consistency-checker がスキャナ＋意味整合。flow-state.md「ゲート手順」が正本）。
