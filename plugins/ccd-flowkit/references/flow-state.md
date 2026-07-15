# 開発フロー 状態機械（flow-state）

オーケストレーターは **状態機械 `state/_flow_state.json` の現在ノードを読み、対応エージェントを起動するだけ**。
判断・編集・state 更新はしない。**state 更新は各サブエージェントが完了時に `manage_flow_state.py advance` で行う**。
遷移は決定論（`scripts/manage_flow_state.py` の TRANSITIONS 表）。

## ノード → 担当エージェント
| ノード | 担当 | 説明 |
|---|---|---|
| REQ_PLAN | project-planner | 要望書→要件案（plan mode） |
| REQ_APPROVE | （ユーザー） | 承認ゲート。オーケストレーターが AskUserQuestion で仲介 |
| REQ_WRITE | project-planner | 要件定義書 書き出し |
| SYS_PLAN | system-test-planner | 総合試験計画 |
| SYS_REVIEW / SYS_FIX | system-test-plan-reviewer / system-test-planner | レビュー→是正 |
| REQ_GATE | gate-runner | 要件フェーズ ゲート（決定論スキャナのみ） |
| DES_DRAFT / DES_VERIFY / DES_FINAL | designer / verifier / designer | 第1R→[E?]検証→第2R確定 |
| DES_REVIEW / DES_FIX | design-reviewer / designer | レビュー→是正 |
| INT_PLAN / INT_REVIEW / INT_FIX | integration-test-planner / integration-test-plan-reviewer / integration-test-planner | 結合試験計画 |
| DES_GATE | gate-runner | 設計フェーズ ゲート（決定論スキャナのみ） |
| BLD_IMPL | builder | 実装 |
| BLD_REVIEW / BLD_FIX | build-reviewer / builder | レビュー→是正 |
| UNIT_PLAN / UNIT_REVIEW / UNIT_FIX | unit-test-planner / unit-test-plan-reviewer / unit-test-planner | 単体試験計画 |
| BLD_GATE | consistency-checker | 構築フェーズ ゲート（DES→code） |
| T_UNIT / T_INT / T_SYS | unit-tester / integration-tester / system-tester | 単体→結合→総合 試験 |
| T_GATE | consistency-checker | 試験フェーズ ゲート（result/ontology/trace/consistency） |
| RCA | root-cause-analyzer | 試験NG/ゲートhigh の根本原因解析→回帰先判定（変更モードの入口層判定も担当） |
| LEARN | lessons-distiller | 教訓抽出 |
| DONE | — | 終端（変更要求は `change`→RCA で再入＝デルタフロー） |

## outcome 語彙（advance の第2引数）
- `done`: 生成/是正の完了（次ステップへ）
- `pass`: レビュー/ゲート合格 ／ `high`: 未是正「高」→ 是正へ
- `approve` / `revise`: ユーザー承認 / 要修正
- `verify`: 設計に [E?] があり検証要 ／ `ok` / `fail`: verifier 結果
- `ng`: 試験 NG → root-cause-analyzer へ
- `impl` / `design` / `req`: RCA の原因層判定 → 回帰先（構築是正 / 設計是正 / 要件承認）
- `test` / `unitplan` / `intplan` / `sysplan`: RCA が**原因は試験成果物側**（実装・設計・要件は正しい）と
  判定した場合の回帰先。`test`＝結果文書・ハーネス起因→T_UNIT（tester が結果を是正して再通過）、
  `unitplan`/`intplan`/`sysplan`＝試験計画の期待値誤り→UNIT_FIX/INT_FIX/SYS_FIX（planner が計画を
  是正→再レビュー→ゲート→全試験再実行）。実装が正しいのに impl 判定で無意味な構築回帰をしない。
- `change`: **変更モード（デルタフロー）**。DONE の後の変更要求を root-cause-analyzer が受理して
  RCA へ再入し、変更の入口層（req/design/impl/test系）を判定して回帰する。

## 変更モード（デルタフロー・R3）
完成（DONE）後の変更要求はフルフロー再走ではなく、**影響範囲だけを再工程する**:
1. ディスパッチャは current=DONE で変更要求（ユーザー入力、または docs/requirements/変更要望書.md で
   **版番号が根本原因解析.md の受理記録に無い**もの＝未処理）を受けたら、`manage_active_skill.py
   acquire test` のうえ root-cause-analyzer を起動する（変更要求本文を渡す）。**完了通知後にディスパッチャが
   `release test` する**（明示ペア。ループのフェーズ追跡と混ぜない）。
