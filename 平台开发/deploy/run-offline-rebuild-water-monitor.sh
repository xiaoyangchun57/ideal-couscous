#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: run-offline-rebuild-water-monitor.sh \
  --consistent-source-confirmed \
  --source /absolute/path/water.db \
  --source-manifest /absolute/path/SOURCE_MANIFEST.json \
  --rules /absolute/path/rebuild-rules.json \
  --as-of 2026-10-28T12:00:00+08:00 \
  --target /absolute/data/path/rebuilt-water.db \
  --report /absolute/data/path/rebuild-report.json \
  --archive /absolute/data/path/rebuilt-water.zip \
  --temp-dir /absolute/data/path/sqlite-temp \
  --minimum-free-bytes POSITIVE_INTEGER \
  --space-requirement-basis "release preflight calculation"

The source must be an immutable database after all writers have stopped, or a
complete consistent snapshot. This wrapper does not stop services, checkpoint
WAL, switch bind mounts, run recovery, or copy the source to a system disk.
EOF
  exit 64
}

source_db=""
source_manifest=""
rules=""
as_of=""
target=""
report=""
archive=""
temp_dir=""
minimum_free_bytes=""
space_requirement_basis=""
consistent_source_confirmed=0

while (($#)); do
  case "$1" in
    --consistent-source-confirmed)
      consistent_source_confirmed=1
      shift
      ;;
    --source)
      (($# >= 2)) || usage
      source_db="$2"
      shift 2
      ;;
    --source-manifest|--rules|--as-of|--target|--report|--archive|--temp-dir|--minimum-free-bytes|--space-requirement-basis)
      (($# >= 2)) || usage
      name="${1#--}"
      name="${name//-/_}"
      printf -v "$name" '%s' "$2"
      shift 2
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      ;;
  esac
done

((consistent_source_confirmed == 1)) || {
  echo "Refusing rebuild: confirm a stopped-writer source or complete consistent snapshot." >&2
  exit 64
}

required=(
  source_db source_manifest rules as_of target report archive temp_dir
  minimum_free_bytes space_requirement_basis
)
for name in "${required[@]}"; do
  [[ -n "${!name}" ]] || { echo "Missing required rebuild argument: $name" >&2; exit 64; }
done

for name in source_db source_manifest rules target report archive temp_dir; do
  [[ "${!name}" == /* ]] || { echo "Path must be absolute: $name" >&2; exit 64; }
done
for name in source_db source_manifest rules; do
  [[ -f "${!name}" && -r "${!name}" ]] || {
    echo "Required input is not a readable file: $name=${!name}" >&2
    exit 66
  }
done
for name in target report archive; do
  [[ ! -e "${!name}" ]] || { echo "Output already exists: ${!name}" >&2; exit 73; }
done
[[ "$minimum_free_bytes" =~ ^[1-9][0-9]*$ ]] || {
  echo "--minimum-free-bytes must be a positive integer" >&2
  exit 64
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$script_dir/offline-rebuild-water-monitor.py" rebuild \
  --source "$source_db" \
  --source-manifest "$source_manifest" \
  --rules "$rules" \
  --as-of "$as_of" \
  --target "$target" \
  --report "$report" \
  --archive "$archive" \
  --temp-dir "$temp_dir" \
  --minimum-free-bytes "$minimum_free_bytes" \
  --space-requirement-basis "$space_requirement_basis"
