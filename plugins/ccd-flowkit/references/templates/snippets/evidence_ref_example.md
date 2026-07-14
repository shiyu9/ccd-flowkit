<!-- snippet: evidence_ref_example v1 -->
# エビデンス参照 `[E_n]` の書式例示

## 対策する問題

`evidence_scan.py` は設計書 HTML の判断・命題に `[E_n]` エビデンスラベルの付与を要求する。
`[E_n]` は `docs/design/vrr/<name>.md` などのエビデンス記録への参照であり、参照先が実在
するか（未検証 `[E?]` が残っていないか）を検査する。

**発生しやすいパターン**:
- 実装ソースの重要な分岐（例: `except json.JSONDecodeError` などの例外ハンドリング）に、対応する
  設計書エビデンス `[E_n]` への根拠コメントが欠落 → build-convention-reviewer が high 検出

## 適用範囲（規約の明確化）

evidence-conventions.md「エビデンス参照」節の規約:
- **必須対象**: 設計書 HTML の判断・命題（DES 節の推論・仕様選定）
- **推奨対象**: 実装ソースの重要な分岐（設計書エビデンスへ辿れるコメント）
- **対象外**: 試験計画・レビュー記録の散文
- **禁止**: `[E?]`（未検証プレースホルダの残置）

## 正しい書き方

### ✅ 設計書 HTML（必須）

```html
<h3>DES-002 load_tasks の JSON パース失敗時の挙動</h3>
<p>JSONDecodeError を捕捉して TasksFileError に変換する
   [E3] (vrr: <a href="vrr/json_decode_error_type.md">json_decode_error_type.md</a>)。</p>
```

エビデンスの実体は `docs/design/vrr/` 配下の Markdown ファイル。ラベル `[E3]` と `vrr/<name>.md`
への参照をペアで書く。

### ✅ 実装ソース（推奨・例外ハンドリング等の重要分岐で欠落しやすい領域）

```python
def load_tasks(path):
    # DES-002 [REQ-011 REQ-012]
    # JSONDecodeError → TasksFileError 変換の根拠は 設計書 DES-002 の [E3]
    # (docs/design/vrr/json_decode_error_type.md)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as exc:
        raise TasksFileError(f"tasks.json is corrupted: {exc}") from exc
```

コメントに `[E3]` を書くことで、実装のこの分岐が設計書のどのエビデンスに支えられているかを
辿れる。

### ❌ 間違った例

**未検証プレースホルダの残置**:
```html
<p>選択肢 A を採用 [E?]。</p>
```

**エビデンス参照の記法違反**（`[E-3]` や `[E: 3]` 等）:
```html
<p>選択肢 A を採用 [E-3]。</p>
```

**参照先が存在しない**:
```html
<p>選択肢 A を採用 [E99]（存在しない vrr/foo.md）。</p>
```

## 番号採番

`[E_n]` の番号 n は文書内で通し番号にする。設計書全体で `[E1]`, `[E2]`, ... と連番。
再採番を防ぐため、既存の設計書に追加する場合は末尾から続ける（`[E1]` から `[E7]` まで
使われていれば新規は `[E8]` から）。

## 参照先の実在確認

`evidence_scan.py` は以下を検査:
1. `[E_n]` の n が対応する VRR (`docs/design/vrr/`) やコードベース参照 (`{file}:{line}`) が実在するか
2. 未検証 `[E?]` の残置がないか
3. 参照先の内容が実際に主張を支えているか（LLM 判断も含む）

## 関連

- `../../references/evidence-conventions.md`: エビデンス参照の規約
- `../../scripts/evidence_scan.py`: `[E_n]` を検査するスキャナ本体
