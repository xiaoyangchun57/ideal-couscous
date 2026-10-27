#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${STATION_RETENTION_ENV_FILE:-/etc/water-monitor/station-ingest-retention.env}"
[[ -r "$ENV_FILE" ]] || { echo "Retention environment is not readable: $ENV_FILE" >&2; exit 1; }
set -a
source "$ENV_FILE"
set +a

required=(
  STATION_RETENTION_BATCH_LIMIT
  STATION_ARCHIVE_MINIMUM_TOTAL_BYTES STATION_ARCHIVE_MINIMUM_FREE_BYTES
  STATION_HOT_MINIMUM_TOTAL_BYTES STATION_HOT_MINIMUM_FREE_BYTES
)
for name in "${required[@]}"; do
  [[ "${!name:-}" =~ ^[1-9][0-9]*$ ]] || {
    echo "Archive setting must be a positive integer: $name" >&2
    exit 1
  }
done

receiver="${STATION_RECEIVER_CONTAINER:-water-monitor-station-ingest}"
day="${ARCHIVE_DAY:-$(date -u -d yesterday +%F)}"
[[ "$day" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { echo "ARCHIVE_DAY must use YYYY-MM-DD" >&2; exit 1; }

test "$(docker inspect "$receiver" --format '{{.State.Running}}')" = true
test "$(docker inspect "$receiver" --format '{{.State.Health.Status}}')" = healthy

result="$(docker exec -e SQLITE_TMPDIR=/var/lib/station-ingest/raw "$receiver" \
  python station_ingest_retention.py archive \
  --database /app/backend/data/water.db \
  --day "$day" \
  --raw-storage-dir /var/lib/station-ingest/raw \
  --storage-mode governance \
  --batch-limit "$STATION_RETENTION_BATCH_LIMIT" \
  --archive-minimum-total-bytes "$STATION_ARCHIVE_MINIMUM_TOTAL_BYTES" \
  --archive-minimum-free-bytes "$STATION_ARCHIVE_MINIMUM_FREE_BYTES" \
  --hot-minimum-total-bytes "$STATION_HOT_MINIMUM_TOTAL_BYTES" \
  --hot-minimum-free-bytes "$STATION_HOT_MINIMUM_FREE_BYTES")"

python3 - "$result" "$day" "$STATION_RETENTION_BATCH_LIMIT" <<'PY'
import json
import sys

item = json.loads(sys.argv[1])
day = sys.argv[2]
batch_limit = int(sys.argv[3])
if item.get("action") != "archive":
    raise SystemExit("archive result action mismatch")

# Current releases return action/result. The older host wrapper shape remains accepted
# so a wrapper retry can report the already-registered archive without duplicating it.
if "result" in item:
    processed = item["result"]
elif item.get("day") == day and "processed" in item:
    processed = item["processed"]
else:
    raise SystemExit("archive result payload mismatch")
if isinstance(processed, bool) or not isinstance(processed, int) or processed < 0:
    raise SystemExit("archive processed count invalid")

retry_required = processed >= batch_limit
print(json.dumps(
    {
        "action": "archive", "day": day, "processed": processed,
        "retry_required": retry_required,
    },
    sort_keys=True,
    separators=(",", ":"),
))
if retry_required:
    raise SystemExit(75)
PY
