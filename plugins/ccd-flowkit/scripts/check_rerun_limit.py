#!/usr/bin/env python
"""レビュー内 rerun 上限の判定（フラット化・P2機械化）。

フラット化モデルで integrator が同一 review_round 内で観点サブの rerun を要求できる。
本スクリプトは main が rerun 実行前に呼び、_advance_audit.jsonl から現ラウンドの rerun 回数を
集計して manage_flow_state.py の RERUN_LIMIT_PER_ROUND と比較する。上限超過時はディスパッチャが
AskUserQuestion でユーザーに諮る（強制収束はしない＝integrator の判断を上書きしない）。

判定:
  - 上限内 → exit 0（rerun 実行してよい）
  - 上限超過 → exit 1（AskUserQuestion 促し）
  - プロジェクトルート外 → exit 2（project_guard・幽霊判定防止）
  - 引数不足・不正 → exit 2

使い方: python check_rerun_limit.py <NODE>   （例: BLD_REVIEW）
出力（stdout JSON）: {round, rerun_count, limit, under_limit, reason}
"""
import json
import pathlib
import sys

try:
    from project_guard import ensure_project_root
except ImportError:
    def ensure_project_root():
        if not pathlib.Path("docs").is_dir():
            print("エラー: docs/ が見つかりません（プロジェクトルート外での実行）。",
                  file=sys.stderr)
            sys.exit(2)

try:
    import manage_flow_state as _fs
except ImportError:
    _fs = None

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

AUDIT_FILE = pathlib.Path("state/_advance_audit.jsonl")
STATE_FILE = pathlib.Path("state/_flow_state.json")


def current_review_round(history, node):
    """指定ノードの現在の review_round を返す（純関数・manage_flow_state.review_round 再利用）。

    _fs が import 不能なら None（fail-open で 0 扱い）。
    """
    if _fs is None:
        return None
    try:
        return _fs.review_round(history, node)
    except Exception:
        return None


def count_reruns_in_round(audit_records, node, round_no):
    """_advance_audit.jsonl の op:"rerun" レコードから指定ノード＋ラウンドの rerun 回数を返す（純関数）。

    op:"rerun" は integrator の rerun 要求ごとに main が append する記録。
    round フィールドで区別する。node フィールドで対象ノードを絞る。
    audit_records: {op, node, round, targets, at, ...} 形式のリスト。
    """
    if not isinstance(audit_records, list) or round_no is None:
        return 0
    count = 0
    for rec in audit_records:
        if not isinstance(rec, dict):
            continue
        if (rec.get("op") == "rerun"
                and rec.get("node") == node
                and rec.get("round") == round_no):
            count += 1
    return count


def evaluate(audit_records, history, node):
    """rerun 上限判定の純関数。戻り値: (under_limit, round, rerun_count, limit, reason)。

    - node が *_REVIEW でない → (True, None, 0, N, "非レビューノード")
    - manage_flow_state が読めない → **fail-safe deny**（under=False）にして main を exit 1
      に落とし、ディスパッチャに AskUserQuestion を促す（上限を推測して素通しにしない）。
    - 通常判定: rerun_count が limit 未満なら under_limit=True
    """
    if _fs is None:
        # コードレビュー指摘: 旧実装は limit=2 をハードコードで fail-open していた
        # が、正本（manage_flow_state.RERUN_LIMIT_PER_ROUND）と同期しないうえ「上限判定不能」を
        # 素通しで隠す挙動になっていた。fail-safe deny に転換し main が exit 1 を返すことで
        # ディスパッチャの AskUserQuestion 経路に載せる（強制収束はしない設計と一貫）。
        return False, None, 0, 0, "manage_flow_state import 失敗（fail-safe: deny）"
    limit = getattr(_fs, "RERUN_LIMIT_PER_ROUND", 2)
    if not isinstance(node, str) or not node.endswith("_REVIEW"):
        return True, None, 0, limit, f"{node} は *_REVIEW ノードでない"
    round_no = current_review_round(history, node)
    if round_no is None:
        return True, None, 0, limit, "review_round が算出不能（fail-open）"
    count = count_reruns_in_round(audit_records, node, round_no)
    under = count < limit
    reason = (f"round {round_no} で rerun {count}/{limit} "
              f"({'許可' if under else '上限超過'})")
    return under, round_no, count, limit, reason


def read_audit_records(path=None):
    """_advance_audit.jsonl を1行1レコードで読み、辞書のリストを返す。無ければ空リスト。"""
    p = pathlib.Path(path) if path else AUDIT_FILE
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def read_history(path=None):
    """_flow_state.json の history を読む。読めなければ None。"""
    p = pathlib.Path(path) if path else STATE_FILE
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    hist = data.get("history")
    return hist if isinstance(hist, list) else None


def main():
    ensure_project_root()
    if len(sys.argv) < 2:
        print("使い方: check_rerun_limit.py <NODE>", file=sys.stderr)
        return 2
    node = sys.argv[1]
    records = read_audit_records()
    history = read_history()
    under, round_no, count, limit, reason = evaluate(records, history, node)
    print(json.dumps({
        "node": node,
        "round": round_no,
        "rerun_count": count,
        "limit": limit,
        "under_limit": under,
        "reason": reason,
    }, ensure_ascii=False))
    return 0 if under else 1


if __name__ == "__main__":
    sys.exit(main())
