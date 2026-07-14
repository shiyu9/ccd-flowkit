#!/usr/bin/env python
"""Claude Code PreToolUse Hook: advance / baseline save の実行主体をノード担当と突合する（役割またぎ禁止）。

担当外エージェントによる advance の代行（例: planner がレビュワー担当の advance を代行・builder が
BLD_REVIEW の advance とレビュー記録作成を自ら執行・オーケストレーターが役割またぎ advance を委任
記述で明示指示）を防ぐ。manage_flow_state.py は実行主体を知り得ない（Bash 環境に agent_type が無い）
ため、PreToolUse(Bash) の payload にある agent_type で検証する。

ルール（fail-closed は advance コマンドを含む場合のみ。advance を含まない Bash は即許可）:
  - agent_type 無し（メインセッション＝ディスパッチャ）の advance → block（advance は担当サブエージェントの責務）
  - advance <node> の node 担当（NODE_AGENT 表）と agent_type の素名が不一致 → block
  - REQ_APPROVE は担当 None（ユーザー承認）だが、run スキルの設計どおり project-planner の実行を許可
  - 担当が引けない/表が読めない等の不確実ケースは fail-open（manage_flow_state 自体の妥当性検査に委ねる）
許可した advance は state/_advance_audit.jsonl に {at, actor, node, outcome} を追記する
（リモートセッションでも残る実行主体の一次監査記録）。記録失敗は握りつぶす（ブロックしない）。
注意: これは PreToolUse＝**実行前の「許可した試行」の記録**であり、遷移の成否は含まない
（ノード不一致等で advance がエラー終了した試行も残る）。遷移成立の正本は
_flow_state.json の history。突合時は「試行ログ ⊇ 成立遷移」として読む。

あわせて **manage_review_baseline.py save <node>** の実行主体も同じ表で検証する:
baseline（再レビュー機械diffの基準スナップショット）を是正者が先回りで上書きすると
diff が空に見え、3成分モデルの回帰確認が無効化される。save は当該レビューノードの
担当レビュワーのみ実行できる（diff/hash は読み取り系のため全員可）。
"""
import json
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

# ノード→担当の正本は manage_flow_state.py（同じ scripts/ ディレクトリ）。二重管理しない。
try:
    import manage_flow_state as _fs
except Exception:
    _fs = None

# Windows(cp932)対策。stdin も含め UTF-8 化。
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

PROJECT_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
AUDIT_FILE = PROJECT_ROOT / "state" / "_advance_audit.jsonl"

# `... manage_flow_state.py" advance REQ_PLAN done` のような呼び出しから (node, outcome) を拾う。
# スクリプトパスは引用符付き/なし・Windows/POSIX いずれもあり得るため、
# 「manage_flow_state(.py) の後に advance が続く」ことだけを要求する。
ADVANCE_RE = re.compile(
    r"manage_flow_state(?:\.py)?[\"']?\s+advance\s+[\"']?([A-Za-z_]+)[\"']?\s+[\"']?([a-z]+)",
    re.IGNORECASE)

# 担当 None ノードのうち、advance 実行者が規約で定まっているもの（run/SKILL.md）。
# DONE→change（変更モード＝デルタフロー）は変更要求を受理する root-cause-analyzer が実行する。
NONE_AGENT_EXECUTORS = {"REQ_APPROVE": "project-planner", "DONE": "root-cause-analyzer"}

# baseline の保存（上書き）コマンド。save のみ主体検証する（diff/hash は読み取り系）。
BASELINE_SAVE_RE = re.compile(
    r"manage_review_baseline(?:\.py)?[\"']?\s+save\s+[\"']?([A-Za-z_]+)",
    re.IGNORECASE)

STATE_FILE = PROJECT_ROOT / "state" / "_flow_state.json"

# trace_fill.py の実行コマンド（traceability.json の全置換書込を伴う）。python 系の起動に
# 限定して照合する（grep/cat 等の読み取り系の言及を誤って deny しない）。
TRACE_FILL_EXEC_RE = re.compile(
    r"(?:python[0-9.]*|py)\s+[^\n&|;]*trace_fill(?:\.py)?\b",
    re.IGNORECASE)
# trace_fill を実行できる担当（BLD_GATE/T_GATE の consistency-checker。台帳の所有者）。
TRACE_FILL_EXECUTOR = "consistency-checker"

