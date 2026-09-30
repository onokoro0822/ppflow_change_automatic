#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DESTINATION="$PROJECT_ROOT/.local/pflow"
SCOPE="aichi-minimal"
DRY_RUN=0
MIN_FREE_BYTES=$((20 * 1024 * 1024 * 1024))
LOCAL_CAP_BYTES=$((10 * 1024 * 1024 * 1024))
MINIMAL_EXPECTED_BYTES=3230000000

usage() {
  cat <<'EOF'
Usage: scripts/download_pflow_inputs.sh [options]
  --scope aichi-minimal|full-processing  Download Aichi minimum (default) or all selected data
  --dest PATH                            PFLOW_HOME destination (default: .local/pflow)
  --dry-run                              Show AWS CLI actions without downloading
  -h, --help                             Show this help

full-processing is intentionally rejected for the internal .local/pflow area.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scope) SCOPE="$2"; shift 2 ;;
    --dest) DESTINATION="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$SCOPE" != "aichi-minimal" && "$SCOPE" != "full-processing" ]]; then
  echo "Invalid scope: $SCOPE" >&2
  exit 2
fi
command -v aws >/dev/null || { echo "aws CLI is required" >&2; exit 1; }

mkdir -p "$DESTINATION"
DESTINATION="$(cd "$DESTINATION" && pwd)"
INTERNAL_ROOT="$PROJECT_ROOT/.local/pflow"
INTERNAL=0
case "$DESTINATION/" in
  "$INTERNAL_ROOT"/*|"$INTERNAL_ROOT"/) INTERNAL=1 ;;
esac

if [[ "$SCOPE" == "full-processing" && "$INTERNAL" -eq 1 ]]; then
  echo "Refusing full-processing download into internal .local/pflow; use the SSD destination." >&2
  exit 1
fi

free_kib="$(df -Pk "$DESTINATION" | awk 'NR==2 {print $4}')"
free_bytes=$((free_kib * 1024))
existing_kib="$(du -sk "$DESTINATION" | awk '{print $1}')"
existing_bytes=$((existing_kib * 1024))
if [[ "$SCOPE" == "aichi-minimal" && "$INTERNAL" -eq 1 ]]; then
  if (( existing_bytes + MINIMAL_EXPECTED_BYTES > LOCAL_CAP_BYTES )); then
    echo "Refusing download: estimated internal PFLOW usage would exceed 10 GiB." >&2
    exit 1
  fi
  if (( free_bytes - MINIMAL_EXPECTED_BYTES < MIN_FREE_BYTES )); then
    echo "Refusing download: estimated free space would fall below 20 GiB." >&2
    exit 1
  fi
fi

common=(--only-show-errors --case-conflict error)
if [[ "$DRY_RUN" -eq 1 ]]; then
  common+=(--dryrun)
fi

mkdir -p "$DESTINATION/data/facilities" "$DESTINATION/data/census/person/agent"

if [[ "$SCOPE" == "aichi-minimal" ]]; then
  filters=(
    --exclude "*"
    --include "base_station.csv"
    --include "city_boundary.csv"
    --include "city_census_od.csv"
    --include "city_hospital.csv"
    --include "city_pre_school.csv"
    --include "city_restaurant.csv"
    --include "city_retail.csv"
    --include "city_school.csv"
    --include "city_tatemono.csv"
    --include "mesh_ecensus.csv"
    --include "mnl/labor_params.csv"
    --include "mnl/nolabor_params.csv"
    --include "mnl/student1_params.csv"
    --include "mnl/student2_params.csv"
    --include "markov/v2/chukyo2011_*_prob.csv"
    --include "school/primary_23.csv"
    --include "school/secondary_23.csv"
  )
  aws s3 sync s3://pseudo-pflow/processing/ver3.0/ "$DESTINATION/data/facilities/" "${filters[@]}" "${common[@]}"
  aws s3 sync s3://pseudo-pflow/ver3.0/agent/23/ "$DESTINATION/data/census/person/agent/23/" "${common[@]}"
else
  aws s3 sync s3://pseudo-pflow/processing/ver3.0/ "$DESTINATION/data/facilities/" "${common[@]}"
  aws s3 sync s3://pseudo-pflow/ver3.0/agent/ "$DESTINATION/data/census/person/agent/" "${common[@]}"
fi

if [[ "$DRY_RUN" -eq 0 ]]; then
  final_kib="$(du -sk "$DESTINATION" | awk '{print $1}')"
  final_bytes=$((final_kib * 1024))
  final_free_kib="$(df -Pk "$DESTINATION" | awk 'NR==2 {print $4}')"
  final_free_bytes=$((final_free_kib * 1024))
  if [[ "$INTERNAL" -eq 1 && "$final_bytes" -gt "$LOCAL_CAP_BYTES" ]]; then
    echo "ERROR: internal PFLOW usage exceeded 10 GiB; stop before any further download." >&2
    exit 1
  fi
  if [[ "$INTERNAL" -eq 1 && "$final_free_bytes" -lt "$MIN_FREE_BYTES" ]]; then
    echo "ERROR: internal free space is below 20 GiB; stop before any further download." >&2
    exit 1
  fi
  du -sh "$DESTINATION"
  df -h "$DESTINATION" | tail -1
  echo "PFLOW_HOME=$DESTINATION"
fi
