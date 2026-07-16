---
description: 依存パッケージ脆弱性の手動再スキャン。フェーズ外から osv-scanner を回して evidence/security/osv-scan.json を更新し、前回スキャン結果との差分を報告する。定期監査・新規CVE検知・BLD_GATE 通過後の再確認に使用。
argument-hint: (引数不要)
disable-model-invocation: true
allowed-tools: Read, Bash
---

# 依存パッケージ脆弱性 手動再スキャン

`scripts/security_scan.py` を明示的に実行して `evidence/security/osv-scan.json` を最新化し、
前回スキャン結果との差分（新規 / 消えた findings）をユーザーに報告する。BLD_GATE で自動実行される
security_scan.py と同じスキャナだが、こちらはフェーズと切り離してユーザーが任意タイミングで叩ける。

## 使いどころ
- **定期監査**: 完走後の変更モードに入る前など、依存に変更が無くても新規 CVE の発生を確認したい
- **BLD_GATE 通過後の再確認**: フェーズを跨いだ時間経過での新規 CVE 検知
- **依存パッケージの手動更新後**: `pip install -U <pkg>` などの後に BLD_GATE を待たず即確認

## 手順

1. **前回スキャン結果の保存**: `evidence/security/osv-scan.json` が既存なら
   `evidence/security/osv-scan.prev.json` にコピーする（Bash で `cp`）。無ければスキップ。

2. **スキャン実行**: `python ${CLAUDE_PLUGIN_ROOT}/scripts/security_scan.py` を Bash で実行し
   exit code と stdout を捕捉する。
   - exit 0: high なし（medium/low はあり得る）
   - exit 1: high あり
   - exit 2: osv-scanner 未 install または実行エラー（stderr の install 手順をユーザーに提示して終了）

3. **差分の算出と報告**: `evidence/security/osv-scan.json`（新）と
   `evidence/security/osv-scan.prev.json`（旧）を両方 Read し、以下を報告する。
   - **新規 findings**: 旧に無く新にある `(package@version, vuln_id)` の組
   - **解消された findings**: 旧にあり新に無い組（依存更新で消えた等）
   - **severity 別集計** の推移: `high: N→M, medium: N→M, low: N→M`
   - **evidence 保存先**: `evidence/security/osv-scan.json`

   前回スキャン結果が無い場合は「初回スキャン」として全 findings を新規扱いで報告する。

4. **high が存在する場合の扱い**:
   - このスキルは**フェーズ外**の手動再スキャンなので、advance/state 遷移は行わない（読み取り専用の
     報告に徹する）。
   - 発見された high は「依存を更新するか、mitigation を実装するか」の判断材料として提示する。
   - フェーズ内での fail-close 判定は BLD_GATE の consistency-checker が担う（責務分離）。

## 注意
- state（`state/_flow_state.json`）を触らない。手動監査はフローに影響しない情報提供に徹する。
- `evidence/security/osv-scan.json` は BLD_GATE も上書きするため、直近の scan 結果が定期監査時か
  BLD_GATE 実行時かはファイルの mtime で判別する（差分報告に mtime を添えると親切）。
- osv-scanner の install が必要な場合、`security_scan.py` の stderr がそのまま install コマンドを
  案内する（Windows: `winget install Google.OSVScanner` など）。
