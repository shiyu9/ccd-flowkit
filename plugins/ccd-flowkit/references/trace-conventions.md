# トレーサビリティ規約

成果物にIDを通し、要件→設計→コード→試験を相互に追跡する。原則は **ID-first**
（IDを先に発行してから内容を書く）。採番は各担当が `scripts/next_id.py` で行う。

## ID体系（接頭辞は役割で1対1）
| 接頭辞 | 採番する担当 | 採番単位 | 形式 |
|---|---|---|---|
| REQ- | project-planner | shall文 1つ | REQ-NNN |
| DES- | designer | コンポーネント/IF 1つ（HTMLの<section>1つ） | DES-NNN |
| TEST- | 各試験計画担当 | 試験項目 1つ | TEST-NNN |
| コードタグ | builder | 公開シンボル/モジュール 1つ | コメントに DES-NNN を記載 |

## 採番台帳（high-water mark, 統合）
traceability.json の予約キー `"_allocator"` に接頭辞ごとの発行済み最大番号を保持する。
この値は採番のたびに増えるだけで**減らない**ため、末尾項目を削除しても番号は再利用されない
（IDは永久欠番）。手動で台帳より大きいIDを足した場合は実在最大と突き合わせて矛盾を防ぐ。
予約キーは `_` で始まり、ID形式(REQ-NNN等)と衝突しない。スキャナは `_` 始まりキーを無視する。
例: `"_allocator": { "REQ": 5, "DES": 12, "TEST": 30 }`

## 採番手順（ID-first）
内容を書く前に次IDを取得する：
`python ${CLAUDE_PLUGIN_ROOT}/scripts/next_id.py DES`  → 例 DES-014
得たIDを器として先に置き、その中に内容を書く（後貼りしない）。

## 成果物ごとの記法
- 要件（docs/requirements/要件定義書.md, Markdown）: 箇条書き先頭に `**REQ-001**`。
- 設計（docs/design/設計書.html, **HTML**）: 各単位を <section> にし、属性でIDと上流REQを持つ。
  ```html
  <section data-trace-id="DES-001" data-trace-req="REQ-001">
    <h2>認証モジュール</h2>
    <p>IF: login(email, password) -> Token</p>
  </section>
  ```
  複数のREQに紐づく場合は空白区切り: data-trace-req="REQ-001 REQ-004"
  他の設計単位に依存する場合は data-trace-deps に依存先DESを空白区切りで記す:
  `<section data-trace-id="DES-005" data-trace-req="REQ-007" data-trace-deps="DES-003">`
