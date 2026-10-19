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
        backup_database,
        verify_station_monitoring_contract,
    )
    from .station_ingest_retention import (
        BOOTSTRAP_CONSUMER_WINDOW,
        RetentionError,
        bootstrap_retention_state,
        drop_duplicate_cleanup_indexes,
        ensure_duplicate_cleanup_indexes,
        reduce_confirmed_minute_duplicates,
    )
except ImportError:  # pragma: no cover - direct script execution
    from migrate_station_ingestion import (
        MigrationError,
        STATION_INGEST_RETENTION_WINDOWS_MIGRATION_VERSION,
        apply_migration,
        backup_database,
        verify_station_monitoring_contract,
    )
    from station_ingest_retention import (
        BOOTSTRAP_CONSUMER_WINDOW,
        RetentionError,
        bootstrap_retention_state,
        drop_duplicate_cleanup_indexes,
        ensure_duplicate_cleanup_indexes,
        reduce_confirmed_minute_duplicates,
    )


CUTOVER_NAME = "station-ingest-necessary-state-v2"


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
    consumer_cutoff = _cutoff(now, BOOTSTRAP_CONSUMER_WINDOW)
    cutoff_hour = datetime.fromisoformat(consumer_cutoff).replace(
        minute=0, second=0, microsecond=0).isoformat()
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
            "SELECT COUNT(*) FROM ingest_raw_frames WHERE received_at>=?", (consumer_cutoff,)
        ).fetchone()[0]
        actual_recent = connection.execute(
            "SELECT COUNT(*) FROM station_ingest_recent_receipts WHERE received_at>=?",
            (consumer_cutoff,),
        ).fetchone()[0]
        if actual_recent != expected_recent:
            raise CutoverError(
                f"consumer receipt preparation mismatch: raw={expected_recent}, prepared={actual_recent}")
        expected_hourly_receipts = connection.execute(
            "SELECT COUNT(*) FROM ingest_raw_frames WHERE received_at>=?", (consumer_cutoff,)
        ).fetchone()[0]
        actual_hourly_receipts = connection.execute(
            """SELECT COALESCE(SUM(receipt_count),0) FROM station_ingest_receipt_hourly
               WHERE bucket_hour>=?""", (cutoff_hour,),
        ).fetchone()[0]
        if actual_hourly_receipts != expected_hourly_receipts:
            raise CutoverError(
                "consumer hourly preparation mismatch: "
                f"raw={expected_hourly_receipts}, prepared={actual_hourly_receipts}")
        latest_mismatches = connection.execute(
            """SELECT COUNT(*) FROM trusted_endpoints endpoint
               LEFT JOIN station_ingest_current_state state ON state.endpoint_id=endpoint.id
               WHERE EXISTS (SELECT 1 FROM ingest_raw_frames raw WHERE raw.endpoint_id=endpoint.id)
                 AND (state.source_key IS NULL
                      OR state.latest_raw_frame_id<>(
                          SELECT raw.id FROM ingest_raw_frames raw
                          WHERE raw.endpoint_id=endpoint.id
                          ORDER BY raw.received_at DESC,raw.id DESC LIMIT 1)
                      OR COALESCE(state.latest_authenticated_raw_frame_id,0)<>COALESCE((
                          SELECT raw.id FROM ingest_raw_frames raw
                          WHERE raw.endpoint_id=endpoint.id
                            AND raw.authentication_status='authenticated'
                          ORDER BY raw.received_at DESC,raw.id DESC LIMIT 1),0))"""
        ).fetchone()[0]
        if latest_mismatches:
            raise CutoverError(
                f"necessary current-state preparation mismatch: endpoints={latest_mismatches}")
        if counts["raw_frame_count"] and not counts["current_state_count"]:
            raise CutoverError("raw history exists but necessary current state is empty")
        counts["expected_recent_receipt_count"] = expected_recent
        counts["expected_hourly_receipt_count"] = expected_hourly_receipts
        counts["latest_authenticated_mismatches"] = latest_mismatches
        counts["consumer_window_hours"] = int(
            BOOTSTRAP_CONSUMER_WINDOW.total_seconds() // 3600)
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


def _state_detail(row) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        detail = json.loads(row["detail"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return detail if isinstance(detail, dict) else {}


def _cutover_recovery(
    database: Path, backup_dir: Path | None, migration_backup: Path | None,
    recovery_reference: str | None, state, previous_detail: dict[str, Any],
) -> tuple[Path | None, str | None, dict[str, str]]:
    recorded = previous_detail.get("recovery")
    if state is not None:
        if not isinstance(recorded, dict):
            raise CutoverError("incomplete cutover has no recorded pre-cleanup recovery source")
        kind = recorded.get("kind")
        reference = recorded.get("reference")
        if not isinstance(reference, str) or not reference:
            raise CutoverError("recorded pre-cleanup recovery source is invalid")
        if kind == "local_backup":
            path = Path(reference)
            if not path.is_file():
                raise CutoverError("recorded pre-cleanup backup is missing")
            return path, None, {"kind": kind, "reference": str(path)}
        if kind == "verified_external":
            if recovery_reference and recovery_reference != reference:
                raise CutoverError("verified recovery reference changed during cutover resume")
            return None, reference, {"kind": kind, "reference": reference}
        raise CutoverError("recorded pre-cleanup recovery source type is invalid")

    if migration_backup is not None:
        path = Path(migration_backup)
        return path, None, {"kind": "local_backup", "reference": str(path)}
    if backup_dir is not None:
        path = backup_database(database, backup_dir)
        return path, None, {"kind": "local_backup", "reference": str(path)}
    if recovery_reference:
        return None, recovery_reference, {
            "kind": "verified_external", "reference": recovery_reference,
        }
    raise CutoverError("cutover requires a pre-cleanup backup or verified recovery set")


def prepare_cutover(
    database: Path, backup_dir: Path | None, *, now: datetime | None = None,
    batch_limit: int = 500, recovery_reference: str | None = None,
) -> dict[str, Any]:
    database = Path(database)
    now = now or datetime.now(timezone.utc)
    backup_dir = Path(backup_dir) if backup_dir is not None else None
    recovery_reference = recovery_reference.strip() if recovery_reference else None
    applied, migration_backup = apply_migration(
        database, backup_dir,
        external_recovery_confirmed=bool(recovery_reference),
    )
    state = _read_cutover_state(database)
    previous_detail = _state_detail(state)
    if (state is not None
            and state["migration_version"] == STATION_INGEST_RETENTION_WINDOWS_MIGRATION_VERSION
            and state["status"] == "completed"):
        recorded = previous_detail.get("recovery")
        completed_backup = None
        completed_reference = recovery_reference
        if isinstance(recorded, dict) and recorded.get("kind") == "local_backup":
            completed_backup = recorded.get("reference")
            completed_reference = None
        elif isinstance(recorded, dict) and recorded.get("kind") == "verified_external":
            completed_reference = recorded.get("reference")
        return {
            "migration": "applied" if applied else "already-applied",
            "backup": completed_backup,
            "recovery_reference": completed_reference,
            "backfill": "already-completed",
            "verification": require_cutover_ready(database),
        }
    backup, recovery_reference, recovery = _cutover_recovery(
        database, backup_dir, migration_backup, recovery_reference, state, previous_detail)
    cursor = int(previous_detail.get("cursor_raw_id") or 0)
    totals = previous_detail.get("duplicate_reduction_totals")
    if not isinstance(totals, dict):
        totals = {"scanned": 0, "eligible": 0, "deleted": 0,
                  "retained_protected": 0, "retained_unclassified": 0}
    phase = "necessary-state"
    _record_state(database, "running", {
        "phase": phase, "cursor_raw_id": cursor,
        "duplicate_reduction_totals": totals,
        "recovery": recovery,
    })
    try:
        bootstrap_counts = bootstrap_retention_state(database, now=now)
        phase = "confirmed-duplicate-reduction"
        cleanup_index_prep = ensure_duplicate_cleanup_indexes(database)
        while True:
            batch = reduce_confirmed_minute_duplicates(
                database, after_id=cursor, batch_limit=batch_limit, now=now)
            cursor = int(batch["cursor_raw_id"])
            for key in totals:
                totals[key] = int(totals[key]) + int(batch.get(key, 0))
            progress = {
                "phase": phase, "cursor_raw_id": cursor,
                "duplicate_reduction_totals": totals, "last_batch": batch,
                "bootstrap": bootstrap_counts, "recovery": recovery,
            }
            _record_state(database, "running", progress)
            if batch["complete"]:
                break
        removed_cleanup_indexes = drop_duplicate_cleanup_indexes(database)
        phase = "necessary-consumer-verification"
        verification = _verify_backfill(database, now=now)
        _record_state(
            database, "completed",
            {
                "phase": "ready", "bootstrap": bootstrap_counts,
                "duplicate_reduction_totals": totals, "cursor_raw_id": cursor,
                "cleanup_index_prep": cleanup_index_prep,
                "removed_cleanup_indexes": removed_cleanup_indexes,
                "verification": verification, "recovery": recovery,
            },
            verification,
        )
        return {
            "migration": "applied" if applied else "already-applied",
            "backup": str(backup) if backup else None,
            "recovery_reference": recovery_reference,
            "backfill": bootstrap_counts,
            "duplicate_reduction": totals,
            "cleanup_index_prep": cleanup_index_prep,
            "removed_cleanup_indexes": removed_cleanup_indexes,
            "verification": verification,
        }
    except Exception as exc:
        try:
            _record_state(database, "failed", {
                "phase": phase, "cursor_raw_id": cursor,
                "duplicate_reduction_totals": totals, "error": str(exc),
                "recovery": recovery,
            })
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
    parser.add_argument("--verified-recovery-reference")
    parser.add_argument("--batch-limit", type=int, default=500)
    arguments = parser.parse_args(argv)
    try:
        if arguments.action == "prepare":
            if arguments.backup_dir is None and not arguments.verified_recovery_reference:
                raise CutoverError(
                    "prepare requires --backup-dir or --verified-recovery-reference")
            result = prepare_cutover(
                arguments.database, arguments.backup_dir, batch_limit=arguments.batch_limit,
                recovery_reference=arguments.verified_recovery_reference)
        else:
            result = require_cutover_ready(arguments.database)
    except (CutoverError, MigrationError, RetentionError, sqlite3.Error) as exc:
        print(json.dumps({"action": arguments.action, "status": "failed", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({"action": arguments.action, "status": "completed", "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