# フラット化モデル: check_rerun_limit.py の実行と、state/review_results/ への
# append 書込は main（オーケストレーター）専用。integrator や観点サブが実行するのは
# フラット化の順序制御（main が観点結果を保存し、上限判定を機械化する）を破るため deny する。
# python 系の起動に限定（grep/cat 等の読み取り言及は対象外）。
RERUN_CHECK_EXEC_RE = re.compile(
    r"(?:python[0-9.]*|py)\s+[^\n&|;]*check_rerun_limit(?:\.py)?\b",
    re.IGNORECASE)
# state/review_results/<node>.jsonl への append（>>）または上書き（>）。書込主体を main に限定する。
# パスは絶対/相対いずれもあり得るので "state/review_results/" 部分でマッチ。
# コードレビュー指摘に対応:
#   - パスにスペースが含まれる Windows パス（例: `>> "C:/Program Files/proj/state/review_results/X.jsonl"`）
#     を捕捉できるよう、開き引用符から state までの区間に空白を許可（`[^\"'\n>|]*`）。
#   - シングル/ダブルクォート囲みのパスでも同じルートで捕捉。
#   - パス末尾の閉じ引用符・リダイレクト記号・パイプは除外し、余計な取り込みを防ぐ。
REVIEW_RESULTS_WRITE_RE = re.compile(
    r"(?:>>|>)\s*[\"']?(?:[^\"'\n>|]*[/\\])?"
    r"state[/\\]review_results[/\\][^\s\"'>|]+\.jsonl",
    re.IGNORECASE)


def load_history():
    """_flow_state.json の history を読む。読めなければ None（fail-open の判断材料）。"""
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        hist = data.get("history") if isinstance(data, dict) else None
        return hist if isinstance(hist, list) else None
    except Exception:
        return None


def _waiver_token_present(command, node):
    """`manage_flow_state.py advance <node> pass waiver` の免除トークンが command にあるか。

    ADVANCE_RE と同じく manage_flow_state プレフィックスを要求する（echo 等の
    偶発文字列中の `advance ... pass waiver` を免除と誤認しない）。"""
    return bool(re.search(
        rf"manage_flow_state(?:\.py)?[\"']?\s+advance\s+[\"']?{re.escape(node)}[\"']?"
        rf"\s+[\"']?pass[\"']?\s+[\"']?waiver\b",
        command, re.IGNORECASE))


def evaluate_review_round(command, history):
    """収束規律の機械強制: ラウンド上限超過のまま `advance <*_REVIEW> pass` なら block メッセージを返す。

    review_round は manage_flow_state.py が機械算出でき、上限も同所の
    REVIEW_ROUND_ESCALATION_THRESHOLD が正本——判定材料が全て機械側にあるのに
    従来はエージェント定義の「3を超えたら要ユーザー判断を返す」というLLMへのお願い
    だけだった（P2 機械化違反の是正）。
    - high（差し戻し）はラウンドに関係なく許可する（正当な回帰検知を阻害しない）。
    - `advance <node> pass waiver`（ユーザー承諾済み免除。ディスパッチャが AskUserQuestion の
      承諾後に再委任プロンプトで指示する正規経路）は許可する。
      【既知の限界】waiver トークンは実行者自身が書けるため、
      ユーザー承諾なしの自己発行で迂回できる。受容の根拠: (1)waiver は history と
      _advance_audit.jsonl の両方に機械記録され、AskUserQuestion 実施との突合で事後検証できる
      (2)免除注記の decisions 裏付けを review_authenticity_scan が high 検出する
      （承諾主張パターンに免除形を追加済み）＝自己発行は複数の監査痕跡に矛盾として現れる
      (3)指示提供元（ディスパッチャ経由か自己判断か）はコマンド文字列から機械判別できない。
    - history が読めない・表が読めない等は fail-open（None を返す）。
    """
    if _fs is None or history is None:
        return None
    try:
        threshold = _fs.REVIEW_ROUND_ESCALATION_THRESHOLD
    except Exception:
        return None
    for node, outcome in parse_advances(command):
        if outcome != "pass" or not node.endswith("_REVIEW"):
            continue
        if _waiver_token_present(command, node):
            continue
        try:
            rr = _fs.review_round(history, node)
        except Exception:
            return None
        if rr is not None and rr > threshold:
            return (
                f"[hook] 収束規律ガード: {node} は review_round={rr} で上限"
                f"（{threshold}）を超えています。pass での advance はできません。\n"
                f"  レビューが収束していません。advance せず「要ユーザー判断」"
                f"（該当基準5: 同じ失敗の繰り返し）で返してください。\n"
                f"  ユーザーが免除を承諾した場合のみ、ディスパッチャの再委任指示に従い\n"
                f"  レビュー記録に免除注記を書いたうえで advance してください"
                f"（review-conventions.md「収束規律」）。\n"
            )
    return None


