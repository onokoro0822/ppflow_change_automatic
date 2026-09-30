#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="$PROJECT_ROOT/.local/pflow"
DESTINATION="/Volumes/PFLOW_SSD/PFLOW"
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: scripts/migrate_pflow_to_ssd.sh [--source PATH] [--dest PATH] [--dry-run]
Copies the partial internal PFLOW tree to the SSD without deleting the source,
then verifies file count and byte total. No --delete operation is used.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE="$2"; shift 2 ;;
    --dest) DESTINATION="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -d "$SOURCE" ]] || { echo "Source does not exist: $SOURCE" >&2; exit 1; }
volume_root="/Volumes/$(printf '%s' "$DESTINATION" | cut -d/ -f3)"
[[ -d "$volume_root" ]] || { echo "SSD volume is not mounted: $volume_root" >&2; exit 1; }
mkdir -p "$DESTINATION"

rsync_args=(-a -h --progress)
if [[ "$DRY_RUN" -eq 1 ]]; then
  rsync_args+=(-n)
fi
rsync "${rsync_args[@]}" "$SOURCE/" "$DESTINATION/"

if [[ "$DRY_RUN" -eq 0 ]]; then
  source_files="$(find "$SOURCE" -type f | wc -l | tr -d ' ')"
  dest_files="$(find "$DESTINATION" -type f | wc -l | tr -d ' ')"
  source_kib="$(du -sk "$SOURCE" | awk '{print $1}')"
  dest_kib="$(du -sk "$DESTINATION" | awk '{print $1}')"
  if [[ "$source_files" != "$dest_files" || "$source_kib" != "$dest_kib" ]]; then
    echo "Verification failed: source files/KiB=$source_files/$source_kib, destination=$dest_files/$dest_kib" >&2
    exit 1
  fi
  echo "Verified: $dest_files files, $dest_kib KiB"
  echo "export PFLOW_HOME='$DESTINATION'"
  echo "The source remains at $SOURCE until the SSD-side experiment is verified."
fi