2. root-cause-analyzer は **`advance DONE change` を先に実行**（RCA へ遷移。失敗したら受理記録を
   書かずに失敗を報告して完了＝要求が「処理済み」に見えて喪失するのを防ぐ）→ traceability から
   影響集合を特定 → 受理記録（**要望書の版を明記**）を根本原因解析.md へ追記 →
   `advance RCA <層>`（req=要件改訂が要る / design / impl / test系）。
3. 以降は既存の回帰経路。**再工程のコストは影響範囲に比例する**——変更の無い成果物の再レビューは
   3成分モデルの機械 diff で軽く、src 無変更の試験はハッシュ免除記録で通る（test-conventions.md）。
4. **変更要望書は「版」を必須とする**（増分。例 1.0→1.1）。受理判定は版の突合＝決定論
   （同一テーマの改訂要望を「受理済み」と誤認して黙って落とさないため）。
5. **新設 REQ には design_pending フラグ**（trace-conventions.md）: 要件改訂時に planner が
   台帳の新 REQ ノードへ `"design_pending": true` を付け、REQ_GATE の設計カバレッジを medium に
   猶予する（waiver 頼み構造の解消）。designer が設計反映後に除去する。

## 重要な構造的保証
- **再レビュー必須**: `*_FIX --done--> *_REVIEW`。是正の次は必ず再レビュー（自己確認で飛ばせない）。
- **回帰**: 試験NG→RCA→(impl/design/req/test)。回帰先の是正後は前進経路をたどり、以降の全テストレベルを再実行する
  （BLD_FIX→BLD_REVIEW→…→T_UNIT→T_INT→T_SYS、DES_FIX→…→再ビルド→全試験）。
- **回帰後の再試験は実体を伴う**（test-conventions.md「回帰後の再試験の実体化」）: 回帰後の T_* ノードで
  tester は、(i) src に変更があれば機械的に再実行し結果文書へ再試験記録を追記、(ii) src 無変更をハッシュで
  確認した場合のみ「再試験免除（src無変更・ハッシュ値）」を結果文書に明示記録して pass できる。
  既存の結果文書を Read しただけの**無言の advance pass は禁止**（形式だけの再通過を防ぐ対策）。
- **単一active**: 現在ノードは常に1つ。並行は生じない。

## ゲート手順（ゲート/レビューの責務分離＝R1）
**ゲートは2種類**。軽量ゲート（REQ_GATE/DES_GATE＝gate-runner）は**決定論スキャナの実行と
exit code 判定のみ**を行い、意味的な分析をしない。意味整合は各フェーズの**レビュー観点に吸収**
（review-conventions.md: 設計レビュー=DES間依存・decisions来歴、試験計画レビュー=決定値矛盾）。
cross-doc の意味整合が本当に要る**重ゲート（BLD_GATE/T_GATE＝consistency-checker・Opus）**だけが
スキャナ＋意味分析の両方を行う（同じ意味チェックをレビューとゲートで二重に全量実行しない）。
いずれか high または未解決 suspect/矛盾が残れば **advance で high**（T_GATE は **high→RCA**）、
すべてクリアなら **pass**。スキャナは委任プロンプトに渡された絶対パスの `scripts/` を実行する。
- **REQ_GATE**（gate-runner）: trace_scan.py・template_residue_scan.py。
- **DES_GATE**（gate-runner）: evidence_scan.py・review_authenticity_scan.py・ontology_scan.py・trace_scan.py・template_residue_scan.py・
  `design_spec_scan.py design`（設計仕様ソースのスキーマ/決定参照/生成ビュー鮮度）。
- **BLD_GATE**（consistency-checker）: review_authenticity_scan.py・ontology_scan.py・template_residue_scan.py ＋ trace_scan.py（DES→code 実装トレース含む）＋
  `design_spec_scan.py build`（設計 IF と実装の決定論突合・設計外の公開関数検出）＋ 意味整合（設計⇔実装の事後乖離・設計外の副産物）。
- **T_GATE**（consistency-checker）: result_scan.py・ontology_scan.py・template_residue_scan.py ＋ trace_scan.py（REQ.test 充填含む）＋ 意味整合（結果⇔計画⇔上流）。REQ.test 未充填を解消してから pass。

