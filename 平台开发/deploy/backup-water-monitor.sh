#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/water-monitor-20260730}"
BACKUP_DIR="${BACKUP_DIR:-/opt/water-monitor-backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
CONTAINER_NAME="${CONTAINER_NAME:-water-monitor}"
STAMP="$(date +%Y%m%d-%H%M%S)"
SNAPSHOT_NAME=".backup-${STAMP}.db"
SNAPSHOT_IN_CONTAINER="/app/backend/data/${SNAPSHOT_NAME}"
SNAPSHOT_ON_HOST="${APP_DIR}/backend/data/${SNAPSHOT_NAME}"

require_directory() {
  [[ -d "$1" ]] || { echo "Missing directory: $1" >&2; exit 1; }
}

require_directory "$APP_DIR/backend/data"
require_directory "$APP_DIR/frontend/uploads"
mkdir -p "$BACKUP_DIR"
umask 077

docker inspect "$CONTAINER_NAME" >/dev/null

# SQLite's backup API creates a consistent snapshot while the application runs.
docker exec "$CONTAINER_NAME" python -c '
import sqlite3
source = sqlite3.connect("/app/backend/data/water.db")
target = sqlite3.connect("'"$SNAPSHOT_IN_CONTAINER"'")
with target:
    source.backup(target)
target.close()
source.close()
'

trap 'rm -f "$SNAPSHOT_ON_HOST"' EXIT
mv "$SNAPSHOT_ON_HOST" "$BACKUP_DIR/water.db-${STAMP}"
tar -C "$APP_DIR/frontend" -czf "$BACKUP_DIR/uploads-${STAMP}.tar.gz" uploads
sha256sum "$BACKUP_DIR/water.db-${STAMP}" "$BACKUP_DIR/uploads-${STAMP}.tar.gz" \
  > "$BACKUP_DIR/SHA256SUMS-${STAMP}.txt"

find "$BACKUP_DIR" -maxdepth 1 -type f -mtime +"$RETENTION_DAYS" -delete
echo "Backup complete: $BACKUP_DIR ($STAMP)"
