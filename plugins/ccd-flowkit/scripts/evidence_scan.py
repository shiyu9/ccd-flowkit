#!/usr/bin/env python
"""evidence_scan.py
設計書のエビデンス（根拠）参照を決定論的に点検する。フェーズゲートで実行。

- [E?...] 未検証マーカーが残っていれば high（draft が確定として提出された signal）。
- 本文が参照する [E_n] に対応する定義が「エビデンス一覧」に無ければ high（未定義参照）。
- 定義済みだが本文で未参照の [E_n] は medium（未使用エビデンス）。

high が1件でもあれば exit 1。LLM 判断は使わない（参照先が主張を支えるかは design-reviewer が見る）。
使い方: python scripts/evidence_scan.py
"""
import glob
import pathlib
import re
import sys

# Windows コンソール(cp932)で日本語を print してもクラッシュしないよう stdout/stderr を UTF-8 化。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

findings = []
def add(sev, kind, msg): findings.append((sev, kind, msg))

# 定義行: [E1] codebase: src/auth.py:10   （type は codebase/spec/vrr/ud）
# [E_n] の各出現を1回でマッチする。直後に「型キーワード + コロン」が続けば定義、
# 続かなければ参照。行番号・文字位置は一切使わない（編集でずれて追えなくなるのを避けるため、
# 判定は隣接トークンのみで行う）。
ENTRY_RE = re.compile(r"\[E(\d+)\]\s*((?:codebase|spec|vrr|ud)\s*:)?", re.IGNORECASE)
UNVERIFIED_RE = re.compile(r"\[E\?")


def _strip_code_blocks(text):
    """<pre>/<code> ブロックと HTML コメントを除去する。

    コード例や規約の書式引用（例: <code>[E?:type:topic]</code>）を参照・未検証マーカーとして
    誤検出しないため。エビデンス一覧の定義もこの除去後の本文に置く規約（code ブロック外）。
    """
    return re.sub(
        r"<pre\b[^>]*>.*?</pre>|<code\b[^>]*>.*?</code>|<!--.*?-->",
        " ", text, flags=re.DOTALL | re.IGNORECASE,
    )


def scan_text(text):
    """1つの設計書テキストを点検し findings のリストを返す（純関数・テスト可能）。

    各 [E_n] 出現を、直後に「型: 」が続くか否か（隣接トークンのみ）で定義／参照に分類する。
    位置・行番号は使わないので、文章を編集してマーカーがずれても破綻しない。
    """
    out = []
    # コード例・書式引用の誤検出を避けるため、コードブロック等を除いた本文で判定する。
    body = _strip_code_blocks(text)
    defs = set()
    refs = set()
    for m in ENTRY_RE.finditer(body):
        n = int(m.group(1))
        if m.group(2):   # 「型: 」が続く → 定義（エビデンス一覧の行）
            defs.add(n)
        else:            # 続かない → 本文の参照
            refs.add(n)

    if UNVERIFIED_RE.search(body):
        out.append(("high", "evidence_unverified",
                    "未検証マーカー [E?] が残っている（draft が確定として提出された）"))
    for n in sorted(refs - defs):
        out.append(("high", "evidence_undefined",
                    f"本文が参照する [E{n}] がエビデンス一覧に定義されていない"))
    for n in sorted(defs - refs):
        out.append(("medium", "evidence_unused",
                    f"定義された [E{n}] が本文で参照されていない（未使用エビデンス）"))
    return out


# 誤 cwd の無検査 green を防ぐ（project_guard.py 参照）
try:
    from project_guard import ensure_project_root
except ImportError:  # 配置差異でも fail-open にしない最小フォールバック
    def ensure_project_root():
        import pathlib as _p, sys as _s
        if not _p.Path('docs').is_dir():
            print('エラー: docs/ が無い（プロジェクトルート外での実行）', file=_s.stderr)
            _s.exit(2)


def main():
    ensure_project_root()
    for p in glob.glob("docs/design/*.html"):
        text = pathlib.Path(p).read_text(encoding="utf-8", errors="ignore")
        for sev, kind, msg in scan_text(text):
            add(sev, kind, f"{p}: {msg}")
    order = {"high": 0, "medium": 1, "low": 2}
    for sev, kind, msg in sorted(findings, key=lambda x: order.get(x[0], 9)):
        print(f"[{sev}] {kind}: {msg}")
    highs = [f for f in findings if f[0] == "high"]
    print(f"\nsummary: high={sum(1 for f in findings if f[0]=='high')} "
          f"medium={sum(1 for f in findings if f[0]=='medium')} "
          f"low={sum(1 for f in findings if f[0]=='low')}")
    sys.exit(1 if highs else 0)


if __name__ == "__main__":
    main()
