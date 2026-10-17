"""Explicit station-ingestion migration/backfill cutover gate."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

try:
    from .migrate_station_ingestion import (
        MigrationError,
        STATION_INGEST_RETENTION_WINDOWS_MIGRATION_VERSION,
        apply_migration,
        verify_station_monitoring_contract,
    )
    from .station_ingest_retention import (
        RECEIPT_SUMMARY_RETENTION,
        RECENT_RECEIPT_RETENTION,
        RetentionError,
        bootstrap_retention_state,
    )
except ImportError:  # pragma: no cover - direct script execution
    from migrate_station_ingestion import (
        MigrationError,
        STATION_INGEST_RETENTION_WINDOWS_MIGRATION_VERSION,
        apply_migration,
        verify_station_monitoring_contract,
    )
    from station_ingest_retention import (
        RECEIPT_SUMMARY_RETENTION,
        RECENT_RECEIPT_RETENTION,
        RetentionError,
        bootstrap_retention_state,
    )


CUTOVER_NAME = "station-ingest-retention-windows-v1"


class CutoverError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _cutoff(now: datetime, delta: timedelta) -> str:
    value = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return (value.astimezone(timezone.utc) - delta).replace(microsecond=0).isoformat()


def _record_state(database: Path, status: str, detail: dict[str, Any], counts=None) -> None:
    counts = counts or {}
    with closing(sqlite3.connect(str(database), timeout=30, isolation_level=None)) as connection:
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT INTO station_ingest_cutover_state(
                   cutover_name,migration_version,status,raw_frame_count,raw_max_id,
                   current_state_count,authenticated_source_count,recent_receipt_count,
                   receipt_hour_count,detail,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(cutover_name) DO UPDATE SET
                   migration_version=excluded.migration_version,status=excluded.status,
                   raw_frame_count=excluded.raw_frame_count,raw_max_id=excluded.raw_max_id,
                   current_state_count=excluded.current_state_count,
                   authenticated_source_count=excluded.authenticated_source_count,
                   recent_receipt_count=excluded.recent_receipt_count,
                   receipt_hour_count=excluded.receipt_hour_count,
                   detail=excluded.detail,updated_at=excluded.updated_at""",
            (
                CUTOVER_NAME, STATION_INGEST_RETENTION_WINDOWS_MIGRATION_VERSION, status,
                counts.get("raw_frame_count"), counts.get("raw_max_id"),
                counts.get("current_state_count"), counts.get("authenticated_source_count"),
                counts.get("recent_receipt_count"), counts.get("receipt_hour_count"),
                json.dumps(detail, sort_keys=True), _utc_now(),
            ),
        )
        connection.commit()


def _verify_backfill(database: Path, *, now: datetime) -> dict[str, int]:
    recent_cutoff = _cutoff(now, RECENT_RECEIPT_RETENTION)
    hourly_cutoff = _cutoff(now, RECEIPT_SUMMARY_RETENTION)
    with closing(sqlite3.connect(str(database), timeout=30)) as connection:
        connection.row_factory = sqlite3.Row
        verify_station_monitoring_contract(connection)
        counts = dict(connection.execute(
            """SELECT
                   (SELECT COUNT(*) FROM ingest_raw_frames) AS raw_frame_count,
                   (SELECT COALESCE(MAX(id),0) FROM ingest_raw_frames) AS raw_max_id,
                   (SELECT COUNT(*) FROM station_ingest_current_state) AS current_state_count,
                   (SELECT COUNT(*) FROM station_ingest_current_state
                      WHERE latest_authenticated_at IS NOT NULL) AS authenticated_source_count,
                   (SELECT COUNT(*) FROM station_ingest_recent_receipts) AS recent_receipt_count,
                   (SELECT COUNT(*) FROM station_ingest_receipt_hourly) AS receipt_hour_count"""
        ).fetchone())
        expected_recent = connection.execute(
            "SELECT COUNT(*) FROM ingest_raw_frames WHERE received_at>=?", (recent_cutoff,)
        ).fetchone()[0]
        actual_recent = counts["recent_receipt_count"]
        if actual_recent != expected_recent:
            raise CutoverError(
                f"recent receipt backfill mismatch: raw={expected_recent}, summary={actual_recent}")
        expected_hourly_receipts = connection.execute(
            "SELECT COUNT(*) FROM ingest_raw_frames WHERE received_at>=?", (hourly_cutoff,)
        ).fetchone()[0]
        actual_hourly_receipts = connection.execute(
            "SELECT COALESCE(SUM(receipt_count),0) FROM station_ingest_receipt_hourly"
        ).fetchone()[0]
        if actual_hourly_receipts != expected_hourly_receipts:
            raise CutoverError(
                "hourly receipt backfill mismatch: "
                f"raw={expected_hourly_receipts}, summary={actual_hourly_receipts}")
        latest_mismatches = connection.execute(
            """WITH ranked AS (
                   SELECT id,
                          CASE WHEN endpoint_id IS NOT NULL THEN 'endpoint:' || endpoint_id
                               WHEN station_code IS NOT NULL AND station_code<>''
                                   THEN 'station:' || station_code
                               ELSE 'transport:unidentified' END AS source_key,
                          received_at,
                          ROW_NUMBER() OVER (
                              PARTITION BY CASE WHEN endpoint_id IS NOT NULL THEN 'endpoint:' || endpoint_id
                                                WHEN station_code IS NOT NULL AND station_code<>''
                                                    THEN 'station:' || station_code
                                                ELSE 'transport:unidentified' END
                              ORDER BY received_at DESC,id DESC) AS rank
                   FROM ingest_raw_frames WHERE authentication_status='authenticated'
               )
               SELECT COUNT(*) FROM ranked
               LEFT JOIN station_ingest_current_state state USING(source_key)
               WHERE ranked.rank=1 AND (
                   state.latest_authenticated_raw_frame_id IS NULL
                   OR state.latest_authenticated_raw_frame_id<>ranked.id
                   OR state.latest_authenticated_at<>ranked.received_at)"""
        ).fetchone()[0]
        if latest_mismatches:
            raise CutoverError(
                f"latest authenticated backfill mismatch: sources={latest_mismatches}")
        if counts["raw_frame_count"] and not counts["current_state_count"]:
            raise CutoverError("raw history exists but current-state backfill is empty")
        counts["expected_recent_receipt_count"] = expected_recent
        counts["expected_hourly_receipt_count"] = expected_hourly_receipts
        counts["latest_authenticated_mismatches"] = latest_mismatches
        return {key: int(value) for key, value in counts.items()}


