#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${STATION_RETENTION_ENV_FILE:-/etc/water-monitor/station-ingest-retention.env}"
[[ -r "$ENV_FILE" ]] || { echo "Retention environment is not readable: $ENV_FILE" >&2; exit 1; }
set -a
# The installer writes this root-owned file from validated scalar arguments.
source "$ENV_FILE"
set +a

required=(
  APP_DIR STATION_RETENTION_BATCH_LIMIT STATION_RETENTION_TIME_BUDGET_SECONDS
  STATION_ARCHIVE_MINIMUM_TOTAL_BYTES STATION_ARCHIVE_MINIMUM_FREE_BYTES
  STATION_HOT_MINIMUM_TOTAL_BYTES STATION_HOT_MINIMUM_FREE_BYTES
)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { echo "Missing retention setting: $name" >&2; exit 1; }
done
[[ "$APP_DIR" =~ ^/opt/water-monitor-[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
  echo "Invalid immutable release directory: $APP_DIR" >&2
  exit 1
}
[[ -f "$APP_DIR/docker-compose.yml" ]] || { echo "Release compose file is missing" >&2; exit 1; }
for name in "${required[@]:1}"; do
  [[ "${!name}" =~ ^[1-9][0-9]*$ ]] || { echo "Retention setting must be a positive integer: $name" >&2; exit 1; }
done

cd "$APP_DIR"
base_compose="$APP_DIR/docker-compose.yml"
production_compose="$APP_DIR/docker-compose.production.yml"
release_mounts_compose="$APP_DIR/docker-compose.release-mounts.yml"
stable_cutover_compose="$APP_DIR/docker-compose.offline-cutover-current.yml"
stable_cutover_env="$APP_DIR/.env.offline-cutover-current"
default_compose_file="$base_compose:$production_compose:$release_mounts_compose"
IFS=: read -r -a compose_files <<< "${COMPOSE_FILE:-$default_compose_file}"
[[ ${#compose_files[@]} -gt 0 ]] || { echo "COMPOSE_FILE did not select any files" >&2; exit 1; }

stable_cutover_selected=0
for compose_file in "${compose_files[@]}"; do
  case "$compose_file" in
    "$base_compose"|"$production_compose"|"$release_mounts_compose") ;;
    "$stable_cutover_compose") stable_cutover_selected=$((stable_cutover_selected + 1)) ;;
    *) echo "Unsupported retention compose file: $compose_file" >&2; exit 1 ;;
  esac
  [[ -f "$compose_file" ]] || { echo "Retention compose file is missing: $compose_file" >&2; exit 1; }
done
for required_compose in "$base_compose" "$production_compose" "$release_mounts_compose"; do
  [[ ":${COMPOSE_FILE:-$default_compose_file}:" == *":$required_compose:"* ]] || {
    echo "Required retention compose file is not selected: $required_compose" >&2
    exit 1
  }
done
[[ $stable_cutover_selected -le 1 ]] || { echo "Stable cutover compose file is selected more than once" >&2; exit 1; }

compose=(docker compose)
if [[ $stable_cutover_selected -eq 1 || -n "${COMPOSE_ENV_FILE:-}" ]]; then
  [[ $stable_cutover_selected -eq 1 && "${COMPOSE_ENV_FILE:-}" == "$stable_cutover_env" ]] || {
    echo "Stable retention mode requires both $stable_cutover_compose and $stable_cutover_env" >&2
    exit 1
  }
  [[ -r "$stable_cutover_env" ]] || { echo "Stable cutover environment is not readable: $stable_cutover_env" >&2; exit 1; }
  compose+=(--env-file "$stable_cutover_env")
fi
compose+=(--project-directory "$APP_DIR")
for compose_file in "${compose_files[@]}"; do
  compose+=(-f "$compose_file")
done
database_source="$("${compose[@]}" --profile station-ingest --profile station-ingest-maintenance config --format json | python3 -c '
import json, sys
config = json.load(sys.stdin)
def source(service):
    mounts = [v for v in config["services"][service].get("volumes", [])
              if v.get("target") == "/app/backend/data" and v.get("type") == "bind"]
    if len(mounts) != 1:
        raise SystemExit("Missing unique business database bind mount: " + service)
    return mounts[0]["source"]
web, receiver = source("water-monitor"), source("station-ingest")
if web != receiver:
    raise SystemExit("Business database mounts do not match")
if not isinstance(web, str) or not web.startswith("/"):
    raise SystemExit("Business database bind source must be an absolute host path")
print(web)
')"
result="$("${compose[@]}" --profile station-ingest-maintenance run --rm --no-deps -T \
  --volume "$database_source:/app/backend/data" \
  station-ingest-retention \
  python station_ingest_retention.py maintain-once \
  --database /app/backend/data/water.db \
  --raw-storage-dir /var/lib/station-ingest/raw \
  --lock-file /app/backend/data/.station-ingest-retention.lock \
  --storage-mode governance \
  --batch-limit "$STATION_RETENTION_BATCH_LIMIT" \
  --time-budget-seconds "$STATION_RETENTION_TIME_BUDGET_SECONDS" \
  --archive-minimum-total-bytes "$STATION_ARCHIVE_MINIMUM_TOTAL_BYTES" \
  --archive-minimum-free-bytes "$STATION_ARCHIVE_MINIMUM_FREE_BYTES" \
  --hot-minimum-total-bytes "$STATION_HOT_MINIMUM_TOTAL_BYTES" \
  --hot-minimum-free-bytes "$STATION_HOT_MINIMUM_FREE_BYTES")"

printf '%s\n' "$result"
python3 - "$result" <<'PY'
import json
import sys

lines = [line for line in sys.argv[1].splitlines() if line.strip()]
if not lines:
    raise SystemExit("retention command produced no result")
item = json.loads(lines[-1])
result = item.get("result")
if item.get("action") != "maintain-once" or not isinstance(result, dict):
    raise SystemExit("retention result payload mismatch")
if result.get("historical_maintenance_called") != 1:
    raise SystemExit("retention governance was not executed")
if result.get("status") not in {"completed", "partial"}:
    raise SystemExit("retention result status is invalid")
if not isinstance(result.get("retry_required"), bool):
    raise SystemExit("retention retry flag is invalid")
if result["retry_required"]:
    raise SystemExit(75)
PY
