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
compose=(docker compose --project-directory "$APP_DIR"
  -f "$APP_DIR/docker-compose.yml" -f "$APP_DIR/docker-compose.production.yml"
  -f "$APP_DIR/docker-compose.release-mounts.yml")
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
if web != receiver or web != "/opt/water-monitor-persistent/backend-data":
    raise SystemExit("Business database mounts do not match the approved persistent database")
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
PY
