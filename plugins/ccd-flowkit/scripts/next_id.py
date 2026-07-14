#!/usr/bin/env python3
"""next_id.py PREFIX
発行台帳(high-water mark)方式で次のIDを返す。台帳は docs/trace/traceability.json の
予約キー "_allocator" に統合（ID形式と衝突しない）。
末尾項目を削除しても台帳は減らないため、番号の再利用が起きない。
採番のたびに台帳を+1して書き戻す（書込不可＝プランモード等では best-effort で非永続化）。
使い方: python3 scripts/next_id.py DES  ->  DES-014
"""
import sys, re, glob, json, pathlib

# Windows コンソール(cp932)で日本語を print してもクラッシュしないよう stdout/stderr を UTF-8 化。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 誤 cwd の無検査書込を防ぐ（書込系への横展開。project_guard.py 参照）
try:
    from project_guard import ensure_project_root
except ImportError:  # 配置差異でも fail-open にしない最小フォールバック
    def ensure_project_root(root=None):
        check = pathlib.Path(root) if root is not None else pathlib.Path('.')
        if not (check / 'docs').is_dir():
            print(f'エラー: {check} に docs/ が無い（プロジェクトルート外での実行）', file=sys.stderr)
            sys.exit(2)

ROOT = pathlib.Path(".")
LEDGER = ROOT / "docs/trace/traceability.json"
PREFIX_SOURCES = {
    "REQ": ["docs/requirements/*.md"],
    "DES": ["docs/design/*.html"],
    "TEST": ["docs/**/*試験計画*.md", "docs/**/*試験結果*.md"],
    "MOD": ["src/**/*"],
}

def existing_max(prefix):
    pat = re.compile(rf"\b{prefix}-(\d+)\b")
    nums = [0]
    for g in PREFIX_SOURCES.get(prefix, []):
        for p in glob.glob(g, recursive=True):
            try:
                nums += [int(m) for m in pat.findall(pathlib.Path(p).read_text(encoding="utf-8", errors="ignore"))]
            except Exception:
                pass
    return max(nums)

def load():
    if LEDGER.exists():
        try: return json.loads(LEDGER.read_text(encoding="utf-8"))
        except Exception: return {}
    return {}

def main():
    ensure_project_root()
    if len(sys.argv) < 2:
        print("usage: next_id.py PREFIX", file=sys.stderr); sys.exit(2)
    prefix = sys.argv[1].upper()
    data = load()
    alloc = data.get("_allocator", {})
    hwm = int(alloc.get(prefix, 0))            # 過去に発行した最大（減らない）
    nxt = max(hwm, existing_max(prefix)) + 1   # 手動追加で台帳より進んでいても矛盾しない
    # 台帳を更新して書き戻す（best-effort）
    alloc[prefix] = nxt
    data["_allocator"] = alloc
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass  # 読み取り専用(プランモード等)。承認後の書き出し時に永続化される。
    print(f"{prefix}-{nxt:03d}")

if __name__ == "__main__":
    main()
