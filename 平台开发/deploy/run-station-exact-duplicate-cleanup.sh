#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${STATION_EXACT_DUPLICATE_ENV_FILE:-/etc/water-monitor/station-exact-duplicate-cleanup.env}"
[[ -r "$ENV_FILE" ]] || { echo "Exact duplicate cleanup environment is not readable: $ENV_FILE" >&2; exit 1; }
set -a
source "$ENV_FILE"
set +a

required=(
  APP_DIR STATION_EXACT_DUPLICATE_CUTOFF STATION_EXACT_DUPLICATE_RECEIPT_CUTOFF
  STATION_EXACT_DUPLICATE_UPPER_RAW_ID STATION_EXACT_DUPLICATE_BATCH_LIMIT
  STATION_EXACT_DUPLICATE_SCAN_LIMIT STATION_EXACT_DUPLICATE_TOTAL_TIMEOUT_SECONDS
)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { echo "Missing exact duplicate cleanup setting: $name" >&2; exit 1; }
done
[[ "$APP_DIR" =~ ^/opt/water-monitor-[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
  echo "Invalid immutable release directory: $APP_DIR" >&2
  exit 1
}
[[ -f "$APP_DIR/docker-compose.yml" ]] || { echo "Release compose file is missing" >&2; exit 1; }
for name in STATION_EXACT_DUPLICATE_UPPER_RAW_ID STATION_EXACT_DUPLICATE_BATCH_LIMIT \
  STATION_EXACT_DUPLICATE_SCAN_LIMIT STATION_EXACT_DUPLICATE_TOTAL_TIMEOUT_SECONDS; do
  [[ "${!name}" =~ ^[1-9][0-9]*$ ]] || {
    echo "Exact duplicate cleanup setting must be a positive integer: $name" >&2
    exit 1
  }
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
maintenance_module="$(cd "$SCRIPT_DIR/../backend" && pwd)/station_ingest_retention.py"
EXPECTED_MAINTENANCE_MODULE_SHA256="2aca5123cb8d52aa30bed9b7fc67649464613660e2f913ce31cc3bdea214bf4f"
[[ -r "$maintenance_module" ]] || { echo "Reviewed maintenance module is not readable" >&2; exit 1; }
module_sha256="$(sha256sum "$maintenance_module" | awk '{print $1}')"
[[ "$module_sha256" == "$EXPECTED_MAINTENANCE_MODULE_SHA256" ]] || {
  echo "Reviewed maintenance module SHA-256 mismatch" >&2
  exit 1
}

command -v timeout >/dev/null || { echo "GNU timeout is required" >&2; exit 1; }
if [[ "${STATION_EXACT_DUPLICATE_DEADLINE_ACTIVE:-}" != 1 ]]; then
  absolute_deadline=$(( $(date +%s) + STATION_EXACT_DUPLICATE_TOTAL_TIMEOUT_SECONDS ))
  exec timeout --foreground --signal=TERM --kill-after=30s \
    "${STATION_EXACT_DUPLICATE_TOTAL_TIMEOUT_SECONDS}s" env \
    STATION_EXACT_DUPLICATE_DEADLINE_ACTIVE=1 \
    STATION_EXACT_DUPLICATE_ABSOLUTE_DEADLINE_EPOCH="$absolute_deadline" \
    bash "$0" "$@"
fi

remaining_budget() {
  local requested=$1 remaining
  remaining=$(( STATION_EXACT_DUPLICATE_ABSOLUTE_DEADLINE_EPOCH - $(date +%s) - 5 ))
  (( remaining > 0 )) || { echo "Exact duplicate cleanup absolute deadline exhausted" >&2; exit 124; }
  (( requested < remaining )) && printf '%s\n' "$requested" || printf '%s\n' "$remaining"
}

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

run_cleanup_action() {
  "${compose[@]}" --profile station-ingest-maintenance run --rm --no-deps -T \
    --volume "$database_source:/app/backend/data" \
    --volume "$maintenance_module:/app/backend/station_ingest_retention.py:ro" \
    station-ingest-retention \
    python /app/backend/station_ingest_retention.py "$@" \
    --database /app/backend/data/water.db \
    --lock-file /app/backend/data/.station-ingest-retention.lock
}

probe="$("${compose[@]}" --profile station-ingest-maintenance run --rm --no-deps -T \
  --volume "$database_source:/app/backend/data" \
  --volume "$maintenance_module:/app/backend/station_ingest_retention.py:ro" \
  -e "EXPECTED_MAINTENANCE_MODULE_SHA256=$module_sha256" \
  station-ingest-retention /bin/sh -ec '
    actual="$(sha256sum /app/backend/station_ingest_retention.py | awk '\''{print $1}'\'')"
    test "$actual" = "$EXPECTED_MAINTENANCE_MODULE_SHA256"
    python /app/backend/station_ingest_retention.py --help
  ')"
grep -q 'run-exact-duplicate-cleanup' <<< "$probe" || {
  echo "Mounted maintenance module does not expose exact duplicate cleanup actions" >&2
  exit 1
}

job_budget="$(remaining_budget "$STATION_EXACT_DUPLICATE_TOTAL_TIMEOUT_SECONDS")"
result="$(run_cleanup_action run-exact-duplicate-cleanup \
  --cutoff "$STATION_EXACT_DUPLICATE_CUTOFF" \
  --receipt-cutoff "$STATION_EXACT_DUPLICATE_RECEIPT_CUTOFF" \
  --raw-storage-dir /var/lib/station-ingest/raw \
  --upper-raw-id "$STATION_EXACT_DUPLICATE_UPPER_RAW_ID" \
  --batch-limit "$STATION_EXACT_DUPLICATE_BATCH_LIMIT" \
  --scan-limit "$STATION_EXACT_DUPLICATE_SCAN_LIMIT" \
  --candidate-range 2000001-2002000 \
  --candidate-range 4000001-4002000 \
  --candidate-range 6000001-6002000 \
  --candidate-range 7107831-7109830 \
  --time-budget-seconds "$job_budget")"

summary="$(python3 - "$result" <<'PY'
import json
import sys

def last_json(value, label):
    lines = [line for line in value.splitlines() if line.strip()]
    if not lines:
        raise SystemExit(label + " produced no result")
    return json.loads(lines[-1])

cleanup = last_json(sys.argv[1], "exact duplicate cleanup")
if cleanup.get("action") != "run-exact-duplicate-cleanup":
    raise SystemExit("exact duplicate cleanup result mismatch")
result = cleanup.get("result")
if not isinstance(result, dict) or not isinstance(result.get("retry_required"), bool):
    raise SystemExit("exact duplicate cleanup result is invalid")
if not isinstance(result.get("indexes", {}).get("query_plans"), dict):
    raise SystemExit("cleanup index evidence missing")
print(json.dumps({"action": "exact-duplicate-cleanup", "result": result},
                 sort_keys=True, separators=(",", ":")))
PY
)"
printf '%s\n' "$summary"

retry_required="$(python3 - "$summary" <<'PY'
import json, sys
print("true" if json.loads(sys.argv[1])["result"]["retry_required"] else "false")
PY
)"
if [[ "$retry_required" == true ]]; then
  exit 75
fi
