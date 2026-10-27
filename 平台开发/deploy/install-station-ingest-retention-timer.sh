#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run this installer as root" >&2
  exit 1
fi
if [[ $# -ne 7 ]]; then
  echo "Usage: $0 /opt/water-monitor-<release> batch-limit time-budget-seconds archive-total-bytes archive-free-bytes hot-total-bytes hot-free-bytes" >&2
  exit 1
fi

APP_DIR="$1"
shift
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ "$APP_DIR" =~ ^/opt/water-monitor-[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
  echo "Invalid immutable release directory: $APP_DIR" >&2
  exit 1
}
[[ -f "$APP_DIR/docker-compose.yml" ]] || { echo "Release compose file is missing" >&2; exit 1; }
for overlay in docker-compose.production.yml docker-compose.release-mounts.yml; do
  [[ -f "$APP_DIR/$overlay" ]] || { echo "Release compose overlay is missing: $overlay" >&2; exit 1; }
done
for value in "$@"; do
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || { echo "All governance budgets must be positive integers" >&2; exit 1; }
done

install -d -m 0750 /etc/water-monitor
cat > /etc/water-monitor/station-ingest-retention.env <<EOF
APP_DIR=$APP_DIR
COMPOSE_FILE=$APP_DIR/docker-compose.yml:$APP_DIR/docker-compose.production.yml:$APP_DIR/docker-compose.release-mounts.yml
STATION_RETENTION_BATCH_LIMIT=$1
STATION_RETENTION_TIME_BUDGET_SECONDS=$2
STATION_ARCHIVE_MINIMUM_TOTAL_BYTES=$3
STATION_ARCHIVE_MINIMUM_FREE_BYTES=$4
STATION_HOT_MINIMUM_TOTAL_BYTES=$5
STATION_HOT_MINIMUM_FREE_BYTES=$6
EOF
chmod 0640 /etc/water-monitor/station-ingest-retention.env
install -m 0755 "$SCRIPT_DIR/run-station-ingest-retention.sh" \
  /usr/local/sbin/run-station-ingest-retention
install -m 0755 "$SCRIPT_DIR/archive-water-monitor-raw.sh" \
  /usr/local/sbin/archive-water-monitor-raw
install -m 0644 "$SCRIPT_DIR/water-monitor-station-ingest-retention.service" /etc/systemd/system/
install -m 0644 "$SCRIPT_DIR/water-monitor-station-ingest-retention.timer" /etc/systemd/system/
install -m 0644 "$SCRIPT_DIR/water-monitor-station-ingest-retention-alert@.service" /etc/systemd/system/
systemctl daemon-reload
echo "Retention units installed but not enabled. Run one reviewed manual service start before enabling the timer."
