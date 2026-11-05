#!/usr/bin/env bash
set -Eeuo pipefail

[[ $EUID -eq 0 ]] || { echo "Run this installer as root" >&2; exit 1; }
[[ $# -eq 1 ]] || { echo "Usage: $0 /etc/water-monitor/daily-nas-database-backup.env" >&2; exit 1; }
CONFIG_FILE="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ "$CONFIG_FILE" == /* && -f "$CONFIG_FILE" ]] || { echo "Backup config must be an existing absolute file" >&2; exit 1; }
case "$(stat -c %a "$CONFIG_FILE")" in 400|600) ;; *) echo "Backup config must be root-only (0400 or 0600)" >&2; exit 1 ;; esac
[[ "$(stat -c %u "$CONFIG_FILE")" == 0 ]] || { echo "Backup config must be owned by root" >&2; exit 1; }

install -d -m 0755 /usr/local/lib/water-monitor
install -m 0755 "$SCRIPT_DIR/daily-nas-database-backup.py" /usr/local/lib/water-monitor/
install -m 0755 "$SCRIPT_DIR/run-daily-nas-database-backup.sh" /usr/local/sbin/run-daily-nas-database-backup
install -m 0644 "$SCRIPT_DIR/water-monitor-daily-nas-database-backup.service" /etc/systemd/system/
install -m 0644 "$SCRIPT_DIR/water-monitor-daily-nas-database-backup.timer" /etc/systemd/system/
install -m 0755 "$SCRIPT_DIR/archive-water-monitor-raw.sh" /usr/local/sbin/archive-water-monitor-raw
systemctl daemon-reload
sha256sum /usr/local/sbin/archive-water-monitor-raw
echo "Daily NAS database backup and current raw archive wrapper installed but not enabled."
echo "Run one reviewed service start before enabling the timer."