def normalize_agent(agent_type):
    """agent_type を素のエージェント名に正規化する（'ccd-flowkit:designer' → 'designer'）。"""
    if not isinstance(agent_type, str) or not agent_type.strip():
        return None
    return agent_type.strip().split(":")[-1].strip() or None


def parse_advances(command):
    """Bash コマンド文字列から advance の (node, outcome) の組を全て抽出する（純関数）。

    早期リターンの部分一致も小文字化して行う（Windows のファイルシステムは大小無視のため、
    `Manage_Flow_State.py ADVANCE ...` でも本物の advance が実行できてしまう——ガードも同じ
    寛容さで見ないとすり抜ける）。
    """
    if not isinstance(command, str) or "advance" not in command.lower():
        return []
    return [(n.upper(), o.lower()) for n, o in ADVANCE_RE.findall(command)]


def expected_executor(node):
    """ノードの advance を実行すべきエージェントの素名を返す（不明なら None＝検証しない）。"""
    if node in NONE_AGENT_EXECUTORS:
        return NONE_AGENT_EXECUTORS[node]
    if _fs is None:
        return None
    try:
        return _fs.NODE_AGENT.get(node)
    except Exception:
        return None


def parse_baseline_saves(command):
    """Bash コマンド文字列から baseline save の対象ノードを全て抽出する（純関数）。"""
    if not isinstance(command, str) or "save" not in command.lower():
        return []
    return [n.upper() for n in BASELINE_SAVE_RE.findall(command)]


def evaluate_trace_fill(command, payload):
    """trace_fill.py の実行主体を検証し、違反なら block メッセージを返す（純関数・テスト可能）。

    trace_fill は traceability.json を全置換で書き戻すため、実行は台帳の所有者
    （consistency-checker）に限定する（非担当エージェントの
    Bash 実行が台帳改竄の直接経路になる指摘への対策。baseline save ガードと同型）。
    """
    if not isinstance(command, str) or not TRACE_FILL_EXEC_RE.search(command):
        return None
    actor = normalize_agent(payload.get("agent_type") if isinstance(payload, dict) else None)
    if actor != TRACE_FILL_EXECUTOR:
        who = actor or "メインセッション"
        return (
            f"[hook] trace_fill ガード: trace_fill.py（traceability.json の機械転記）は\n"
            f"  {TRACE_FILL_EXECUTOR}（BLD_GATE/T_GATE の担当）のみ実行できます（実行者: {who}）。\n"
            f"  台帳の全置換書込を伴うため、他エージェントによる実行は台帳改竄の経路になります。\n"
        )
    return None


def evaluate_rerun_actor(command, payload):
    """check_rerun_limit.py の実行と review_results への append を main 専用に限定する。

    フラット化モデルで main が観点結果を state に保存し、上限判定を機械化する
    順序制御を破らないため。integrator や観点サブがこれらを実行するのは規約違反。
    パターンは trace_fill ガードと同型（実行者反転: main=許可、サブ=deny）。
    """
    if not isinstance(command, str):
        return None
    is_rerun_check = bool(RERUN_CHECK_EXEC_RE.search(command))
    is_results_write = bool(REVIEW_RESULTS_WRITE_RE.search(command))
    if not (is_rerun_check or is_results_write):
        return None
    actor = normalize_agent(payload.get("agent_type") if isinstance(payload, dict) else None)
    if actor is None:
        return None  # main（agent_type 無し）は許可
    target = ("check_rerun_limit.py" if is_rerun_check
              else "state/review_results/ への書込")
    return (
        f"[hook] rerun/review_results ガード: {target} は main（ディスパッチャ）専用です"
        f"（実行者: {actor}）。\n"
        f"  フラット化モデルでは main が観点結果の保存と rerun 上限判定を担います。\n"
        f"  integrator や観点サブがこれらを実行すると順序制御が破れます。\n"
    )


def evaluate_baseline(command, payload):
    """baseline save の実行主体を検証し、違反なら block メッセージを返す（純関数・テスト可能）。"""
    saves = parse_baseline_saves(command)
    if not saves:
        return None
    actor = normalize_agent(payload.get("agent_type") if isinstance(payload, dict) else None)
    for node in saves:
        expected = expected_executor(node)
        if actor is None or (expected and actor != expected):
            who = actor or "メインセッション"
            return (
                f"[hook] baseline ガード: {node} の baseline save は担当レビュワー"
                f"（{expected or '当該ノードの担当'}）のみ実行できます（実行者: {who}）。\n"
                f"  是正者による baseline の先回り上書きは、再レビューの機械diffを空に見せて\n"
                f"  回帰確認を無効化するため禁止です（diff/hash の参照は誰でも可）。\n"
            )
    return None


