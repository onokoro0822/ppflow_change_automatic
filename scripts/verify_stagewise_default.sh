#!/usr/bin/env bash
# Run once in the background. This verifies defaults, not calibration targets.
set -euo pipefail
cd "$(dirname "$0")/.."
out="${1:?output root required}"
mkdir -p "$out"
finish() {
  code=$?
  printf '%s\n' "$code" > "$out/exit_code.txt"
  if [[ $code -eq 0 ]]; then message='段階校正の手順1：既定207 CSVの完全一致を確認しました。'; else message='段階校正の手順1：検証が停止しました。NEXT.mdのログを確認してください。'; fi
  printf '%s\n' "$message" > "$out/終了結果.txt"
  /usr/bin/osascript -e 'on run argv' -e 'display notification (item 1 of argv) with title "PFLOW 検証終了" sound name "Glass"' -e 'end run' "$message" || true
  /usr/bin/osascript -e 'on run argv' -e 'display dialog (item 1 of argv) with title "PFLOW 検証終了" buttons {"OK"} default button "OK"' -e 'end run' "$message" > "$out/notification.log" 2>&1 &
}
trap finish EXIT
bash scripts/run_pflow_capacity_sweep.sh --scenario baseline --seed 42 --skip-compile --output-root "$out"
if rg -n 'Unresolved compilation problems|Exception|OutOfMemoryError' "$out/baseline/logs" > "$out/log_errors.txt"; then exit 1; fi
count=$(find "$out/baseline/23" -name '*.csv' -type f | wc -l | tr -d ' ')
[[ "$count" == 207 ]]
diff -rq .local/pflow/output/capacity_experiment/baseline/23 "$out/baseline/23" > "$out/default_diff.txt"
printf 'CSV files: %s; byte comparison: identical\n' "$count" > "$out/verification.txt"