def _read_cutover_state(database: Path):
    database = Path(database)
    if not database.is_file():
        raise CutoverError("station-ingestion database does not exist; run cutover prepare first")
    with closing(sqlite3.connect(str(database), timeout=5)) as connection:
        connection.row_factory = sqlite3.Row
        try:
            verify_station_monitoring_contract(connection)
            row = connection.execute(
                "SELECT * FROM station_ingest_cutover_state WHERE cutover_name=?",
                (CUTOVER_NAME,),
            ).fetchone()
        except (MigrationError, sqlite3.Error) as exc:
            raise CutoverError(f"station-ingestion cutover is unavailable: {exc}") from exc
    return row


def require_cutover_ready(database: Path) -> dict[str, Any]:
    row = _read_cutover_state(database)
    if row is None or row["migration_version"] != STATION_INGEST_RETENTION_WINDOWS_MIGRATION_VERSION:
        raise CutoverError("station-ingestion cutover has not completed for migration 014")
    if row["status"] != "completed":
        raise CutoverError(
            f"station-ingestion cutover is {row['status']}: {row['detail']}")
    return {
        "cutover_name": row["cutover_name"],
        "migration_version": row["migration_version"],
        "status": row["status"],
        "updated_at": row["updated_at"],
    }


def prepare_cutover(database: Path, backup_dir: Path, *, now: datetime | None = None) -> dict[str, Any]:
    database = Path(database)
    now = now or datetime.now(timezone.utc)
    applied, backup = apply_migration(database, Path(backup_dir))
    state = _read_cutover_state(database)
    if (state is not None
            and state["migration_version"] == STATION_INGEST_RETENTION_WINDOWS_MIGRATION_VERSION
            and state["status"] == "completed"):
        return {
            "migration": "applied" if applied else "already-applied",
            "backup": str(backup) if backup else None,
            "backfill": "already-completed",
            "verification": require_cutover_ready(database),
        }
    _record_state(database, "running", {"phase": "historical-summary-backfill"})
    try:
        bootstrap_counts = bootstrap_retention_state(database, now=now)
        verification = _verify_backfill(database, now=now)
        _record_state(
            database, "completed",
            {"phase": "ready", "bootstrap": bootstrap_counts, "verification": verification},
            verification,
        )
        return {
            "migration": "applied" if applied else "already-applied",
            "backup": str(backup) if backup else None,
            "backfill": bootstrap_counts,
            "verification": verification,
        }
    except Exception as exc:
        try:
            _record_state(database, "failed", {"phase": "historical-summary-backfill", "error": str(exc)})
        except sqlite3.Error:
            pass
        if isinstance(exc, (CutoverError, MigrationError, RetentionError, sqlite3.Error)):
            raise
        raise CutoverError("station-ingestion cutover failed") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare or verify station-ingestion cutover")
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--backup-dir", type=Path)
    arguments = parser.parse_args(argv)
    try:
        if arguments.action == "prepare":
            if arguments.backup_dir is None:
                raise CutoverError("prepare requires --backup-dir")
            result = prepare_cutover(arguments.database, arguments.backup_dir)
        else:
            result = require_cutover_ready(arguments.database)
    except (CutoverError, MigrationError, RetentionError, sqlite3.Error) as exc:
        print(json.dumps({"action": arguments.action, "status": "failed", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({"action": arguments.action, "status": "completed", "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
