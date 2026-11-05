#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-}"
BACKUP_DIR="${BACKUP_DIR:-}"
INGEST_ARCHIVE_DIR="${INGEST_ARCHIVE_DIR:-}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
CONTAINER_NAME="${CONTAINER_NAME:-water-monitor}"
TAR_BIN="${TAR_BIN:-tar}"
STAT_BIN="${STAT_BIN:-stat}"
STAMP="$(date +%Y%m%d-%H%M%S)"
SNAPSHOT_NAME=".backup-${STAMP}.db.part"

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

discover_database_source_dir() {
  local -a mounts
  mapfile -t mounts < <(docker inspect --format \
    '{{range .Mounts}}{{if eq .Destination "/app/backend/data"}}{{printf "%s\t%s\n" .Type .Source}}{{end}}{{end}}' \
    "$CONTAINER_NAME")
  if [[ ${#mounts[@]} -ne 1 ]]; then
    echo "Container must expose one business database mount: $CONTAINER_NAME" >&2
    return 1
  fi
  local mount_type source_dir
  IFS=$'\t' read -r mount_type source_dir <<< "${mounts[0]}"
  if [[ "$mount_type" != bind || "$source_dir" != /* ]]; then
    echo "Business database mount must be one absolute host bind path" >&2
    return 1
  fi
  printf '%s\n' "$source_dir"
}

DATABASE_SOURCE_DIR="$(discover_database_source_dir)"
CONTAINER_IMAGE_ID="$(docker inspect --format '{{.Image}}' "$CONTAINER_NAME")"
[[ -n "$CONTAINER_IMAGE_ID" ]] || { echo "Cannot resolve running container image ID: $CONTAINER_NAME" >&2; exit 1; }
PRIVATE_UPLOAD_ROOT="${PRIVATE_UPLOAD_ROOT:-${APP_DIR}/private_uploads}"
PRIVATE_ORIGINALS_DIR="${PRIVATE_UPLOAD_ROOT}/site_photo_originals"

[[ "$BACKUP_DIR" == /* ]] || { echo "BACKUP_DIR must be an explicit absolute path" >&2; exit 1; }
[[ "$INGEST_ARCHIVE_DIR" == /* ]] || { echo "INGEST_ARCHIVE_DIR must be an explicit absolute path" >&2; exit 1; }
[[ "$BACKUP_DIR" != /opt && "$BACKUP_DIR" != /opt/* ]] || {
  echo "BACKUP_DIR must not place the large recovery set on the /opt system path" >&2
  exit 1
}

require_directory() {
  [[ -d "$1" ]] || { echo "Missing directory: $1" >&2; exit 1; }
}

require_readable_directory() {
  require_directory "$1"
  [[ -r "$1" && -x "$1" ]] || { echo "Directory is not readable: $1" >&2; exit 1; }
}

require_readable_directory "$DATABASE_SOURCE_DIR"
[[ -r "$DATABASE_SOURCE_DIR/water.db" ]] || {
  echo "Business database is not readable: $DATABASE_SOURCE_DIR/water.db" >&2
  exit 1
}
require_directory "$APP_DIR/frontend/uploads"
require_readable_directory "$PRIVATE_ORIGINALS_DIR"
require_readable_directory "$INGEST_ARCHIVE_DIR"
mkdir -p "$BACKUP_DIR"
umask 077

DATABASE_DEVICE="$("$STAT_BIN" -c %d -- "$DATABASE_SOURCE_DIR")"
BACKUP_DEVICE="$("$STAT_BIN" -c %d -- "$BACKUP_DIR")"
[[ "$DATABASE_DEVICE" != "$BACKUP_DEVICE" ]] || {
  echo "BACKUP_DIR must be on a different filesystem from the business database" >&2
  exit 1
}

DATABASE_BACKUP="$BACKUP_DIR/water.db-${STAMP}"
SNAPSHOT_ON_TARGET="$BACKUP_DIR/$SNAPSHOT_NAME"
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
  rm -f "$SNAPSHOT_ON_TARGET"
  if [[ $status -ne 0 ]]; then
    rm -f "${planned_files[@]}"
    echo "Backup failed: no successful backup set was published ($STAMP)" >&2
  fi
  exit "$status"
}
trap cleanup EXIT

# Use the running release image, but mount the actual business database source
# and the independent backup target explicitly. The source connection is
# read-only; the directory stays writable only for SQLite WAL shared memory.
docker run --rm --network none \
  --mount "type=bind,src=$DATABASE_SOURCE_DIR,dst=/source" \
  --mount "type=bind,src=$BACKUP_DIR,dst=/backup" \
  "$CONTAINER_IMAGE_ID" python -c '
import sqlite3
import sys
source = sqlite3.connect("file:/source/water.db?mode=ro", uri=True)
target = sqlite3.connect(sys.argv[1])
with target:
    source.backup(target)
check = target.execute("PRAGMA quick_check").fetchone()
if not check or check[0] != "ok":
    raise SystemExit("backup quick_check failed")
target.close()
source.close()
' "/backup/$SNAPSHOT_NAME"

[[ -f "$SNAPSHOT_ON_TARGET" ]] || { echo "Backup snapshot was not created on target storage" >&2; exit 1; }
mv "$SNAPSHOT_ON_TARGET" "$DATABASE_BACKUP"
"$TAR_BIN" -C "$APP_DIR/frontend" -czf "$UPLOADS_BACKUP" uploads
"$TAR_BIN" -C "$PRIVATE_UPLOAD_ROOT" -czf "$PRIVATE_ORIGINALS_BACKUP" site_photo_originals
backup_files=("$DATABASE_BACKUP" "$UPLOADS_BACKUP" "$PRIVATE_ORIGINALS_BACKUP")

# Raw-frame archives are sensitive evidence. Their real input is mandatory and
# explicitly configured; do not infer a historical path under the release tree.
"$TAR_BIN" -C "$(dirname "$INGEST_ARCHIVE_DIR")" -czf "$INGEST_ARCHIVE_BACKUP" \
  "$(basename "$INGEST_ARCHIVE_DIR")"
backup_files+=("$INGEST_ARCHIVE_BACKUP")
sha256sum "${backup_files[@]}" > "$CHECKSUM_FILE"

find "$BACKUP_DIR" -maxdepth 1 -type f -mtime +"$RETENTION_DAYS" -delete
echo "Backup complete: $BACKUP_DIR ($STAMP)"
