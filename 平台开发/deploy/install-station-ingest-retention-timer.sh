#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run this installer as root" >&2
  exit 1
fi
if [[ $# -ne 1 || ! "$1" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
  echo "Usage: $0 /absolute/verified/mirror-storage-path" >&2
  exit 1
fi

MIRROR_PATH="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -d "$MIRROR_PATH" && -r "$MIRROR_PATH" && -w "$MIRROR_PATH" && -x "$MIRROR_PATH" ]] || {
  echo "Mirror storage path must already exist and be readable/writable: $MIRROR_PATH" >&2
  exit 1
}

install -d -m 0750 /etc/water-monitor
printf 'SL651_MIRROR_STORAGE_HOST_PATH=%s\n' "$MIRROR_PATH" \
  > /etc/water-monitor/station-ingest-retention.env
chmod 0640 /etc/water-monitor/station-ingest-retention.env
install -m 0755 "$SCRIPT_DIR/run-station-ingest-retention.sh" \
  /usr/local/sbin/run-station-ingest-retention
install -m 0644 "$SCRIPT_DIR/water-monitor-station-ingest-retention.service" /etc/systemd/system/
install -m 0644 "$SCRIPT_DIR/water-monitor-station-ingest-retention.timer" /etc/systemd/system/
install -m 0644 "$SCRIPT_DIR/water-monitor-station-ingest-retention-alert@.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now water-monitor-station-ingest-retention.timer
systemctl list-timers water-monitor-station-ingest-retention.timer --no-pager
