#!/usr/bin/env python
"""変更要望書の版番号と根本原因解析の受理記録を決定論で突合する（未処理変更の検知）。

従来この判定は run/SKILL.md の記述に基づきディスパッチャ（LLM）が目視で行っていたが、
版番号の文字列突合は完全に決定論化できる（P2機械化違反の是正）。
誤ると「未処理の変更要求を受理済みと誤認して黙って落とす」ため、パース不能は
未処理あり側に倒す（fail-closed）。

判定:
  - docs/requirements/変更要望書.md が無い → 変更要求なし（exit 0, pending: false）
  - 要望書の版が docs/test/根本原因解析.md の受理記録（「変更要求受理 … 変更要望書 版 X.Y」）
    に含まれる → 受理済み（exit 0, pending: false）
  - 含まれない／要望書の版が読めない → 未処理あり（exit 1, pending: true）→ 変更モードへ
  - プロジェクトルート外での実行 → exit 2（project_guard。誤 cwd の「要望書なし＝変更なし」
    という幽霊判定を防ぐ）

使い方: python check_pending_changes.py   （ディスパッチャが DONE 到達時に実行）
出力: JSON {pending, request_version, accepted_versions, reason}
"""
import json
import pathlib
import re
import sys

try:
    from project_guard import ensure_project_root
except ImportError:  # 単体配布などで project_guard が無い場合は cwd 検査を簡易内蔵
    def ensure_project_root():
        if not pathlib.Path("docs").is_dir():
            print("エラー: docs/ が見つかりません（プロジェクトルート外での実行）。",
                  file=sys.stderr)
            sys.exit(2)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

REQUEST_FILE = pathlib.Path("docs/requirements/変更要望書.md")
RCA_FILE = pathlib.Path("docs/test/根本原因解析.md")

# 要望書の版（例: `- 版: 1.0` / `版：2.1`）。最初の1件を要望書の現行版とみなす。
REQUEST_VERSION_RE = re.compile(r"版\s*[:：]\s*([0-9]+(?:\.[0-9]+)*)")
# 受理記録（root-cause-analyzer.md の正準形: 「変更要求受理 …（変更要望書 版 X.Y）」。
# 見出し/本文いずれでも、同一行に「変更要求受理」と「変更要望書 版 X.Y」を含む行を受理とみなす。
ACCEPTED_LINE_RE = re.compile(
    r"変更要求受理.*変更要望書\s*版\s*([0-9]+(?:\.[0-9]+)*)")


def parse_request_version(text):
    """変更要望書テキストから現行版を返す（無ければ None）。純関数。

    要望書内に複数の「版」記述がある場合（改訂履歴表に旧版が並ぶ等）は**最大の版**を
    現行版とみなす（最初の1件だと履歴表の旧版を拾い、受理済みと誤判定して新しい版の
    変更要求を黙って落とす恐れがある——fail-closed 方向の選択）。"""
    versions = REQUEST_VERSION_RE.findall(text or "")
    if not versions:
        return None
    return max(versions, key=lambda v: tuple(int(p) for p in v.split(".")))


def parse_accepted_versions(text):
    """根本原因解析テキストから受理済みの版の集合を返す。純関数。"""
    out = set()
    for line in (text or "").splitlines():
        m = ACCEPTED_LINE_RE.search(line)
        if m:
            out.add(m.group(1))
    return out


def evaluate(request_text, rca_text):
    """(pending, request_version, accepted_versions, reason) を返す純関数。

    request_text が None は「要望書なし」。版が読めない要望書は fail-closed で pending。
    """
    if request_text is None:
        return False, None, set(), "変更要望書なし"
    ver = parse_request_version(request_text)
    accepted = parse_accepted_versions(rca_text or "")
    if ver is None:
        return True, None, accepted, ("変更要望書の版が読めない（`- 版: X.Y` を記載すること）。"
                                      "黙った喪失を防ぐため未処理として扱う")
    if ver in accepted:
        return False, ver, accepted, f"版 {ver} は受理済み"
    return True, ver, accepted, f"版 {ver} が受理記録に無い（未処理の変更要求）"


def main():
    ensure_project_root()
    request_text = None
    if REQUEST_FILE.exists():
        request_text = REQUEST_FILE.read_text(encoding="utf-8", errors="replace")
    rca_text = ""
    if RCA_FILE.exists():
        rca_text = RCA_FILE.read_text(encoding="utf-8", errors="replace")
    pending, ver, accepted, reason = evaluate(request_text, rca_text)
    print(json.dumps({"pending": pending, "request_version": ver,
                      "accepted_versions": sorted(accepted), "reason": reason},
                     ensure_ascii=False))
    return 1 if pending else 0


if __name__ == "__main__":
    sys.exit(main())
