#!/usr/bin/env bash
set -Eeuo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-water-monitor}"
ENV_FILE="${ENV_FILE:-/etc/water-monitor/station-ingest-retention.env}"

failure() {
  local status=$?
  logger -p daemon.err -t water-monitor-station-ingest-retention \
    "station ingestion retention failed with status ${status}"
  exit "$status"
}
trap failure ERR

[[ -r "$ENV_FILE" ]] || { echo "Missing maintenance environment: $ENV_FILE" >&2; exit 1; }
# This file is root-owned and created by the installer from a validated absolute path.
source "$ENV_FILE"
: "${SL651_MIRROR_STORAGE_HOST_PATH:?missing mirror storage path}"
[[ -d "$SL651_MIRROR_STORAGE_HOST_PATH" ]] || {
  echo "Missing mirror storage directory: $SL651_MIRROR_STORAGE_HOST_PATH" >&2
  exit 1
}

APP_DIR="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$CONTAINER_NAME")"
if [[ ! "$APP_DIR" =~ ^/opt/water-monitor-[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
  echo "Invalid application directory discovered from $CONTAINER_NAME: ${APP_DIR:-<empty>}" >&2
  exit 1
fi

cd "$APP_DIR"
docker compose --profile station-ingest-maintenance run --rm --no-deps \
  --volume "$SL651_MIRROR_STORAGE_HOST_PATH:/var/lib/station-ingest/mirror" \
  station-ingest-retention
logger -p daemon.info -t water-monitor-station-ingest-retention \
  "station ingestion retention completed"
