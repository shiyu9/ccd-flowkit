# コミットメッセージ規約

committer はこの書式で staged diff からメッセージを組み立てる。

## 書式
```
<type>: <要約（日本語・72文字以内）>

<本文: 変更の背景・意図・決定理由。feat/fix/refactor では書く>

Refs: <関連ID をカンマ区切り>
```

- **type**: feat（機能追加）/ fix（バグ修正）/ docs（文書のみ）/ test（試験）/ refactor（挙動不変の整理）/ chore（その他）
- **要約**: diff の主たる変更を端的に。前回経緯に引きずられず diff の中身から判断する。
- **本文**: なぜその変更をしたか（背景・意図・決定理由）。空でもよいが feat/fix/refactor では書く。
- **Refs**: docs/trace/traceability.json を参照し、staged のファイルが対応する
  REQ-/DES-/TEST- のIDを列挙する（best-effort。該当が無ければ Refs 行ごと省略）。

## 例
```
feat: ログイン認証モジュールを実装

メール＋パスワードでの認証を追加。既存 UserRepository を利用し、
パスワードは bcrypt で照合する。

Refs: REQ-007, DES-012
```
