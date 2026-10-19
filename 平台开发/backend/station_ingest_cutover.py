"""Explicit station-ingestion migration/backfill cutover gate."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any

try:
    from .migrate_station_ingestion import (
        MigrationError,
        STATION_INGEST_RETENTION_WINDOWS_MIGRATION_VERSION,
        apply_migration,
        verify_station_monitoring_contract,
    )
    from .station_ingest_retention import (
        BOOTSTRAP_CONSUMER_WINDOW,
        RetentionError,
        prepare_recent_slot_summaries,
        prepare_startup_state,
    )
except ImportError:  # pragma: no cover - direct script execution
    from migrate_station_ingestion import (
        MigrationError,
        STATION_INGEST_RETENTION_WINDOWS_MIGRATION_VERSION,
        apply_migration,
        verify_station_monitoring_contract,
    )
    from station_ingest_retention import (
        BOOTSTRAP_CONSUMER_WINDOW,
        RetentionError,
        prepare_recent_slot_summaries,
        prepare_startup_state,
    )


CUTOVER_NAME = "station-ingest-startup-ready-v1"


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


def _verify_startup(
    database: Path, *, now: datetime, recent_summary: dict[str, int],
) -> dict[str, int]:
    consumer_cutoff = _cutoff(now, BOOTSTRAP_CONSUMER_WINDOW)
    cutoff_hour = datetime.fromisoformat(consumer_cutoff).replace(
        minute=0, second=0, microsecond=0).isoformat()
    with closing(sqlite3.connect(str(database), timeout=30)) as connection:
        connection.row_factory = sqlite3.Row
        verify_station_monitoring_contract(connection)
        counts = dict(connection.execute(
            """SELECT
                   (SELECT COALESCE(MAX(id),0) FROM ingest_raw_frames) AS raw_max_id,
                   (SELECT COUNT(*) FROM station_ingest_current_state) AS current_state_count,
                   (SELECT COUNT(*) FROM station_ingest_current_state
                      WHERE latest_authenticated_at IS NOT NULL) AS authenticated_source_count,
                   (SELECT COUNT(*) FROM station_ingest_recent_receipts
                      WHERE received_at>=?) AS recent_receipt_count,
                   (SELECT COUNT(*) FROM station_ingest_receipt_hourly
                      WHERE bucket_hour>=?) AS receipt_hour_count,
                   (SELECT COUNT(*) FROM station_ingest_slot_summaries
                      WHERE last_received_at>=?) AS recent_slot_summary_count""",
            (consumer_cutoff, cutoff_hour, consumer_cutoff),
        ).fetchone())
        expected_recent = connection.execute(
            """SELECT COUNT(*) FROM ingest_raw_frames
                    INDEXED BY idx_ingest_raw_received WHERE received_at>=?""",
            (consumer_cutoff,),
        ).fetchone()[0]
        actual_recent = connection.execute(
            "SELECT COUNT(*) FROM station_ingest_recent_receipts WHERE received_at>=?",
            (consumer_cutoff,),
        ).fetchone()[0]
        if actual_recent != expected_recent:
            raise CutoverError(
                f"consumer receipt preparation mismatch: raw={expected_recent}, prepared={actual_recent}")
        expected_hourly_receipts = connection.execute(
            """SELECT COUNT(*) FROM ingest_raw_frames
                    INDEXED BY idx_ingest_raw_received WHERE received_at>=?""",
            (consumer_cutoff,),
        ).fetchone()[0]
        actual_hourly_receipts = connection.execute(
            """SELECT COALESCE(SUM(receipt_count),0) FROM station_ingest_receipt_hourly
               WHERE bucket_hour>=?""", (cutoff_hour,),
        ).fetchone()[0]
        if actual_hourly_receipts < expected_hourly_receipts:
            raise CutoverError(
                "consumer hourly preparation mismatch: "
                f"minimum_raw={expected_hourly_receipts}, prepared={actual_hourly_receipts}")
        latest_mismatches = connection.execute(
            """SELECT COUNT(*) FROM trusted_endpoints endpoint
               LEFT JOIN station_ingest_current_state state ON state.endpoint_id=endpoint.id
               WHERE EXISTS (
                         SELECT 1 FROM ingest_raw_frames raw
                              INDEXED BY idx_ingest_raw_endpoint_received
                         WHERE raw.endpoint_id=endpoint.id)
                 AND (state.source_key IS NULL
                      OR state.latest_raw_frame_id<>(
                           SELECT raw.id FROM ingest_raw_frames raw
                                INDEXED BY idx_ingest_raw_endpoint_received
                          WHERE raw.endpoint_id=endpoint.id
                          ORDER BY raw.received_at DESC,raw.id DESC LIMIT 1)
                      OR COALESCE(state.latest_authenticated_raw_frame_id,0)<>COALESCE((
                           SELECT raw.id FROM ingest_raw_frames raw
                                INDEXED BY idx_ingest_raw_authenticated_endpoint_received
                          WHERE raw.endpoint_id=endpoint.id
                            AND raw.authentication_status='authenticated'
                          ORDER BY raw.received_at DESC,raw.id DESC LIMIT 1),0))"""
        ).fetchone()[0]
        if latest_mismatches:
            raise CutoverError(
                f"necessary current-state preparation mismatch: endpoints={latest_mismatches}")
        expected_slot_identities = int(recent_summary.get("slot_identities", 0))
        if counts["recent_slot_summary_count"] < expected_slot_identities:
            raise CutoverError(
                "recent slot summary preparation mismatch: "
                f"expected={expected_slot_identities}, prepared={counts['recent_slot_summary_count']}")
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
        raise CutoverError("station-ingestion startup has not completed for migration 014")
    if row["status"] != "completed":
        raise CutoverError(
            f"station-ingestion startup is {row['status']}: {row['detail']}")
    return {
        "cutover_name": row["cutover_name"],
        "migration_version": row["migration_version"],
        "status": row["status"],
        "updated_at": row["updated_at"],
    }


def _phase_result(
    started_at: str, started_clock: float, counts: dict[str, Any],
) -> dict[str, Any]:
    return {
        "started_at": started_at,
        "completed_at": _utc_now(),
        "elapsed_seconds": round(time.perf_counter() - started_clock, 3),
        "counts": counts,
    }


def prepare_cutover(
    database: Path, backup_dir: Path | None, *, now: datetime | None = None,
    batch_limit: int = 500, recovery_reference: str | None = None,
) -> dict[str, Any]:
    database = Path(database)
    now = now or datetime.now(timezone.utc)
    backup_dir = Path(backup_dir) if backup_dir is not None else None
    recovery_reference = recovery_reference.strip() if recovery_reference else None
    del batch_limit  # Retained only for command compatibility with the previous cutover.
    phases: dict[str, dict[str, Any]] = {}
    phase = "structure"
    phase_started_at = _utc_now()
    phase_started_clock = time.perf_counter()
    recovery: dict[str, str] = {"kind": "not-required", "reference": "migration-already-applied"}
    try:
        applied, migration_backup = apply_migration(
            database, backup_dir,
            external_recovery_confirmed=bool(recovery_reference),
            require_014_only=True,
        )
        if migration_backup is not None:
            recovery = {"kind": "local_backup", "reference": str(migration_backup)}
        elif applied and recovery_reference:
            recovery = {"kind": "verified_external", "reference": recovery_reference}
        phases[phase] = _phase_result(
            phase_started_at, phase_started_clock,
            {"migration": "applied" if applied else "already-applied"},
        )

        phase = "necessary-state"
        phase_started_at = _utc_now()
        phase_started_clock = time.perf_counter()
        _record_state(database, "running", {
            "phase": phase, "phases": phases, "recovery": recovery,
        })
        startup_counts = prepare_startup_state(database, now=now)
        phases[phase] = _phase_result(
            phase_started_at, phase_started_clock, startup_counts)

        phase = "recent-summary"
        phase_started_at = _utc_now()
        phase_started_clock = time.perf_counter()
        _record_state(database, "running", {
            "phase": phase, "phases": phases, "recovery": recovery,
        }, {
            "current_state_count": startup_counts["current_states"],
            "recent_receipt_count": startup_counts["recent_receipts"],
            "receipt_hour_count": startup_counts["receipt_hour_summaries"],
        })
        recent_summary = prepare_recent_slot_summaries(database, now=now)
        phases[phase] = _phase_result(
            phase_started_at, phase_started_clock, recent_summary)

        phase = "startup-verification"
        phase_started_at = _utc_now()
        phase_started_clock = time.perf_counter()
        _record_state(database, "running", {
            "phase": phase, "phases": phases, "recovery": recovery,
        }, {
            "current_state_count": startup_counts["current_states"],
            "recent_receipt_count": startup_counts["recent_receipts"],
            "receipt_hour_count": startup_counts["receipt_hour_summaries"],
        })
        verification = _verify_startup(
            database, now=now, recent_summary=recent_summary)
        phases[phase] = _phase_result(
            phase_started_at, phase_started_clock, verification)
        _record_state(
            database, "completed",
            {
                "phase": "ready", "phases": phases,
                "startup": startup_counts, "recent_summary": recent_summary,
                "verification": verification, "recovery": recovery,
            },
            verification,
        )
        return {
            "migration": "applied" if applied else "already-applied",
            "backup": str(migration_backup) if migration_backup else None,
            "recovery_reference": recovery_reference if applied and not migration_backup else None,
            "startup": startup_counts,
            "recent_summary": recent_summary,
            "verification": verification,
            "phases": phases,
        }
    except Exception as exc:
        setattr(exc, "startup_phase", phase)
        failed_phase = {
            "started_at": phase_started_at,
            "failed_at": _utc_now(),
            "elapsed_seconds": round(time.perf_counter() - phase_started_clock, 3),
            "error": str(exc),
        }
        try:
            _record_state(database, "failed", {
                "phase": phase, "phases": phases, "failed_phase": failed_phase,
                "recovery": recovery,
            })
        except (MigrationError, sqlite3.Error):
            pass
        if isinstance(exc, (CutoverError, MigrationError, RetentionError, sqlite3.Error)):
            raise
        raise CutoverError(f"station-ingestion startup failed during {phase}") from exc


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
            result = prepare_cutover(
                arguments.database, arguments.backup_dir, batch_limit=arguments.batch_limit,
                recovery_reference=arguments.verified_recovery_reference)
        else:
            result = require_cutover_ready(arguments.database)
    except (CutoverError, MigrationError, RetentionError, sqlite3.Error) as exc:
        payload = {"action": arguments.action, "status": "failed", "error": str(exc)}
        phase = getattr(exc, "startup_phase", None)
        if phase:
            payload["phase"] = phase
        print(json.dumps(payload, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps({"action": arguments.action, "status": "completed", "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