`template_residue_scan.py` は成果物テンプレート（references/templates/）由来の未処理プレースホルダ（`<FILL:>` `<FILL-COUNT:>` `<OPT:>` `<GEN:>`）が最終成果物に残置されていないかを機械検出する（Phase 1c で新設）。テンプレをコピー→穴埋めした際の削除/置換忘れによる書式起因ループを構造的に防ぐ。
  試験結果に**未是正の FAIL** が残る場合は pass にしない。FAIL の根本原因が**試験計画の期待値誤り**（上流の
  要件/設計と食い違う期待値）なら、計画を是正→当該試験を再評価してから通す（偽陰性 FAIL を『部分カバレッジ』へ
  格下げして通過させない＝test-conventions.md）。原因が実装なら **high→RCA**。

## コマンド
- `python ${CLAUDE_PLUGIN_ROOT}/scripts/manage_flow_state.py init`（run の前処理で1回）
- `... current`（ディスパッチャが現在ノード・担当・phase を取得）
- `... wait [timeout_sec] [interval_sec]`（ディスパッチャの待機。state が変化するまでブロックして
  新しい current を返す。sleep＋current の反復ポーリングをしない。timeout_sec は Bash ツール自体の
  1回呼び出し上限内に収まるよう内部でクランプされる単一区間のポーリングで、内部で延長はしない。
  changed:false 時、出力の `recent_activity`（直近に Task/Agent 起動があったかの簡易シグナル。
  委任先が単体で長時間作業している区間は false になる既知の限界がある）を見て、true ならもう一度
  wait、false が2回連続するなら advance 漏れを疑う。詳細は skills/run/SKILL.md 手順5）
- `... advance <node> <outcome>`（各サブエージェントが完了時に呼ぶ。node は現在ノードに一致必須）
- ディスパッチャは phase 境界で `manage_active_skill.py acquire/release <phase>`（protected-path マーカー）。

## state 参照の規律（パス・ファイル名の取り違え防止）
- スクリプトはプラグインの `scripts/` 配下を**展開済みの絶対パス**で指す。**`${CLAUDE_PLUGIN_ROOT}` という
  文字列を委任プロンプトに書かない**——サブエージェントの Bash 環境ではこの変数が未定義で、advance が
  パス不明で失敗する（失敗した担当が state 手書き・誤 cwd init に逃げ履歴を破壊する事態を防ぐ）。
  ディスパッチャは自分が current 実行に使った絶対パスをそのまま委任プロンプトへ埋め込む。
- 現在ノード・履歴は **`manage_flow_state.py current`/`advance` 経由でのみ**参照/更新する。**state の生ファイルを
  直接 Read/Write しない**。ファイル名は `state/_flow_state.json`（**先頭アンダースコア**。`flow_state.json` は
  存在しない——取り違えると Read 失敗する）。state/_* への Write/Edit は hook（hook_check_state_write）が
  メイン/サブを問わず block する。
- **advance が失敗しても state を手書きで直さない**。エラー出力をそのまま完了報告に含めて終了する
  （ディスパッチャが正しいパスで再委任する）。init は seed 済みディレクトリでのみ実行できる
  （誤った cwd での state 新規生成は manage_flow_state が拒否する）。
- advance の実行主体は hook（hook_check_advance_actor）がノード担当表と突合する: **自分の担当ノードの
  advance だけを実行できる**。他ノードの advance 代行（レビュワーの代わりに planner が実行する等）と
  メインセッションからの advance は block される。許可された advance は state/_advance_audit.jsonl に
  実行主体付きで記録される（**実行前の許可記録**＝失敗した試行も残る。遷移成立の正本は
  _flow_state.json の history）。
- **advance 漏れは hook（hook_enforce_advance・SubagentStop）が機械検知する**: ノード担当の
  サブエージェントが current の advance を実行せずに停止すると、停止が差し戻され advance の実行を
  指示される（差し戻しは1停止チェーンにつき1回・同一呼び出しへは累計2回まで。上限超過と
  実行中の子タスクを持つ停止は正当として素通しされ、ディスパッチャの再委任に委ねられる——
  BLD_REVIEW で起きうる block↔許可の空振りサイクルの対策）。エスカレーション書式（「要ユーザー判断」＋該当基準の
  明示）を含む停止は正当として素通しされる。判定は state/_advance_audit.jsonl に op:"subagent_stop"
  として記録される。担当エージェント定義の「全観点確定後は必ず advance を実行する」手順の
  機械側の二重担保であり、advance を呼び忘れても停滞せず自走で復帰する。
  既知の限界: エスカレーション書式の偽装で素通り可能（自己申告テキスト依存）。偽装は
  AskUserQuestion 経由でユーザーに可視化され、監査 reason:"escalation" と AskUserQuestion 実施の
  突合で事後検証できるため受容している。**書式だけ書いて実際のエスカレーション内容が無い停止は
  規約違反**であり、レビュー・評価の検査対象。