def evaluate(command, payload):
    """advance コマンドの実行主体を検証し、違反なら block メッセージを返す（純関数・テスト可能）。"""
    advances = parse_advances(command)
    if not advances:
        return None
    actor = normalize_agent(payload.get("agent_type") if isinstance(payload, dict) else None)
    for node, _outcome in advances:
        expected = expected_executor(node)
        if actor is None:
            return (
                f"[hook] advance ガード: ノード {node} の advance をメインセッション"
                f"（ディスパッチャ）は実行できません。\n"
                f"  advance は担当サブエージェント（{expected or '担当エージェント'}）に委任して"
                f"実行させてください（run/SKILL.md）。\n"
            )
        if expected and actor != expected:
            return (
                f"[hook] advance ガード: ノード {node} の advance は {expected} の責務です"
                f"（実行者: {actor}）。\n"
                f"  役割またぎの advance 代行は禁止です。自分の担当ノードの advance だけを実行し、\n"
                f"  他ノードの前進は完了報告に留めてください（ディスパッチャが担当を起動します）。\n"
            )
    return None


def audit(command, payload, verdict="allow"):
    """advance / baseline save の判定を state/_advance_audit.jsonl へ追記する（best-effort）。

    **deny も記録する**（発火記録が無いと『発火機会なし』と『検知漏れ』を
    ログから区別できない）。allow は実行前の許可記録＝失敗試行も含む（遷移成立の正本は history）。
    """
    try:
        actor = normalize_agent(payload.get("agent_type") if isinstance(payload, dict) else None)
        recs = []
        for n, o in parse_advances(command):
            r = {"op": "advance", "node": n, "outcome": o}
            # waiver（ユーザー承諾済み免除）は監査からも判別可能にする
            # （history の waiver: true と併せ、免除注記・AskUserQuestion 実施との事後突合の根拠）
            if o == "pass" and _waiver_token_present(command, n):
                r["waiver"] = True
            recs.append(r)
        recs += [{"op": "baseline_save", "node": n} for n in parse_baseline_saves(command)]
        if isinstance(command, str) and TRACE_FILL_EXEC_RE.search(command):
            recs += [{"op": "trace_fill"}]
        if not recs:
            return
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            for r in recs:
                r.update({"at": datetime.now().isoformat(timespec="seconds"),
                          "actor": actor or "main", "verdict": verdict})
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _read_hook_input():
    """hook 入力を読み取り (tool_name, command, payload) を返す（stdin JSON 優先・env フォールバック）。

    env フォールバックでは agent_type を載せない（委任ガード/commit ゲートと一貫。
    フォールバック経路は常に『欠落＝オーケストレーター』として扱う）。
    """
    try:
        if not sys.stdin.isatty():
            data = sys.stdin.read()
            if data:
                payload = json.loads(data)
                if isinstance(payload, dict):
                    tn = payload.get("tool_name", "")
                    ti = payload.get("tool_input", {})
                    cmd = ti.get("command", "") if isinstance(ti, dict) else ""
                    if tn:
                        return tn, cmd, payload
    except (json.JSONDecodeError, OSError, ValueError):
        pass
    payload = {}
    tn = os.environ.get("TOOL_NAME", "")
    cmd = ""
    s = os.environ.get("TOOL_INPUT", "")
    if s:
        try:
            parsed = json.loads(s)
            if isinstance(parsed, dict):
                cmd = parsed.get("command", "")
        except (json.JSONDecodeError, TypeError):
            pass
    return tn, cmd, payload


def main():
    try:
        tool_name, command, payload = _read_hook_input()
        if tool_name != "Bash" or not command:
            return 0
        low = command.lower()
        if ("manage_flow_state" not in low and "manage_review_baseline" not in low
                and "trace_fill" not in low and "check_rerun_limit" not in low
                and "review_results" not in low):
            return 0
        msg = (evaluate(command, payload) or evaluate_baseline(command, payload)
               or evaluate_trace_fill(command, payload)
               or evaluate_rerun_actor(command, payload))
        if msg is None and any(o == "pass" and n.endswith("_REVIEW")
                               for n, o in parse_advances(command)):
            msg = evaluate_review_round(command, load_history())
        if msg:
            audit(command, payload, verdict="deny")
            sys.stderr.write(msg)
            return 2
        audit(command, payload, verdict="allow")
        return 0
    except Exception:
        # フェイルセーフ: 予期せぬ例外でブロックしない
        try:
            traceback.print_exc(file=sys.stderr)
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