- コード（src/）: 公開シンボルの直前コメントに設計IDを注記。例 `# DES-001 [REQ-001]`
- 試験（docs/.../*試験計画*.md）: 各試験項目の見出しに正準ID `TEST-001`（3桁固定。`#001` 等の
  別記法は使わない）を置き、**見出し直下に正準トレース行**を1行記す:
  `**トレース**: <上流ID群>`（REQ-/DES- を空白またはカンマ区切り。全角コロン可）。
  ```markdown
  ### TEST-001 add コマンド正常系
  **トレース**: DES-002, REQ-001
  ```
  レベルごとの必須上流（ファイル名で判定）:
  | 試験計画 | 必須上流 |
  |---|---|
  | 総合試験計画（docs/requirements/） | REQ を1つ以上（関連 DES も可） |
  | 結合試験計画（docs/design/）       | DES を1つ以上（関連 REQ も可） |
  | 単体試験計画（docs/build/）         | DES を1つ以上（関連 REQ も可） |

## トレーサビリティ・マトリクス（docs/trace/traceability.json）
台帳兼 suspect 管理。各ノードは内容ハッシュを持ち、各下流リンクは「整合済み上流ハッシュ」を持つ。
**トップレベルのキーは ID（`REQ-NNN` / `DES-NNN` / …）か予約キー（`_` 始まり）。**
`nodes`/`links` のようなラッパーで包まない（スキャナはトップレベルのIDキーを直接読む）。
REQ ノードは下記、DES ノードは「設計間の依存」節の dependsOn 等を持つ。スキャナはキーの接頭辞で
種別を判定し、REQ/DES が要件・設計に実在するかを突合する（実在しないIDは幽霊IDで high）。
```json
{
  "REQ-001": {
    "hash": "<reqの内容ハッシュ>",
    "design": ["DES-001"],
    "code": ["src/auth.py:login"],
    "test": ["TEST-101"],
    "verifiedAgainst": { "DES-001": "<DES-001整合時のREQ-001ハッシュ>" }
  }
}
```
- **test 列の充填**: REQ.test 配列は consistency-checker が試験フェーズゲートで充填する。
  試験計画の各 TEST-ID と「対応 REQ-ID」の対応を AI が読み取り、該当する TEST-ID を登録する。
  trace_scan.py は試験結果が存在するのに REQ.test が空のままの場合を medium で警告する
  （充填漏れの安全ネット）。
- 上流ハッシュが変わり、下流の verifiedAgainst と一致しなくなったら、その下流リンクは **suspect**。
- 下流を整合させ、裏取り（再試験/型OK）と根拠記録が揃ったら verifiedAgainst を現在値に更新して **clear**。
- **design_pending（変更モードの設計猶予フラグ）**: デルタフロー（変更モード）で要件を新設した時、
  設計は後工程なのに設計書が既存のため trace_scan の REQ→設計カバレッジが high になり
  REQ_GATE が構造的に止まる（waiver 頼みになるのを防ぐ対策）。**要件改訂時に
  planner が新設 REQ の台帳ノードへ `"design_pending": true` を付ける**と、trace_scan は当該 REQ の
  未カバーを medium（coverage_pending）に下げて REQ_GATE を通す。**designer は設計反映後に
  フラグを除去する**（除去忘れは coverage_pending_stale の medium で促され、BLD_GATE の
  consistency-checker が pending 残置を意味整合で差し戻す）。通常フロー（設計書がまだ無い）では不要。

## 設計間の依存（feature-dependency-graph）
設計コンポーネント（DES）どうしの依存を記録し、横展開漏れ（ある設計を変えたとき、
それに依存する設計の確認漏れ）を防ぐ。粒度は設計書まるごとでなく DES（コンポーネント/IF）単位。

- **正本**: 設計HTMLの <section> 属性 `data-trace-deps`（依存先DESを空白区切り）。
  設計者は「依存先」だけを書く。被依存（誰から依存されるか）は書かない。
- **台帳（traceability.json）の各DESノード**:
  - `dependsOn`: 依存先DESの配列（data-trace-deps の写し）。
  - `dependedOnBy`: 被依存DESの配列（dependsOn から自動逆引き。手書き禁止）。
  - `depsVerifiedAgainst`: 各依存先について「整合確認時の依存先内容ハッシュ」。依存先の
    ハッシュが変わり一致しなくなったら、その依存リンクは suspect（要再確認）。
- **チェックの分担**:
  - 決定論（trace_scan.py）: 依存先の実在（dangling）、自己依存、循環依存。high で fail-closed。
  - 逆引き生成（dependedOnBy）と suspect 判定（横展開漏れ）は consistency-checker（AI主導＋ガード）が担う。

## チェック（フェーズゲートで実行）
- 決定論スキャナ `scripts/trace_scan.py`：未採番・書式・重複・参照整合・双方向・カバレッジ・
  設計間依存(deps)の実在/自己依存/循環。high が1件でもあればフェーズ不合格（fail-closed）。
- **実装トレース（DES→code）の検査分担**: builder が src/ の公開シンボル直前コメントに `# DES-NNN [REQ-xxx]`
  を宣言する（正本＝コード側マーカー）。**trace_scan.py がそのマーカーを収穫し、決定論で検査する**:
  実在しない DES を指すマーカー＝dangling(high)、コードがある時に未実装の設計（マーカー無しの DES）＝
  coverage(high)。実装リンクは要件に直結せず設計を経由する（REQ→DES→code でたどる）。検査はコードがある
  build ゲート以降で発火する（設計フェーズでは src/ 空のため未実装DESを誤検出しない）。
  - 台帳 traceability.json の **design / code 列は `scripts/trace_fill.py` が機械転記する**
    （REQ.design＝設計HTMLの data-trace-req 反転、DES.code／REQ.code＝マーカー位置の導出。
    consistency-checker が BLD_GATE/T_GATE で実行する。列は属性・マーカーから導出される
    ビューであり手書きしない——AI充填依存で REQ の design/code が空のまま完走するのを防ぐ）。
    マーカー漏れを含む公開シンボル網羅の意味検査は LSP（documentSymbol）と consistency-checker が
    担う（決定論＝マーカー収穫/転記/カバレッジ、意味＝網羅性・妥当性、の役割分担）。
  - **マーカーは対象シンボルの直前の独立した行**にコメントとして書く（`# DES-NNN [REQ-xxx]`）。行末注記
    （`def f():  # DES-001`）や docstring/サンプルコード内には書かない。DES は採番と同じ3桁固定（DES-NNN）。
  - MVP のマーカー収穫は `#`/`//`/`;`/`--` 等のコメント様式・SOURCE_EXTS のソース拡張子が対象。
    字句解析はしないため docstring 内の行頭コメント風行を誤収穫しうる・行末注記は収穫しない、を限界として認識する。
- **試験トレース（TEST→上流）の検査分担**: 各試験計画担当が試験項目に正準トレース行 `**トレース**:` を
  宣言する（正本＝計画側のトレース行）。**trace_scan.py が収穫し、決定論で検査する**:
  - 参照整合: トレース行の REQ/DES が実在するか（実在しない＝dangling, high）。
  - 型(presence): 各試験がレベル相応の上流型を持つか（総合→REQ / 結合・単体→DES が無い＝high）。
  - REQ→総合試験 カバレッジ: 総合試験計画がある時、総合試験で参照されない REQ＝未網羅(high)。
  - DES→単体/結合試験 カバレッジ: 単体試験計画がある時、単体/結合のどちらでも参照されない DES＝未網羅(high)。
  REQ→実装は REQ→DES→code で辿るのと対に、要件・設計が試験で確認されることを縦に担保する。
  台帳 traceability.json の REQ.test 充填は consistency-checker が試験フェーズで行い、trace_scan は
  試験結果がある時に REQ.test 空を medium で警告する（台帳記録の安全ネット）。
- 変更の意味解釈・影響調査・矛盾検出・suspect判定は consistency-checker（AI主導＋ガード）が担う。
