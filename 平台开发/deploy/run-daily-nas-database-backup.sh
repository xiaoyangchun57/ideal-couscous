#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_FILE="${DAILY_NAS_DATABASE_BACKUP_CONFIG:-/etc/water-monitor/daily-nas-database-backup.env}"
SCRIPT="${DAILY_NAS_DATABASE_BACKUP_SCRIPT:-/usr/local/lib/water-monitor/daily-nas-database-backup.py}"
LOCK_FILE="${DAILY_NAS_DATABASE_BACKUP_LOCK:-/run/lock/water-monitor-daily-nas-database-backup.lock}"

[[ -r "$CONFIG_FILE" ]] || { echo "Daily NAS backup config is not readable: $CONFIG_FILE" >&2; exit 1; }
[[ -x "$SCRIPT" ]] || { echo "Daily NAS backup program is not executable: $SCRIPT" >&2; exit 1; }
exec 9>"$LOCK_FILE"
flock -n 9 || { echo "Daily NAS database backup is already running" >&2; exit 75; }
exec python3 "$SCRIPT" --config "$CONFIG_FILE"
