#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PSEUDO_REPO="$(cd "$PROJECT_ROOT/../Pseudo-PFLOW-v3" 2>/dev/null && pwd || true)"
PFLOW_HOME_VALUE="${PFLOW_HOME:-$PROJECT_ROOT/.local/pflow}"
SCENARIO_DIR="$PROJECT_ROOT/config/pflow_capacity"
OUTPUT_ROOT=""
SELECTED="all"
SCENARIO_FILE=""
DRY_RUN=0
RESUME=0
SKIP_COMPILE=0
SEED=42
SAMPLE_FACTOR=50

usage() {
  cat <<'EOF'
Usage: scripts/run_pflow_capacity_sweep.sh [options]
  --all                    Run all 10 scenarios (default)
  --scenario ID            Run one scenario ID
  --scenario-file PATH     Run one scenario JSON outside config/pflow_capacity
  --pflow-home PATH         Input root (default: $PFLOW_HOME or .local/pflow)
  --pseudo-repo PATH        Pseudo-PFLOW-v3 checkout
  --output-root PATH        Scenario output root
  --sample-factor N         50 means a 2% sample (default: 50)
  --seed N                  Deterministic seed (default: 42)
  --resume                  Skip scenarios with a .complete marker
  --skip-compile            Reuse an already compiled Pseudo-PFLOW-v3 checkout
  --dry-run                 Print commands only
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all) SELECTED="all"; shift ;;
    --scenario) SELECTED="$2"; shift 2 ;;
    --scenario-file) SCENARIO_FILE="$2"; shift 2 ;;
    --pflow-home) PFLOW_HOME_VALUE="$2"; shift 2 ;;
    --pseudo-repo) PSEUDO_REPO="$2"; shift 2 ;;
    --output-root) OUTPUT_ROOT="$2"; shift 2 ;;
    --sample-factor) SAMPLE_FACTOR="$2"; shift 2 ;;
    --seed) SEED="$2"; shift 2 ;;
    --resume) RESUME=1; shift ;;
    --skip-compile) SKIP_COMPILE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$OUTPUT_ROOT" ]]; then
  OUTPUT_ROOT="$PFLOW_HOME_VALUE/output/capacity_experiment"
fi
mkdir -p "$OUTPUT_ROOT"
OUTPUT_ROOT="$(cd "$OUTPUT_ROOT" && pwd)"

[[ -d "$PSEUDO_REPO" ]] || { echo "Pseudo-PFLOW-v3 not found: $PSEUDO_REPO" >&2; exit 1; }
[[ -d "$PFLOW_HOME_VALUE/data/facilities" ]] || { echo "PFLOW facilities not found: $PFLOW_HOME_VALUE" >&2; exit 1; }
[[ -d "$PFLOW_HOME_VALUE/data/census/person/agent/23" ]] || { echo "Aichi agents not found: $PFLOW_HOME_VALUE" >&2; exit 1; }

if [[ -n "$SCENARIO_FILE" ]]; then
  [[ -f "$SCENARIO_FILE" ]] || { echo "Scenario not found: $SCENARIO_FILE" >&2; exit 1; }
  SCENARIO_FILE="$(cd "$(dirname "$SCENARIO_FILE")" && pwd)/$(basename "$SCENARIO_FILE")"
  scenarios=("$SCENARIO_FILE")
elif [[ "$SELECTED" == "all" ]]; then
  scenarios=("$SCENARIO_DIR"/*.json)
else
  scenarios=("$SCENARIO_DIR/$SELECTED.json")
fi
[[ -f "${scenarios[0]}" ]] || { echo "Scenario not found: ${scenarios[0]}" >&2; exit 1; }

if [[ "$DRY_RUN" -eq 0 && "$SKIP_COMPILE" -eq 0 ]]; then
  (cd "$PSEUDO_REPO" && mvn -q -DskipTests compile)
fi

mains=(pseudo.gen.Commuter pseudo.gen.NonCommuter pseudo.gen.Student)
for scenario in "${scenarios[@]}"; do
  scenario_id="$(basename "$scenario" .json)"
  scenario_output="$OUTPUT_ROOT/$scenario_id"
  if [[ "$RESUME" -eq 1 && -f "$scenario_output/.complete" ]]; then
    echo "[$scenario_id] already complete; skipping"
    continue
  fi
  mkdir -p "$scenario_output/logs"
  for main_class in "${mains[@]}"; do
    role="${main_class##*.}"
    exec_args="--prefectures 23 --sample-factor $SAMPLE_FACTOR --seed $SEED --scenario $scenario --output-dir $scenario_output"
    if [[ "$DRY_RUN" -eq 1 ]]; then
      printf 'PFLOW_HOME=%q mvn -q -DskipTests exec:java -Dexec.mainClass=%q -Dexec.args=%q\n' \
        "$PFLOW_HOME_VALUE" "$main_class" "$exec_args"
    else
      echo "[$scenario_id] $role"
      log_file="$scenario_output/logs/$role.log"
      if ! (cd "$PSEUDO_REPO" && PFLOW_HOME="$PFLOW_HOME_VALUE" mvn -q -DskipTests exec:java \
        -Dexec.mainClass="$main_class" -Dexec.args="$exec_args") >"$log_file" 2>&1; then
        tail -50 "$log_file" >&2
        exit 1
      fi
      tail -5 "$log_file"
    fi
  done
  if [[ "$DRY_RUN" -eq 0 ]]; then
    touch "$scenario_output/.complete"
  fi
done
