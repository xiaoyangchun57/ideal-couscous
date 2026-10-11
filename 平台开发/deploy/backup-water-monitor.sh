#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-}"
BACKUP_DIR="${BACKUP_DIR:-/opt/water-monitor-backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
CONTAINER_NAME="${CONTAINER_NAME:-water-monitor}"
TAR_BIN="${TAR_BIN:-tar}"
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
PRIVATE_UPLOAD_ROOT="${PRIVATE_UPLOAD_ROOT:-${APP_DIR}/private_uploads}"
PRIVATE_ORIGINALS_DIR="${PRIVATE_UPLOAD_ROOT}/site_photo_originals"

require_directory() {
  [[ -d "$1" ]] || { echo "Missing directory: $1" >&2; exit 1; }
}

require_readable_directory() {
  require_directory "$1"
  [[ -r "$1" && -x "$1" ]] || { echo "Directory is not readable: $1" >&2; exit 1; }
}

require_directory "$APP_DIR/backend/data"
require_directory "$APP_DIR/frontend/uploads"
require_readable_directory "$PRIVATE_ORIGINALS_DIR"
mkdir -p "$BACKUP_DIR"
umask 077

DATABASE_BACKUP="$BACKUP_DIR/water.db-${STAMP}"
UPLOADS_BACKUP="$BACKUP_DIR/uploads-${STAMP}.tar.gz"
PRIVATE_ORIGINALS_BACKUP="$BACKUP_DIR/private-originals-${STAMP}.tar.gz"
INGEST_ARCHIVE_BACKUP="$BACKUP_DIR/ingest-archives-${STAMP}.tar.gz"
CHECKSUM_FILE="$BACKUP_DIR/SHA256SUMS-${STAMP}.txt"
planned_files=(
  "$DATABASE_BACKUP"
  "$UPLOADS_BACKUP"
  "$PRIVATE_ORIGINALS_BACKUP"
  "$INGEST_ARCHIVE_BACKUP"
  "$CHECKSUM_FILE"
)
cleanup() {
  local status=$?
  rm -f "$SNAPSHOT_ON_HOST"
  if [[ $status -ne 0 ]]; then
    rm -f "${planned_files[@]}"
    echo "Backup failed: no successful backup set was published ($STAMP)" >&2
  fi
  exit "$status"
}
trap cleanup EXIT

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

mv "$SNAPSHOT_ON_HOST" "$DATABASE_BACKUP"
"$TAR_BIN" -C "$APP_DIR/frontend" -czf "$UPLOADS_BACKUP" uploads
"$TAR_BIN" -C "$PRIVATE_UPLOAD_ROOT" -czf "$PRIVATE_ORIGINALS_BACKUP" site_photo_originals
backup_files=("$DATABASE_BACKUP" "$UPLOADS_BACKUP" "$PRIVATE_ORIGINALS_BACKUP")

# Raw-frame archives are sensitive evidence. If present, package them together with
# the database snapshot without printing names or contents to ordinary logs.
if [[ -d "$INGEST_ARCHIVE_DIR" ]]; then
  "$TAR_BIN" -C "$APP_DIR/backend/data" -czf "$INGEST_ARCHIVE_BACKUP" ingest-archives
  backup_files+=("$INGEST_ARCHIVE_BACKUP")
fi
sha256sum "${backup_files[@]}" > "$CHECKSUM_FILE"

find "$BACKUP_DIR" -maxdepth 1 -type f -mtime +"$RETENTION_DAYS" -delete
echo "Backup complete: $BACKUP_DIR ($STAMP)"
