# ccd-flowkit 成果物テンプレート集

書式起因ループの構造的根絶を目的とした、成果物ごとの正準テンプレート集。担当エージェント
は成果物を新規作成/更新するとき、まずこのディレクトリの該当テンプレートを Read し、
プレースホルダを埋める形で書き進める。書式規約が review-conventions.md /
trace-conventions.md / document-standards.md に分散していた状態を、テンプレそのものを
「書式の正本」に一元化する。

## 導入経緯（設計方針 P1 単一正本／P2 機械化）

書式起因 high の主要因として、以下の構造的問題が観測された:
1. 要件定義書 `**REQ-nnn**` 太字マーカー欠落 → trace_scan で req_set が空集合となり、上流参照が全件 dangling 化
2. 総合試験計画 `[D:id=value]` 例示のコードフェンス漏れ → ontology_scan 誤検知
3. レビュー記録 `[D:id=value]` 例示のコードフェンス漏れ（regression しやすい）
4. 実装ソース `[E_n]` 根拠コメント欠落
5. 単体試験計画 `[D:id=value]` 例示のコードフェンス漏れ

`[D:id=value]` 例示のフェンス漏れが最頻出、次いで REQ 記法欠落、`[E_n]` コメント欠落。
書式規約が分散していて LLM が横断参照して書式を都度再構成 → 抜け・揺れ が発生する構造。
テンプレをそのままコピー→穴埋めに置換することで、書式抜けを構造的に防ぐ。

## ファイル一覧

| テンプレ | 対象成果物 | 主要な必須マーカー |
|---|---|---|
| `requirements.md` | `docs/requirements/要件定義書.md` | `**REQ-NNN**` 太字マーカー |
| `test_plan.md` | `docs/{requirements,design,build}/{総合,結合,単体}試験計画.md` | `**TEST-NNN**` + `**トレース**:` |
| `review_record.md` | `docs/*/{...}レビュー記録.md`（5種共通） | `**起動サブエージェント**:`・`**総合判定**:` |
| `review_record_props.json` | 上記5種のノード固有プロパティ表 | integrator が委任時のノード名で lookup |
| `snippets/` | 全成果物で共通する例示スニペット | `[D:id=value]`・REQ 見出し・トレース行など |

## プレースホルダ規約

| 記法 | 意味 | 対応 |
|---|---|---|
| `<FILL: <説明>>` | LLM が中身を1回埋める | 埋めた後にプレースホルダごと削除 |
| `<FILL-COUNT: <説明>>` | N 個繰り返して埋める | 繰り返し後にプレースホルダごと削除 |
| `<OPT: <説明>>` | 必要時のみ追加 | 不要なら丸ごと削除（節ごと） |
| `<GEN: python <script> <args>>` | 決定論部分（連番・採番）を機械生成 | スクリプト実行結果で置換 |
| `<!-- KEEP: <理由> -->` | この行/構造は必ず残す（必須マーカー） | 削除禁止・スキャナが検査 |

## 使い方（担当エージェント側）

例: designer が設計書を書く場合（将来 design.html テンプレを追加後）:

1. `plugins/ccd-flowkit/references/templates/design.md` を Read（テンプレ本体）
2. `plugins/ccd-flowkit/references/templates/snippets/decision_token_example.md` を Read（[D:] 例示）
3. テンプレを対象パス（`docs/design/設計書.html`）にコピー
4. `<FILL:>` / `<OPT:>` / `<GEN:>` を順次埋め、不要な `<OPT:>` は節ごと削除
5. `<!-- KEEP: -->` マーカーは削除しない
6. Write 完了後、担当スキャナ（trace_scan・ontology_scan 等）で書式検証

integrator（レビュー記録作成）の場合は `review_record_props.json` から自ノードのプロパティ
を取得し、テンプレのプロパティ差し込み位置に反映してからコピーする。

## テンプレバージョン

各テンプレの先頭行 `<!-- template: <name> v<version> -->` でバージョンを埋め込む。
review_authenticity_scan / trace_scan は将来このマーカーを検査し、古いバージョンで生成
された成果物を警告する。

## 段階的導入

- Phase 1a: テンプレファイルの追加（既存挙動は変えない）。各テンプレは snippets を参照する構造
- Phase 1b: エージェント md への手順差し込み
- Phase 1c: スキャナ側の必須マーカー機械強制
- Phase 1d: テンプレ回帰テスト

## 関連ドキュメント

- `../review-conventions.md`: 再レビュー3成分・レビュー記録の書式
- `../trace-conventions.md`: トレース行の書式・上流ID規約
- `../document-standards.md`: 全成果物共通の文書規約
- `../evidence-conventions.md`: `[E_n]` エビデンス参照の書式
