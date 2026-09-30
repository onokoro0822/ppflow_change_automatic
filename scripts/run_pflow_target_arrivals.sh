#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"

TARGET_ARRIVALS=""
DEMAND_JSON="$PROJECT_ROOT/config/development_scenarios/meitetsu_origin_distribution_commercial.json"
CALIBRATION_SAMPLE_FACTOR=50
EXECUTION_SAMPLE_FACTOR=50
FACILITY_SHARE=0.70
SEED=42
OUTPUT_ROOT="$PROJECT_ROOT/.local/pflow/output/calibrated_target"
CALIBRATION_DIR="$PROJECT_ROOT/.local/pflow/calibration"
DRY_RUN=0
SKIP_COMPILE=0

usage() {
  cat <<'EOF'
Usage: scripts/run_pflow_target_arrivals.sh [options]
  --target-arrivals N              Target full-population arrival trips/day
  --demand-json PATH               Demand JSON (default: Meitetsu all-commercial bridge config)
  --calibration-sample-factor N    Sample factor used by the sensitivity summary (default: 50)
  --sample-factor N                Sample factor for this one execution (default: 50; full=1)
  --facility-share R               Preferred facility share in target mesh (default: 0.70)
  --seed N                         Pseudo-PFLOW seed (default: 42)
  --output-root PATH               Output root for the single calibrated run
  --skip-compile                   Reuse compiled Pseudo-PFLOW classes
  --dry-run                        Generate calibration and print execution commands only
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target-arrivals) TARGET_ARRIVALS="$2"; shift 2 ;;
    --demand-json) DEMAND_JSON="$2"; shift 2 ;;
    --calibration-sample-factor) CALIBRATION_SAMPLE_FACTOR="$2"; shift 2 ;;
    --sample-factor) EXECUTION_SAMPLE_FACTOR="$2"; shift 2 ;;
    --facility-share) FACILITY_SHARE="$2"; shift 2 ;;
    --seed) SEED="$2"; shift 2 ;;
    --output-root) OUTPUT_ROOT="$2"; shift 2 ;;
    --skip-compile) SKIP_COMPILE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

mkdir -p "$CALIBRATION_DIR" "$OUTPUT_ROOT"
OUTPUT_ROOT="$(cd "$OUTPUT_ROOT" && pwd)"
SCENARIO_FILE="$CALIBRATION_DIR/calibrated_target.json"
REPORT_FILE="$CALIBRATION_DIR/calibration_report.json"

calibration_args=(
  "$PROJECT_ROOT/pflow_capacity_calibration.py"
  --demand-json "$DEMAND_JSON"
  --calibration-sample-factor "$CALIBRATION_SAMPLE_FACTOR"
  --facility-share "$FACILITY_SHARE"
  --output-scenario "$SCENARIO_FILE"
  --report "$REPORT_FILE"
)
if [[ -n "$TARGET_ARRIVALS" ]]; then
  calibration_args+=(--target-arrivals "$TARGET_ARRIVALS")
fi
"$PYTHON_BIN" "${calibration_args[@]}"

run_args=(
  "$PROJECT_ROOT/scripts/run_pflow_capacity_sweep.sh"
  --scenario-file "$SCENARIO_FILE"
  --output-root "$OUTPUT_ROOT"
  --sample-factor "$EXECUTION_SAMPLE_FACTOR"
  --seed "$SEED"
)
if [[ "$SKIP_COMPILE" -eq 1 ]]; then
  run_args+=(--skip-compile)
fi
if [[ "$DRY_RUN" -eq 1 ]]; then
  run_args+=(--dry-run)
fi
"${run_args[@]}"

if [[ "$DRY_RUN" -eq 0 ]]; then
  "$PYTHON_BIN" "$PROJECT_ROOT/pflow_capacity_calibration.py" \
    --evaluate-only \
    --report "$REPORT_FILE" \
    --scenario-output "$OUTPUT_ROOT/calibrated_target" \
    --execution-sample-factor "$EXECUTION_SAMPLE_FACTOR"
fi

echo "Calibration report: $REPORT_FILE"
