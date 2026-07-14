# オントロジー整合 規約（決定事項の正本＝参照）

## ねらい
資料間の不整合の多くは「同じ事実を複数の文書が別々に書き、片方を直しても他方が陳腐化する」
ことで起きる。これを防ぐため、**確定した設計判断を一意の正本に持ち、
下流文書は値を複製せず参照する**。オントロジー（存在する概念・個体・関係・制約を明示し、
矛盾を機械検出する考え方）の最小適用である。

## 語彙（概念と関係）
ccd-flowkit の成果物は次の個体と関係で結ばれる。

- 概念（型）: 要件(REQ) / 設計(DES) / 決定事項(UD) / 実装 / 試験(TEST)。
- 関係: REQ ⊂satisfiedBy⊃ DES、DES ⊂implementedBy⊃ 実装、TEST ⊂verifies⊃ REQ/DES、
  DES ⊂dependsOn⊃ DES（trace-conventions の data-trace-deps）、
  成果物 ⊂governedBy⊃ UD（本規約が定める新しい関係）。
- 既存の `docs/trace/traceability.json` は REQ/DES/TEST を個体、satisfiedBy/verifies/dependsOn を
  関係として持つ proto-オントロジー。本規約はそこに **UD（決定事項）を第一級の個体**として加える。

## 正本（決定台帳）: `docs/design/decisions.json`
ユーザー確定事項・設計判断のうち、下流が値として参照するものを機械可読に持つ唯一の置き場。

**境界挙動を伴う決定も正本化する**: ID採番・削除後の挙動・エラー時終了コードなど、境界（全削除・最大削除・
空など）で挙動が分かれる方針は、その境界挙動を含めて decisions.json に一意に定める（値だけでなく境界の
振る舞いも確定させ、要件⇔設計⇔試験で照合可能にする）。

**decisions.md との役割分担（値の二重持ちを避ける）**: `docs/design/decisions.md` は
ユーザー確定事項の**叙述・根拠**（evidence の ud 型が引用する出典）に専念し、スカラ値を
復唱しない（必要なら `[D:id=value]` トークンで引く）。**スカラ値の正本は decisions.json 一つ**に
限る。designer が両者を整合させて維持する（decisions.md の確定を decisions.json に反映し status を更新）。

```json
{
  "version": 1,
  "decisions": [
    {"id": "UD-06", "slug": "error_exit_code", "value": "1", "status": "confirmed",
     "evidence": "vrr:stderr_for_errors"},
    {"id": "UD-07", "slug": "usage_exit_code", "value": "1", "status": "confirmed",
     "evidence": "ud"},
    {"id": "UD-10", "slug": "invalid_id_handling", "value": "usage表示+exit1", "status": "provisional"}
  ]
}
```

- `id`: 決定の一意識別子（UD-NN）。`slug`: 主題の短い識別名。
- `value`: 正本の値（スカラ）。`status`: `provisional`（検討中）/ `confirmed`（確定）。
- `evidence`: 値の根拠への参照（`vrr:<topic>` / `ud` / `codebase:<file>:<line>` / `spec`）。
  **`confirmed` には根拠が必須**（ontology_scan が欠落を high とする）。
- ユーザー確認や verifier 検証で確定したら `provisional` → `confirmed` に更新する。確定時に根拠を併記する。
- `doc_reference`（任意）: `"not-expected"` で「下流文書からの参照トークンが構造的に不要」を
  台帳内に宣言する（例: プロセス運用上の決定で、値を述べる下流文書が存在しない）。
  **`doc_reference_reason`（理由の文字列）が必須**（欠落は台帳不備＝high）。宣言した決定の
  未参照 medium は low に格下げされ、ゲートごとの同一説明の反復が不要になる。
  乱用禁止: 安易な未参照もみ消しに使わないこと。宣言はレビュー対象であり、実際に参照が
  ある決定に付けたままにすると陳腐化として medium が出る。

## 参照トークン（下流文書の本文）: `[D:<id>=<value>]`
試験計画・設計書などが決定由来の値を述べるときは、値を直書きせずトークンで引く。
トークンには自分が想定する値を併記し、スキャナが正本と照合する。

- 例（結合試験計画）: 「usage 表示後の終了コードは `[D:UD-07=1]` を期待する。」
- **スカラ値専用**: 値に `]` を含めない（角括弧で区切るため）。終了コード・真偽・列挙の選択肢など、
  陳腐化が起きやすく照合価値の高いスカラに使う。フォーマット文字列等 `]` を含む複雑値は
  トークン化せず、その整合は consistency-checker（LLM）に委ねる。
