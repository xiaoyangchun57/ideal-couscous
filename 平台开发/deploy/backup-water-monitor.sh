#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-}"
BACKUP_DIR="${BACKUP_DIR:-/opt/water-monitor-backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
CONTAINER_NAME="${CONTAINER_NAME:-water-monitor}"
STAMP="$(date +%Y%m%d-%H%M%S)"
SNAPSHOT_NAME=".backup-${STAMP}.db"
SNAPSHOT_IN_CONTAINER="/app/backend/data/${SNAPSHOT_NAME}"

discover_app_dir() {
  local discovered
  if ! discovered="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$CONTAINER_NAME")"; then
    echo "Cannot discover APP_DIR from running container: $CONTAINER_NAME" >&2
    return 1
  fi
  if [[ ! "$discovered" =~ ^/opt/water-monitor-[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    echo "Invalid Docker Compose working directory for $CONTAINER_NAME: ${discovered:-<empty>}" >&2
    return 1
  fi
  printf '%s\n' "$discovered"
}

if [[ -z "$APP_DIR" ]]; then
  APP_DIR="$(discover_app_dir)"
fi

SNAPSHOT_ON_HOST="${APP_DIR}/backend/data/${SNAPSHOT_NAME}"
INGEST_ARCHIVE_DIR="${APP_DIR}/backend/data/ingest-archives"

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
DATABASE_BACKUP="$BACKUP_DIR/water.db-${STAMP}"
UPLOADS_BACKUP="$BACKUP_DIR/uploads-${STAMP}.tar.gz"
mv "$SNAPSHOT_ON_HOST" "$DATABASE_BACKUP"
tar -C "$APP_DIR/frontend" -czf "$UPLOADS_BACKUP" uploads

backup_files=("$DATABASE_BACKUP" "$UPLOADS_BACKUP")
# Raw-frame archives are sensitive evidence. If present, package them together with
# the database snapshot without printing names or contents to ordinary logs.
if [[ -d "$INGEST_ARCHIVE_DIR" ]]; then
  INGEST_ARCHIVE_BACKUP="$BACKUP_DIR/ingest-archives-${STAMP}.tar.gz"
  tar -C "$APP_DIR/backend/data" -czf "$INGEST_ARCHIVE_BACKUP" ingest-archives
  backup_files+=("$INGEST_ARCHIVE_BACKUP")
fi
sha256sum "${backup_files[@]}" > "$BACKUP_DIR/SHA256SUMS-${STAMP}.txt"

find "$BACKUP_DIR" -maxdepth 1 -type f -mtime +"$RETENTION_DAYS" -delete
echo "Backup complete: $BACKUP_DIR ($STAMP)"
