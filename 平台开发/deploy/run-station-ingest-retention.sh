#!/usr/bin/env bash
set -Eeuo pipefail

message="historical station-ingestion maintenance is disabled for this release; scheduled run skipped"
printf '%s\n' \
  '{"action":"maintain-once","result":{"historical_maintenance_called":0,"reason":"historical station-ingestion maintenance is disabled for this release; run it only after separate approval","retry_required":false,"status":"skipped"}}'
if command -v logger >/dev/null 2>&1; then
  logger -p daemon.info -t water-monitor-station-ingest-retention "$message" || true
fi