- 規約・設計書がトークン書式を**例示**する箇所はコードスパン（`` `...` `` や ``` ``` ```）に入れる
  （スキャナはコードスパンと docs/conventions/ を走査対象から除外する）。

## ゲート（決定論）: `scripts/ontology_scan.py`
フェーズ整合性ゲートで実行する。high が1件でもあればフェーズ不合格（fail-closed）。

- 台帳不備（重複id・必須欠落・不正status・壊れたJSON）→ high。
- dangling（参照先 id が台帳に無い）→ high。
- provisional 参照（未確定の決定を確定として下流に固定）→ high。
- 値不一致（トークン値 ≠ 正本値＝複製値の陳腐化）→ high。
- 根拠欠落（confirmed の決定に evidence が無い）→ high。
- 根拠ダングリング（evidence の参照先 `vrr:<topic>`→docs/design/vrr/<topic>.md ／ `codebase:<file>` が実在しない）→ high。
- 未参照の confirmed 決定 → medium（横展開漏れの示唆）。`doc_reference: "not-expected"`
  宣言済みなら low。宣言があるのに実際は参照されている（宣言の陳腐化）→ medium。
- 台帳もトークンも無ければ何もしない（非オントロジー・プロジェクトを壊さない＝fail-open）。

## 来歴（confirmed は根拠で裏打ちする）
内部整合（トークン↔台帳の一致）は正しさを保証しない。VRR・実装と矛盾する値がトークン一致だけで
clean と判定されうる（裏取りされない値の confirmed 正本化）。これを防ぐため `confirmed` には
`evidence`（VRR/ud/codebase）を必須化する。ontology_scan が見るのは**根拠参照の存在（非空の文字列）と、
参照先(`vrr:`/`codebase:`)の実在**まで（evidence が指す VRR 文書の未作成＝ダングリングを検出する）。
形式が正しいか・値が根拠で本当に裏付くかは consistency-checker（LLM）が担う（決定論／意味の役割分担）。
`vrr:<topic>` を根拠にするなら verifier が docs/design/vrr/<topic>.md を実際に作成すること。
decisions.json はフロー実行ごとに新規作成される成果物のため、本必須化に移行負担は無い
（designer が確定時に evidence を併記する）。`evidence` は文字列参照のみ（非文字列は台帳不備＝high）。

## 役割分担（決定論 ⇔ LLM）
trace_scan / evidence_scan と同じ思想で、機械検査できる構造化部分と意味的部分を分ける。

- `ontology_scan.py`（決定論）: トークン↔台帳の構造化整合（上記）＋ confirmed の根拠参照の存在。
- `consistency-checker`（LLM）: トークン化されていない散文中の決定由来の値、複雑値（フォーマット等）、
  「本来トークンで引くべき値が直書きされている」箇所、および **confirmed の値が cited evidence・実装・
  VRR・試験結果と矛盾していないか**（UD-02=1 vs argparse=2 型）を点検し、トークン化または是正を促す。

## フェーズと時系列（要件フェーズの総合試験計画に注意）
正本 decisions.json は設計フェーズで作られるが、総合試験計画は前段の**要件フェーズ**で書かれる。
この時系列差は陳腐化の温床になる（計画が設計確定前に決定由来の値を直書きするため）。対策:
- 総合試験計画は**要件レベルの抽象度**を保ち、設計判断由来の具体値を推測で直書きしない
  （system-test-planner の規約）。設計詳細の値検証は結合/単体試験（設計・構築フェーズ）の担当。
- それでも決定由来の値に触れるなら `[D:id=value]` トークンで書く。ontology_scan は **docs/** 全体**を
  走査するため、要件フェーズで書かれたトークンも後段の**設計ゲート以降**で正本と照合される
  （＝設計ゲートが下流の再照合点。要件フェーズ時点では台帳が無く no-op）。
- フェーズ保護のため要件フェーズの成果物を設計フェーズで直接編集しない。要件レベルに保つことで
  クロスフェーズ編集を不要にする。

## 運用
- decisions.json は **designer が書く**。オーケストレーターは直編集せず designer に委任する
  （委任ガード hook が、agent_type の無いメインセッションからの decisions.json/*試験結果.md 書込を
  ブロックする＝オーケストレーターによる台帳の自己充填を防ぐ）。
- designer: 確定/検討中の決定を decisions.json に維持し、decisions.md の確定を反映して status を更新する。
  確定（status:"confirmed"）にできるのは、ユーザー確定事項(ud)なら **AskUserQuestion またはユーザーの
  明示回答が decisions.md にある**場合のみ（観測可能なエスカレーション。設計書からの自己導出で confirmed に
  しない）。
- 試験計画・設計の各担当: 決定由来のスカラ値は `[D:id=value]` で引く（直書きしない）。
- 是正したら再ゲート: 決定値を変えたら、参照する全文書のトークンも更新し ontology_scan を再実行する
  （正本を直して参照が陳腐化していないことを機械的に確認する）。
