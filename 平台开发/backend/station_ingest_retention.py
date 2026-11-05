"""Offline retention maintenance for station-ingestion evidence."""
from __future__ import annotations

import argparse
import base64
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
from typing import Any

try:
    from .hj212_parser import FrameError as HJ212FrameError, parse_hj212_frame
    from .station_ingest_slots import HJ212SlotIdentityResolver
except ImportError:  # pragma: no cover - direct script execution
    from hj212_parser import FrameError as HJ212FrameError, parse_hj212_frame
    from station_ingest_slots import HJ212SlotIdentityResolver


class RetentionError(RuntimeError):
    pass


LONG_TERM_MINIMUM_TOTAL_BYTES = 100 * 1024 ** 3
LONG_TERM_MINIMUM_FREE_BYTES = 10 * 1024 ** 3
VALIDATION_MINIMUM_TOTAL_BYTES = 20 * 1024 ** 3
VALIDATION_MINIMUM_FREE_BYTES = 5 * 1024 ** 3
DETAIL_RETENTION = timedelta(hours=72)
HOT_RELATION_RETENTION = timedelta(hours=168)
RECENT_RECEIPT_RETENTION = timedelta(hours=72)
DEDUP_DIRECTORY_RETENTION = timedelta(days=90)
CLOUD_ARCHIVE_RETENTION = timedelta(days=90)
ERROR_SUMMARY_RETENTION = timedelta(hours=24)
INGEST_ERROR_SAMPLE_RETENTION = timedelta(hours=24)
INGEST_ERROR_SAMPLE_MAX_ROWS = 512
INGEST_ERROR_SAMPLE_MAX_BYTES = 1024 * 1024
RECEIPT_SUMMARY_RETENTION = timedelta(days=30)
SLOT_SUMMARY_RETENTION = timedelta(days=90)
BOOTSTRAP_CONSUMER_WINDOW = timedelta(hours=8)
RAW_ARCHIVE_FORMAT_VERSION = 2
LEGACY_RAW_INDEXES = (
    "idx_ingest_raw_frame_hash",
    "idx_ingest_raw_disposition_received",
    "idx_ingest_parse_pending",
    "idx_ingest_raw_authenticated_received",
    "idx_ingest_raw_station_auth_received",
)
CLEANUP_INDEXES = {
    "idx_ingest_errors_raw_cleanup":
        "CREATE INDEX IF NOT EXISTS idx_ingest_errors_raw_cleanup ON ingest_errors(raw_frame_id)",
    "idx_ingest_raw_duplicate_cleanup":
        "CREATE INDEX IF NOT EXISTS idx_ingest_raw_duplicate_cleanup ON ingest_raw_frames(duplicate_of_raw_frame_id)",
}
RAW_PRUNE_SCAN_LIMIT = 256
EXACT_DUPLICATE_BATCH_TIME_BUDGET_SECONDS = 30.0
MAINTENANCE_ARCHIVE_MINIMUM_BUDGET_SECONDS = 2.0
EXACT_DUPLICATE_CHECKPOINT_VERSION = "station-ingest-exact-duplicate-checkpoint-v1"


class _StatementDeadlineExceeded(RuntimeError):
    def __init__(self, stage: str, checkpoint: dict[str, Any] | None = None):
        super().__init__(f"statement_deadline_exhausted:{stage}")
        self.stage = stage
        self.checkpoint = checkpoint


class MaintenanceLock:
    """Hold one cross-process maintenance lease for the complete periodic run."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"\0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise RetentionError("station-ingestion maintenance is already running") from exc
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


@dataclass(frozen=True)
class StoragePolicy:
    root: Path
    minimum_total_bytes: int = LONG_TERM_MINIMUM_TOTAL_BYTES
    minimum_free_bytes: int = LONG_TERM_MINIMUM_FREE_BYTES
    mode: str = "long_term"
    hot_minimum_total_bytes: int | None = None
    hot_minimum_free_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"long_term", "validation", "governance"}:
            raise RetentionError("storage mode is invalid")
        if self.mode == "long_term" and (self.minimum_total_bytes < LONG_TERM_MINIMUM_TOTAL_BYTES
                                          or self.minimum_free_bytes < LONG_TERM_MINIMUM_FREE_BYTES):
            raise RetentionError("long-term storage requires at least 100 GiB")
        if self.mode == "validation" and (self.minimum_total_bytes < VALIDATION_MINIMUM_TOTAL_BYTES
                                            or self.minimum_free_bytes < VALIDATION_MINIMUM_FREE_BYTES):
            raise RetentionError("validation storage requires at least 20 GiB total and 5 GiB free")
        hot_total = self.hot_minimum_total_bytes
        hot_free = self.hot_minimum_free_bytes
        if (hot_total is None) != (hot_free is None):
            raise RetentionError("hot storage capacity budget must be provided as a pair")
        if self.mode != "governance" and hot_total is not None:
            raise RetentionError("separate hot storage budgets require governance mode")
        if self.mode == "governance":
            values = (self.minimum_total_bytes, self.minimum_free_bytes, hot_total, hot_free)
            if any(value is None or value <= 0 for value in values):
                raise RetentionError("governance storage budgets must be explicit positive byte counts")
            if self.minimum_free_bytes > self.minimum_total_bytes or hot_free > hot_total:
                raise RetentionError("storage free-space budget cannot exceed total capacity")


def storage_policy_for_mode(root: Path, mode: str) -> StoragePolicy:
    if mode == "validation":
        return StoragePolicy(root, VALIDATION_MINIMUM_TOTAL_BYTES, VALIDATION_MINIMUM_FREE_BYTES, mode)
    return StoragePolicy(root, mode=mode)


def storage_policy_for_governance(
    root: Path, *, archive_minimum_total_bytes: int, archive_minimum_free_bytes: int,
    hot_minimum_total_bytes: int, hot_minimum_free_bytes: int,
) -> StoragePolicy:
    """Build an explicit offline-governance budget without weakening receiver defaults."""
    return StoragePolicy(
        root, archive_minimum_total_bytes, archive_minimum_free_bytes, "governance",
        hot_minimum_total_bytes, hot_minimum_free_bytes,
    )


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _batches(items: list[Any], size: int = 500):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def retire_raw_frame_if_unreferenced(
    connection: sqlite3.Connection, raw_id: int,
) -> bool:
    """Remove a completed receipt once no business or recovery evidence needs it."""
    blockers = (
        ("observation_batches", "raw_frame_id"),
        ("ingest_errors", "raw_frame_id"),
        ("monitoring_quality_issues", "raw_frame_id"),
        ("monitoring_normalization_retries", "raw_frame_id"),
        ("monitoring_raw_archive_frames", "raw_frame_id"),
        ("monitoring_raw_archive_part_frames", "raw_frame_id"),
        ("monitoring_archive_evidence_refs", "source_raw_frame_id"),
    )
    for table, column in blockers:
        if (_table_exists(connection, table)
                and connection.execute(
                    f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1", (raw_id,)
                ).fetchone() is not None):
            return False
    if not _table_exists(connection, "ingest_raw_frames"):
        return False
    if connection.execute(
        "SELECT 1 FROM ingest_raw_frames WHERE id=?", (raw_id,)
    ).fetchone() is None:
        return False

    if _table_exists(connection, "station_ingest_recent_receipts"):
        connection.execute(
            "DELETE FROM station_ingest_recent_receipts WHERE raw_frame_id=?", (raw_id,))
    if _table_exists(connection, "station_ingest_receipt_hourly"):
        connection.execute(
            "UPDATE station_ingest_receipt_hourly SET last_complete_raw_frame_id=NULL "
            "WHERE last_complete_raw_frame_id=?", (raw_id,))
    if _table_exists(connection, "station_ingest_error_hourly"):
        connection.execute(
            "UPDATE station_ingest_error_hourly SET last_raw_frame_id=NULL "
            "WHERE last_raw_frame_id=?", (raw_id,))
    if _table_exists(connection, "station_ingest_current_state"):
        connection.execute(
            """UPDATE station_ingest_current_state SET
                   latest_raw_frame_id=CASE WHEN latest_raw_frame_id=? THEN NULL ELSE latest_raw_frame_id END,
                   latest_authenticated_raw_frame_id=CASE WHEN latest_authenticated_raw_frame_id=? THEN NULL ELSE latest_authenticated_raw_frame_id END,
                   latest_credential_failed_raw_frame_id=CASE WHEN latest_credential_failed_raw_frame_id=? THEN NULL ELSE latest_credential_failed_raw_frame_id END
               WHERE latest_raw_frame_id=? OR latest_authenticated_raw_frame_id=?
                  OR latest_credential_failed_raw_frame_id=?""",
            (raw_id, raw_id, raw_id, raw_id, raw_id, raw_id),
        )
    if _table_exists(connection, "station_ingest_slot_summaries"):
        connection.execute(
            "DELETE FROM station_ingest_slot_summaries WHERE canonical_raw_frame_id=?", (raw_id,))
    if _table_exists(connection, "station_ingest_dedup_keys"):
        connection.execute(
            "DELETE FROM station_ingest_dedup_keys WHERE canonical_raw_frame_id=?", (raw_id,))
    if _table_exists(connection, "station_ingest_duplicate_links"):
        connection.execute(
            "DELETE FROM station_ingest_duplicate_links "
            "WHERE raw_frame_id=? OR canonical_raw_frame_id=?", (raw_id, raw_id))
    connection.execute(
        "UPDATE ingest_raw_frames SET duplicate_of_raw_frame_id=NULL "
        "WHERE duplicate_of_raw_frame_id=?", (raw_id,))
    for table in ("ingest_frame_protocols", "ingest_parse_attempts"):
        if _table_exists(connection, table):
            connection.execute(f"DELETE FROM {table} WHERE raw_frame_id=?", (raw_id,))
    connection.execute("DELETE FROM ingest_raw_frames WHERE id=?", (raw_id,))
    return True


def prune_ingest_error_samples(
    connection: sqlite3.Connection, *, now: datetime | None = None,
    batch_limit: int = 1024, deadline: float | None = None,
) -> dict[str, int | bool]:
    """Bound diagnostic samples by age, row count and retained raw bytes."""
    now = now or datetime.now(timezone.utc)
    now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    cutoff = (now - INGEST_ERROR_SAMPLE_RETENTION).replace(microsecond=0).isoformat()
    cutoff_at = datetime.fromisoformat(cutoff)
    with _statement_deadline(connection, deadline, "diagnostic_sample_inventory"):
        rows = connection.execute(
            """SELECT error.id,error.raw_frame_id,error.created_at,
                      length(error.error_type)+length(error.error_detail)
                        +COALESCE(length(raw.raw_frame),0) AS retained_bytes
               FROM ingest_errors error
               LEFT JOIN ingest_raw_frames raw ON raw.id=error.raw_frame_id
               ORDER BY error.created_at DESC,error.id DESC"""
        ).fetchall()
    delete_ids: list[int] = []
    retained_bytes = 0
    kept_rows = 0
    for row in rows:
        row_id = int(row[0])
        created_at = datetime.fromisoformat(str(row[2]).replace("Z", "+00:00"))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        else:
            created_at = created_at.astimezone(timezone.utc)
        row_bytes = int(row[3] or 0)
        keep = (
            created_at >= cutoff_at
            and kept_rows < INGEST_ERROR_SAMPLE_MAX_ROWS
            and retained_bytes + row_bytes <= INGEST_ERROR_SAMPLE_MAX_BYTES
        )
        if keep:
            kept_rows += 1
            retained_bytes += row_bytes
        elif len(delete_ids) < batch_limit:
            delete_ids.append(row_id)
    raw_ids: list[int] = []
    if delete_ids:
        marks = ",".join("?" * len(delete_ids))
        with _statement_deadline(connection, deadline, "diagnostic_sample_delete"):
            raw_ids = [int(row[0]) for row in connection.execute(
                f"SELECT DISTINCT raw_frame_id FROM ingest_errors "
                f"WHERE id IN ({marks}) AND raw_frame_id IS NOT NULL", delete_ids
            )]
            connection.execute(f"DELETE FROM ingest_errors WHERE id IN ({marks})", delete_ids)
            for raw_id in raw_ids:
                retire_raw_frame_if_unreferenced(connection, raw_id)
    summary_deleted = 0
    if _table_exists(connection, "station_ingest_error_hourly"):
        with _statement_deadline(connection, deadline, "diagnostic_summary_delete"):
            summary_ids = [tuple(row) for row in connection.execute(
                """SELECT source_key,error_type,bucket_hour
                   FROM station_ingest_error_hourly WHERE bucket_hour<?
                   ORDER BY bucket_hour,source_key,error_type LIMIT ?""",
                (cutoff, batch_limit),
            )]
            for source_key, error_type, bucket_hour in summary_ids:
                connection.execute(
                    "DELETE FROM station_ingest_error_hourly "
                    "WHERE source_key=? AND error_type=? AND bucket_hour=?",
                    (source_key, error_type, bucket_hour),
                )
            summary_deleted = len(summary_ids)
    return {
        "samples_deleted": len(delete_ids),
        "summaries_deleted": summary_deleted,
        "retained_rows": kept_rows,
        "retained_bytes": retained_bytes,
        "partial": len(delete_ids) >= batch_limit or summary_deleted >= batch_limit,
    }


def _deadline_expired(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


@contextmanager
def _statement_deadline(
    connection: sqlite3.Connection, deadline: float | None, stage: str,
):
    if deadline is None:
        yield
        return
    if _deadline_expired(deadline):
        raise _StatementDeadlineExceeded(stage)
    interrupted_by_deadline = False

    def interrupt_when_expired() -> int:
        nonlocal interrupted_by_deadline
        if _deadline_expired(deadline):
            interrupted_by_deadline = True
            return 1
        return 0

    connection.set_progress_handler(interrupt_when_expired, 1000)
    try:
        yield
    except sqlite3.OperationalError as exc:
        if interrupted_by_deadline and "interrupted" in str(exc).lower():
            raise _StatementDeadlineExceeded(stage) from exc
        raise
    finally:
        connection.set_progress_handler(None, 0)


def _begin_immediate_with_deadline(
    connection: sqlite3.Connection, deadline: float | None, stage: str,
) -> None:
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _StatementDeadlineExceeded(stage)
        connection.execute(f"PRAGMA busy_timeout={max(1, min(10000, int(remaining * 1000)))}")
    try:
        connection.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if deadline is not None and _deadline_expired(deadline):
            raise _StatementDeadlineExceeded(stage) from exc
        raise


def _archive_read_path(
    local_path: str | Path, mirror_path: str | Path | None, mirror_verified_at: str | None,
) -> Path:
    local = Path(local_path)
    if local.is_file():
        return local
    if mirror_verified_at and mirror_path and Path(mirror_path).is_file():
        return Path(mirror_path)
    raise RetentionError("archived evidence storage is unavailable; retry after storage recovery")


def _mirror_archive_path(local_path: str | Path, local_root: Path, mirror_root: Path) -> Path:
    try:
        relative = Path(local_path).resolve().relative_to(Path(local_root).resolve())
    except ValueError as exc:
        raise RetentionError("archive path is outside the configured local storage root") from exc
    target = Path(mirror_root) / relative
    if target.resolve() == Path(local_path).resolve():
        raise RetentionError("mirror archive path must differ from local archive path")
    return target


def _verify_archive_copy(path: Path, expected_digest: str, expected_count: int) -> None:
    digest = hashlib.sha256()
    count = 0
    try:
        with gzip.open(path, "rb") as stream:
            for line in stream:
                digest.update(line)
                count += 1
    except OSError as exc:
        raise RetentionError("mirror archive cannot be read") from exc
    if digest.hexdigest() != expected_digest or count != expected_count:
        raise RetentionError("mirror archive content verification failed")


def _read_archived_payload(
    archive_path: Path, record_index: int, record_sha256: str, frame_sha256: str,
    content_sha256: str, *, content_verified: bool,
) -> bytes:
    selected = None
    content_digest = hashlib.sha256()
    try:
        with gzip.open(archive_path, "rb") as stream:
            for index, line in enumerate(stream):
                content_digest.update(line)
                if index == record_index:
                    selected = line
                    if content_verified:
                        break
        if not content_verified and content_digest.hexdigest() != content_sha256:
            raise RetentionError("archived raw evidence part digest mismatch")
        if selected is None or hashlib.sha256(selected).hexdigest() != record_sha256:
            raise RetentionError("archived raw evidence record is unavailable")
        item = json.loads(selected.decode("ascii"))
        payload = base64.b64decode(item["frame_b64"], validate=True)
    except RetentionError:
        raise
    except (OSError, ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetentionError("archived raw evidence cannot be read; retry after storage recovery") from exc
    if hashlib.sha256(payload).hexdigest() != frame_sha256:
        raise RetentionError("archived raw evidence frame digest mismatch")
    return payload


def _record_health(connection: sqlite3.Connection, status: str, detail: str) -> None:
    connection.execute(
        """INSERT INTO monitoring_storage_health(storage_key,status,detail,checked_at) VALUES ('raw_archive',?,?,?)
           ON CONFLICT(storage_key) DO UPDATE SET status=excluded.status,detail=excluded.detail,checked_at=excluded.checked_at""",
        (status, detail[:160], _now()),
    )


def _require_storage(policy: StoragePolicy, database: Path | None = None) -> Path:
    root = Path(policy.root)
    if not root.exists() or not root.is_dir():
        raise RetentionError("archive storage root must be an existing directory")
    checked_roots = [(
        "archive storage", root, policy.minimum_total_bytes, policy.minimum_free_bytes,
    )]
    if database is not None:
        database_root = Path(database).parent
        if database_root.resolve() != root.resolve():
            checked_roots.append((
                "SQLite hot storage", database_root,
                (policy.hot_minimum_total_bytes
                 if policy.hot_minimum_total_bytes is not None else policy.minimum_total_bytes),
                (policy.hot_minimum_free_bytes
                 if policy.hot_minimum_free_bytes is not None else policy.minimum_free_bytes),
            ))
    for label, checked_root, minimum_total, minimum_free in checked_roots:
        if not checked_root.exists() or not checked_root.is_dir():
            raise RetentionError(f"{label} root must be an existing directory")
        usage = shutil.disk_usage(checked_root)
        if usage.total < minimum_total:
            raise RetentionError(f"{label} does not meet the minimum capacity")
        if usage.free < minimum_free:
            raise RetentionError(f"{label} is below the free-space protection threshold")
    try:
        descriptor, probe = tempfile.mkstemp(prefix=".station-ingest-write-", dir=root)
        os.close(descriptor)
        Path(probe).unlink()
    except OSError as exc:
        raise RetentionError("archive storage root is not writable") from exc
    return root


def check_storage_policy(database: Path, policy: StoragePolicy) -> None:
    """Persist capacity and writable-directory state before an ACK may be sent."""
    try:
        _require_storage(policy, database)
    except RetentionError:
        with closing(sqlite3.connect(str(database), timeout=5, isolation_level=None)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            _record_health(connection, "degraded", "archive storage unavailable")
            connection.commit()
        raise
    detail = "validation storage available; not long-term production ready" if policy.mode == "validation" else "long-term archive storage available"
    with closing(sqlite3.connect(str(database), timeout=5, isolation_level=None)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _record_health(connection, "healthy", detail)
        connection.commit()


def aggregate_hourly(database: Path, day: str) -> int:
    """Idempotently aggregate published observations into their complete value series."""
    try:
        start_day = date.fromisoformat(day)
    except ValueError as exc:
        raise RetentionError("archive day must use YYYY-MM-DD") from exc
    end_day = start_day + timedelta(days=1)
    with closing(sqlite3.connect(str(database), timeout=5, isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """SELECT b.endpoint_id,b.business_site_id,v.protocol_code,v.business_metric,COALESCE(v.instrument_asset_code,'') AS instrument_asset_code,
                      substr(b.observed_at,1,13) || ':00:00+00:00' AS observed_hour,
                      COUNT(*) AS sample_count,MIN(v.standard_value) AS minimum_value,
                      MAX(v.standard_value) AS maximum_value,AVG(v.standard_value) AS average_value,
                      v.standard_unit,MAX(b.id) AS source_last_batch_id
               FROM observation_values v JOIN observation_batches b ON b.id=v.observation_batch_id
               WHERE b.is_current=1 AND v.is_current=1 AND v.is_published=1
                 AND v.quality IN ('valid','suspect') AND b.observed_at>=? AND b.observed_at<?
               GROUP BY b.endpoint_id,b.business_site_id,v.protocol_code,v.business_metric,v.instrument_asset_code,
                        observed_hour,v.standard_unit""",
            (f"{start_day.isoformat()}T00:00:00+00:00", f"{end_day.isoformat()}T00:00:00+00:00"),
        ).fetchall()
        for row in rows:
            connection.execute(
                """INSERT INTO monitoring_hourly_value_series(endpoint_id,business_site_id,protocol_code,business_metric,
                   instrument_asset_code,observed_hour,sample_count,minimum_value,maximum_value,average_value,
                   standard_unit,source_last_batch_id,aggregated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(endpoint_id,protocol_code,business_metric,instrument_asset_code,standard_unit,observed_hour)
                   DO UPDATE SET sample_count=excluded.sample_count,minimum_value=excluded.minimum_value,
                     maximum_value=excluded.maximum_value,average_value=excluded.average_value,
                     source_last_batch_id=excluded.source_last_batch_id,aggregated_at=excluded.aggregated_at""",
                (*tuple(row), _now()),
            )
        connection.commit()
        return len(rows)


def _archive_item(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "endpoint_id": row["endpoint_id"],
        "station_code": row["station_code"],
        "received_at": row["received_at"],
        "sha256": row["frame_sha256"],
        "logical_key_sha256": row["logical_key_sha256"],
        "body_length": row["body_length"],
        "crc_status": row["crc_status"],
        "authentication_status": row["authentication_status"],
        "disposition": row["disposition"],
        "duplicate_of_raw_frame_id": row["duplicate_of_raw_frame_id"],
        "persistence_state": row["persistence_state"],
        "protocol_family": row["protocol_family"] or "unknown",
        "parser_version": row["parser_version"] or "unknown",
        "frame_b64": base64.b64encode(row["raw_frame"]).decode("ascii"),
    }


def _archive_records(
    rows: list[sqlite3.Row], *, deadline: float | None = None,
) -> tuple[list[bytes], str]:
    lines = []
    digest = hashlib.sha256()
    for row in rows:
        if _deadline_expired(deadline):
            raise _StatementDeadlineExceeded("raw_archive_encode")
        item = _archive_item(row)
        line = (json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
        lines.append(line)
        digest.update(line)
    return lines, digest.hexdigest()


def _write_and_verify_archive(
    path: Path, lines: list[bytes], expected_digest: str, rows: list[sqlite3.Row], *,
    deadline: float | None = None,
) -> None:
    """Verify decompressed IDs, hashes, payload bytes, and the content-lines digest."""
    if not path.exists():
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            with gzip.open(temporary, "wb") as stream:
                for line in lines:
                    if _deadline_expired(deadline):
                        raise _StatementDeadlineExceeded("raw_archive_write")
                    stream.write(line)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    digest = hashlib.sha256()
    actual: list[tuple[int, str, bytes]] = []
    try:
        with gzip.open(path, "rb") as stream:
            for line in stream:
                if _deadline_expired(deadline):
                    raise _StatementDeadlineExceeded("raw_archive_verify")
                digest.update(line)
                item = json.loads(line.decode("ascii"))
                actual.append((int(item["id"]), str(item["sha256"]), base64.b64decode(item["frame_b64"], validate=True)))
    except _StatementDeadlineExceeded:
        raise
    except (OSError, ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetentionError("raw archive file cannot be verified") from exc
    expected = [(int(row["id"]), str(row["frame_sha256"]), bytes(row["raw_frame"])) for row in rows]
    if digest.hexdigest() != expected_digest or actual != expected:
        raise RetentionError("raw archive content verification failed")


def archive_raw_day(
    database: Path, day: str, policy: StoragePolicy, *, batch_limit: int | None = None,
    deadline: float | None = None,
) -> int:
    """Append immutable archive parts, preserving late receipts and retry safety."""
    try:
        start_day = date.fromisoformat(day)
    except ValueError as exc:
        raise RetentionError("archive day must use YYYY-MM-DD") from exc
    end_day = start_day + timedelta(days=1)
    if batch_limit is not None and batch_limit <= 0:
        raise RetentionError("archive batch limit must be positive")
    try:
        # Archiving releases hot-database bytes; a low hot-volume threshold must not
        # prevent writing verified evidence to a healthy archive volume.
        root = _require_storage(policy)
        with closing(sqlite3.connect(str(database), timeout=5, isolation_level=None)) as connection:
            connection.row_factory = sqlite3.Row
            query = """SELECT raw.*,protocol.protocol_family,protocol.parser_version
                   FROM ingest_raw_frames raw
                   LEFT JOIN ingest_frame_protocols protocol ON protocol.raw_frame_id=raw.id
                   LEFT JOIN monitoring_archive_evidence_refs evidence ON evidence.source_raw_frame_id=raw.id
                   WHERE raw.received_at>=? AND raw.received_at<? AND raw.persistence_state='persisted'
                      AND length(raw.raw_frame)>0 AND evidence.source_raw_frame_id IS NULL ORDER BY raw.id"""
            parameters: list[Any] = [
                f"{start_day.isoformat()}T00:00:00+00:00",
                f"{end_day.isoformat()}T00:00:00+00:00",
            ]
            if batch_limit is not None:
                query += " LIMIT ?"
                parameters.append(batch_limit)
            with _statement_deadline(connection, deadline, "raw_archive_select"):
                rows = connection.execute(query, parameters).fetchall()
        if not rows:
            return 0
        lines, content_digest = _archive_records(rows, deadline=deadline)
        archive_dir = root / "raw-archives"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"{day}-{rows[0]['id']}-{rows[-1]['id']}-{content_digest[:16]}.jsonl.gz"
        _write_and_verify_archive(
            archive_path, lines, content_digest, rows, deadline=deadline)
        with closing(sqlite3.connect(str(database), timeout=5, isolation_level=None)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            with _statement_deadline(connection, deadline, "raw_archive_lock"):
                connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """INSERT OR IGNORE INTO monitoring_raw_archive_parts
                   (archive_date,archive_path,content_sha256,frame_count,created_at) VALUES (?,?,?,?,?)""",
                (day, str(archive_path), content_digest, len(rows), _now()),
            )
            archive_id = int(cursor.lastrowid) if cursor.lastrowid else int(connection.execute(
                "SELECT id FROM monitoring_raw_archive_parts WHERE archive_path=?", (str(archive_path),)
            ).fetchone()[0])
            connection.execute(
                """INSERT OR REPLACE INTO monitoring_raw_archive_part_metadata(
                       archive_part_id,format_version,verified_at,first_raw_frame_id,last_raw_frame_id,
                       first_received_at,last_received_at,record_count,mirror_path,mirror_verified_at,
                       local_deleted_at)
                   VALUES (?,?,?,?,?,?,?,?,
                       (SELECT mirror_path FROM monitoring_raw_archive_part_metadata WHERE archive_part_id=?),
                       (SELECT mirror_verified_at FROM monitoring_raw_archive_part_metadata WHERE archive_part_id=?),
                       (SELECT local_deleted_at FROM monitoring_raw_archive_part_metadata WHERE archive_part_id=?))""",
                (archive_id, RAW_ARCHIVE_FORMAT_VERSION, _now(), rows[0]["id"], rows[-1]["id"],
                 rows[0]["received_at"], rows[-1]["received_at"], len(rows),
                 archive_id, archive_id, archive_id),
            )
            connection.executemany(
                """INSERT OR IGNORE INTO monitoring_raw_archive_part_frames(
                       raw_frame_id,archive_part_id,frame_sha256) VALUES (?,?,?)""",
                ((row["id"], archive_id, row["frame_sha256"]) for row in rows),
            )
            for batch in _batches(rows):
                identifiers = [int(row["id"]) for row in batch]
                marks = ",".join("?" * len(identifiers))
                linked = {int(item[0]): (int(item[1]), str(item[2])) for item in connection.execute(
                    f"""SELECT raw_frame_id,archive_part_id,frame_sha256
                        FROM monitoring_raw_archive_part_frames WHERE raw_frame_id IN ({marks})""",
                    identifiers,
                )}
                if any(linked.get(int(row["id"])) != (archive_id, str(row["frame_sha256"]))
                       for row in batch):
                    raise RetentionError("raw archive index conflicts with existing evidence")
            evidence_rows = []
            evidence_created_at = _now()
            for record_index, row in enumerate(rows):
                item = _archive_item(row)
                context = dict(item)
                context.pop("frame_b64")
                record_sha256 = hashlib.sha256(lines[record_index]).hexdigest()
                evidence_rows.append((
                    row["id"], archive_id, record_index, record_sha256, row["frame_sha256"],
                    row["received_at"], row["endpoint_id"], row["station_code"],
                    row["protocol_family"] or "unknown", row["parser_version"] or "unknown",
                    row["logical_key_sha256"], row["crc_status"], row["authentication_status"],
                    row["disposition"], row["body_length"],
                    json.dumps(context, sort_keys=True, separators=(",", ":")), evidence_created_at,
                ))
            connection.executemany(
                """INSERT INTO monitoring_archive_evidence_refs(
                       source_raw_frame_id,archive_part_id,archive_record_index,record_sha256,
                       frame_sha256,received_at,endpoint_id,station_code,protocol_family,parser_version,
                       logical_key_sha256,crc_status,authentication_status,disposition,body_length,
                       context_json,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source_raw_frame_id) DO UPDATE SET
                       archive_part_id=excluded.archive_part_id,
                       archive_record_index=excluded.archive_record_index,
                       record_sha256=excluded.record_sha256,
                       frame_sha256=excluded.frame_sha256,
                       context_json=excluded.context_json""",
                evidence_rows,
            )
            updated_at = _now()
            for batch in _batches(rows):
                identifiers = [int(row["id"]) for row in batch]
                marks = ",".join("?" * len(identifiers))
                connection.execute(
                    f"""UPDATE station_ingest_dedup_keys
                       SET canonical_evidence_ref_id=(SELECT id FROM monitoring_archive_evidence_refs
                                                     WHERE source_raw_frame_id=canonical_raw_frame_id),
                           updated_at=?
                       WHERE canonical_raw_frame_id IN ({marks})""",
                    (updated_at, *identifiers),
                )
                connection.execute(
                    f"UPDATE ingest_raw_frames SET raw_frame=X'' WHERE id IN ({marks})",
                    identifiers,
                )
            _record_health(connection, "healthy", "archive completed")
            connection.commit()
        return len(rows)
    except Exception as exc:
        with closing(sqlite3.connect(str(database), timeout=5, isolation_level=None)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            _record_health(connection, "degraded", "archive unavailable")
            connection.commit()
        if isinstance(exc, (RetentionError, _StatementDeadlineExceeded)):
            raise
        raise RetentionError("raw archive could not be completed") from exc


def archive_oldest_raw_day(
    database: Path, before_day: str, policy: StoragePolicy, *, batch_limit: int | None = None,
) -> dict[str, Any]:
    """Archive the oldest unarchived raw day before the exclusive UTC boundary."""
    try:
        boundary = date.fromisoformat(before_day)
    except ValueError as exc:
        raise RetentionError("archive boundary must use YYYY-MM-DD") from exc
    with closing(sqlite3.connect(str(database))) as connection:
        row = connection.execute(
            """SELECT substr(raw.received_at,1,10)
               FROM ingest_raw_frames raw
               LEFT JOIN monitoring_archive_evidence_refs evidence
                 ON evidence.source_raw_frame_id=raw.id
               WHERE raw.received_at<? AND raw.persistence_state='persisted'
                 AND length(raw.raw_frame)>0 AND evidence.source_raw_frame_id IS NULL
               ORDER BY raw.received_at,raw.id LIMIT 1""",
            (f"{boundary.isoformat()}T00:00:00+00:00",),
        ).fetchone()
    if row is None:
        return {"day": None, "processed": 0, "retry_required": False}
    day = str(row[0])
    processed = archive_raw_day(database, day, policy, batch_limit=batch_limit)
    return {
        "day": day,
        "processed": processed,
        "retry_required": batch_limit is not None and processed >= batch_limit,
    }


def read_raw_evidence(connection: sqlite3.Connection, raw_id: int) -> bytes:
    """Return online or archived raw bytes, failing explicitly when evidence is unavailable."""
    online = connection.execute(
        "SELECT raw_frame,frame_sha256 FROM ingest_raw_frames WHERE id=?", (raw_id,)
    ).fetchone()
    if online is not None and bytes(online[0] or b""):
        payload = bytes(online[0])
        if hashlib.sha256(payload).hexdigest() != str(online[1]):
            raise RetentionError("online raw evidence digest mismatch")
        return payload
    reference = connection.execute(
        """SELECT evidence.archive_record_index,evidence.record_sha256,evidence.frame_sha256,
                  part.archive_path,part.content_sha256,metadata.verified_at,
                  metadata.mirror_path,metadata.mirror_verified_at
           FROM monitoring_archive_evidence_refs evidence
           JOIN monitoring_raw_archive_parts part ON part.id=evidence.archive_part_id
           LEFT JOIN monitoring_raw_archive_part_metadata metadata
             ON metadata.archive_part_id=part.id
           WHERE evidence.source_raw_frame_id=?""",
        (raw_id,),
    ).fetchone()
    if reference is None:
        raise RetentionError("archived raw evidence reference is unavailable; retry after archive repair")
    archive_path = _archive_read_path(reference[3], reference[6], reference[7])
    return _read_archived_payload(
        archive_path, int(reference[0]), str(reference[1]), str(reference[2]),
        str(reference[4]), content_verified=bool(reference[5]),
    )


def backfill_archive_evidence_refs(database: Path, *, part_limit: int | None = None,
                                   part_id: int | None = None) -> int:
    """Verify legacy archive parts and add recoverable per-record references."""
    if part_limit is not None and part_limit <= 0:
        raise RetentionError("archive backfill part limit must be positive")
    if part_id is not None and (type(part_id) is not int or part_id <= 0):
        raise RetentionError("archive backfill part id must be a positive integer")
    with closing(sqlite3.connect(str(database), timeout=30)) as connection:
        connection.row_factory = sqlite3.Row
        if part_id is not None and connection.execute(
                "SELECT 1 FROM monitoring_raw_archive_parts WHERE id=?", (part_id,)
        ).fetchone() is None:
            raise RetentionError("archive backfill target part does not exist")
        query = """SELECT part.*,metadata.mirror_path,metadata.mirror_verified_at
               FROM monitoring_raw_archive_parts part
               LEFT JOIN monitoring_raw_archive_part_metadata metadata
                 ON metadata.archive_part_id=part.id
               WHERE (NOT EXISTS (
                   SELECT 1 FROM monitoring_raw_archive_part_metadata metadata
                   WHERE metadata.archive_part_id=part.id)
                  OR EXISTS (
                   SELECT 1 FROM monitoring_raw_archive_part_frames frame
                   LEFT JOIN monitoring_archive_evidence_refs evidence
                     ON evidence.source_raw_frame_id=frame.raw_frame_id
                   WHERE frame.archive_part_id=part.id AND evidence.id IS NULL))"""
        parameters: tuple[int, ...] = ()
        if part_id is not None:
            query += " AND part.id=?"
            parameters = (part_id,)
        query += " ORDER BY part.id"
        if part_limit is not None:
            query += " LIMIT ?"
            parameters += (part_limit,)
        parts = connection.execute(query, parameters).fetchall()
    inserted = 0
    for part in parts:
        records: dict[int, tuple[int, bytes, dict[str, Any]]] = {}
        content_digest = hashlib.sha256()
        try:
            archive_path = _archive_read_path(
                part["archive_path"], part["mirror_path"], part["mirror_verified_at"])
            with gzip.open(archive_path, "rb") as stream:
                for record_index, line in enumerate(stream):
                    content_digest.update(line)
                    item = json.loads(line.decode("ascii"))
                    raw_id = int(item["id"])
                    if raw_id in records:
                        raise RetentionError("raw archive part contains duplicate record identities")
                    records[raw_id] = (record_index, line, item)
        except RetentionError:
            raise
        except (OSError, ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RetentionError("legacy raw archive part cannot be read") from exc
        if (content_digest.hexdigest() != str(part["content_sha256"])
                or len(records) != int(part["frame_count"])):
            raise RetentionError("legacy raw archive part verification failed")
        raw_ids = sorted(records)
        if not raw_ids:
            raise RetentionError("raw archive part is empty")
        with closing(sqlite3.connect(str(database), timeout=30)) as connection:
            connection.row_factory = sqlite3.Row
            rows = []
            for raw_batch in _batches(raw_ids):
                marks = ",".join("?" * len(raw_batch))
                rows.extend(connection.execute(
                    f"""SELECT raw.*,protocol.protocol_family,protocol.parser_version,
                            frame.archive_part_id,frame.frame_sha256 AS archived_frame_sha256
                     FROM ingest_raw_frames raw
                     JOIN monitoring_raw_archive_part_frames frame ON frame.raw_frame_id=raw.id
                     LEFT JOIN ingest_frame_protocols protocol ON protocol.raw_frame_id=raw.id
                     WHERE raw.id IN ({marks})""",
                    raw_batch,
                ).fetchall())
        by_id = {int(row["id"]): row for row in rows}
        if set(by_id) != set(raw_ids):
            raise RetentionError("legacy raw archive context is incomplete")
        prepared = []
        format_version = RAW_ARCHIVE_FORMAT_VERSION
        for raw_id in raw_ids:
            record_index, line, item = records[raw_id]
            row = by_id[raw_id]
            if int(row["archive_part_id"]) != int(part["id"]):
                raise RetentionError("legacy raw archive mapping conflicts with its part")
            payload = base64.b64decode(item["frame_b64"], validate=True)
            frame_sha256 = hashlib.sha256(payload).hexdigest()
            if (frame_sha256 != str(item["sha256"])
                    or frame_sha256 != str(row["frame_sha256"])
                    or frame_sha256 != str(row["archived_frame_sha256"])):
                raise RetentionError("legacy raw archive frame digest mismatch")
            if "protocol_family" not in item:
                format_version = 1
            context = {
                "id": raw_id,
                "endpoint_id": row["endpoint_id"],
                "station_code": row["station_code"],
                "received_at": row["received_at"],
                "sha256": row["frame_sha256"],
                "logical_key_sha256": row["logical_key_sha256"],
                "body_length": row["body_length"],
                "crc_status": row["crc_status"],
                "authentication_status": row["authentication_status"],
                "disposition": row["disposition"],
                "duplicate_of_raw_frame_id": row["duplicate_of_raw_frame_id"],
                "persistence_state": row["persistence_state"],
                "protocol_family": row["protocol_family"] or "unknown",
                "parser_version": row["parser_version"] or "unknown",
            }
            prepared.append((
                raw_id, int(part["id"]), record_index, hashlib.sha256(line).hexdigest(),
                frame_sha256, row["received_at"], row["endpoint_id"], row["station_code"],
                context["protocol_family"], context["parser_version"], row["logical_key_sha256"],
                row["crc_status"], row["authentication_status"], row["disposition"],
                row["body_length"], json.dumps(context, sort_keys=True, separators=(",", ":")), _now(),
            ))
        ordered_rows = sorted(by_id.values(), key=lambda row: (str(row["received_at"]), int(row["id"])))
        with closing(sqlite3.connect(str(database), timeout=30, isolation_level=None)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT OR REPLACE INTO monitoring_raw_archive_part_metadata(
                       archive_part_id,format_version,verified_at,first_raw_frame_id,last_raw_frame_id,
                       first_received_at,last_received_at,record_count,mirror_path,mirror_verified_at,
                       local_deleted_at)
                   VALUES (?,?,?,?,?,?,?,?,
                       (SELECT mirror_path FROM monitoring_raw_archive_part_metadata WHERE archive_part_id=?),
                       (SELECT mirror_verified_at FROM monitoring_raw_archive_part_metadata WHERE archive_part_id=?),
                       (SELECT local_deleted_at FROM monitoring_raw_archive_part_metadata WHERE archive_part_id=?))""",
                (part["id"], format_version, _now(), min(raw_ids), max(raw_ids),
                 ordered_rows[0]["received_at"], ordered_rows[-1]["received_at"],
                 len(prepared), part["id"], part["id"], part["id"]),
            )
            before = connection.total_changes
            connection.executemany(
                """INSERT OR IGNORE INTO monitoring_archive_evidence_refs(
                       source_raw_frame_id,archive_part_id,archive_record_index,record_sha256,
                       frame_sha256,received_at,endpoint_id,station_code,protocol_family,parser_version,
                       logical_key_sha256,crc_status,authentication_status,disposition,body_length,
                       context_json,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                prepared,
            )
            inserted += connection.total_changes - before
            connection.execute(
                """UPDATE station_ingest_dedup_keys
                   SET canonical_evidence_ref_id=(
                       SELECT evidence.id FROM monitoring_archive_evidence_refs evidence
                       WHERE evidence.source_raw_frame_id=station_ingest_dedup_keys.canonical_raw_frame_id),
                       updated_at=?
                   WHERE canonical_evidence_ref_id IS NULL AND canonical_raw_frame_id IN (
                       SELECT source_raw_frame_id FROM monitoring_archive_evidence_refs
                       WHERE archive_part_id=?)""",
                (_now(), part["id"]),
            )
            connection.commit()
    return inserted


def _source_key(endpoint_id: int | None, station_code: str | None) -> str:
    if endpoint_id is not None:
        return f"endpoint:{endpoint_id}"
    if station_code:
        return f"station:{station_code}"
    return "transport:unidentified"


def _cutoff(now: datetime, delta: timedelta) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now.astimezone(timezone.utc) - delta).replace(microsecond=0).isoformat()


def _upsert_current_state(
    connection: sqlite3.Connection, source_key: str, endpoint_id: int | None,
    station_code: str | None, latest, authenticated, credential_failed,
) -> None:
    connection.execute(
        """INSERT INTO station_ingest_current_state(
               source_key,endpoint_id,station_code,latest_received_at,latest_raw_frame_id,
               latest_authenticated_at,latest_authenticated_raw_frame_id,
               latest_credential_failed_at,latest_credential_failed_raw_frame_id,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(source_key) DO UPDATE SET
               endpoint_id=COALESCE(excluded.endpoint_id,station_ingest_current_state.endpoint_id),
               station_code=COALESCE(excluded.station_code,station_ingest_current_state.station_code),
               latest_raw_frame_id=CASE
                   WHEN excluded.latest_received_at>=COALESCE(station_ingest_current_state.latest_received_at,'')
                   THEN excluded.latest_raw_frame_id ELSE station_ingest_current_state.latest_raw_frame_id END,
               latest_received_at=NULLIF(MAX(
                   COALESCE(station_ingest_current_state.latest_received_at,''),
                   COALESCE(excluded.latest_received_at,'')),''),
               latest_authenticated_raw_frame_id=CASE
                   WHEN excluded.latest_authenticated_at>=COALESCE(station_ingest_current_state.latest_authenticated_at,'')
                   THEN excluded.latest_authenticated_raw_frame_id
                   ELSE station_ingest_current_state.latest_authenticated_raw_frame_id END,
               latest_authenticated_at=NULLIF(MAX(
                   COALESCE(station_ingest_current_state.latest_authenticated_at,''),
                   COALESCE(excluded.latest_authenticated_at,'')),''),
               latest_credential_failed_raw_frame_id=CASE
                   WHEN excluded.latest_credential_failed_at>=COALESCE(station_ingest_current_state.latest_credential_failed_at,'')
                   THEN excluded.latest_credential_failed_raw_frame_id
                   ELSE station_ingest_current_state.latest_credential_failed_raw_frame_id END,
               latest_credential_failed_at=NULLIF(MAX(
                   COALESCE(station_ingest_current_state.latest_credential_failed_at,''),
                   COALESCE(excluded.latest_credential_failed_at,'')),''),
               updated_at=excluded.updated_at""",
        (
            source_key, endpoint_id, station_code,
            latest["received_at"] if latest else None, latest["id"] if latest else None,
            authenticated["received_at"] if authenticated else None,
            authenticated["id"] if authenticated else None,
            credential_failed["received_at"] if credential_failed else None,
            credential_failed["id"] if credential_failed else None, _now(),
        ),
    )


def _prepare_current_state(database: Path, *, recent_cutoff: str) -> int:
    with closing(sqlite3.connect(str(database), timeout=30, isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        endpoint_rows = connection.execute(
            """SELECT endpoint.id,endpoint.station_code FROM trusted_endpoints endpoint
               WHERE EXISTS (
                   SELECT 1 FROM ingest_raw_frames raw
                        INDEXED BY idx_ingest_raw_endpoint_received
                   WHERE raw.endpoint_id=endpoint.id)"""
        ).fetchall()
        for endpoint in endpoint_rows:
            endpoint_id = int(endpoint["id"])
            latest = connection.execute(
                """SELECT id,received_at FROM ingest_raw_frames
                        INDEXED BY idx_ingest_raw_endpoint_received
                   WHERE endpoint_id=? ORDER BY received_at DESC,id DESC LIMIT 1""",
                (endpoint_id,),
            ).fetchone()
            authenticated = connection.execute(
                """SELECT id,received_at FROM ingest_raw_frames
                        INDEXED BY idx_ingest_raw_authenticated_endpoint_received
                   WHERE endpoint_id=? AND authentication_status='authenticated'
                   ORDER BY received_at DESC,id DESC LIMIT 1""",
                (endpoint_id,),
            ).fetchone()
            credential_failed = connection.execute(
                """SELECT id,received_at FROM ingest_raw_frames
                        INDEXED BY idx_ingest_raw_station_auth_received
                   WHERE station_code=? AND authentication_status='credential_failed'
                   ORDER BY received_at DESC,id DESC LIMIT 1""",
                (endpoint["station_code"],),
            ).fetchone()
            _upsert_current_state(
                connection, f"endpoint:{endpoint_id}", endpoint_id,
                endpoint["station_code"], latest, authenticated, credential_failed,
            )
        recent_unknown = connection.execute(
            """WITH ranked AS (
                   SELECT id,station_code,received_at,authentication_status,
                          CASE WHEN station_code IS NOT NULL AND station_code<>''
                               THEN 'station:' || station_code ELSE 'transport:unidentified' END AS source_key,
                          ROW_NUMBER() OVER (
                              PARTITION BY CASE WHEN station_code IS NOT NULL AND station_code<>''
                                                THEN 'station:' || station_code ELSE 'transport:unidentified' END
                              ORDER BY received_at DESC,id DESC) AS latest_rank,
                          ROW_NUMBER() OVER (
                              PARTITION BY CASE WHEN station_code IS NOT NULL AND station_code<>''
                                                THEN 'station:' || station_code ELSE 'transport:unidentified' END,
                                           authentication_status
                              ORDER BY received_at DESC,id DESC) AS status_rank
                   FROM ingest_raw_frames INDEXED BY idx_ingest_raw_received
                   WHERE endpoint_id IS NULL AND received_at>=?)
               SELECT * FROM ranked WHERE latest_rank=1 OR status_rank=1""",
            (recent_cutoff,),
        ).fetchall()
        unknown_by_source: dict[str, dict[str, sqlite3.Row]] = {}
        for row in recent_unknown:
            facts = unknown_by_source.setdefault(row["source_key"], {})
            if row["latest_rank"] == 1:
                facts["latest"] = row
            facts[str(row["authentication_status"])] = row
        for source_key, facts in unknown_by_source.items():
            _upsert_current_state(
                connection, source_key, None, facts["latest"]["station_code"],
                facts.get("latest"), facts.get("authenticated"), facts.get("credential_failed"),
            )
        connection.execute(
            """WITH ranked_open_errors AS (
                   SELECT error.error_type,error.created_at,raw.endpoint_id,raw.station_code,
                          CASE WHEN raw.endpoint_id IS NOT NULL THEN 'endpoint:' || raw.endpoint_id
                               WHEN raw.station_code IS NOT NULL AND raw.station_code<>''
                                   THEN 'station:' || raw.station_code
                               ELSE 'transport:unidentified' END AS source_key,
                          ROW_NUMBER() OVER (
                              PARTITION BY CASE
                                  WHEN raw.endpoint_id IS NOT NULL THEN 'endpoint:' || raw.endpoint_id
                                  WHEN raw.station_code IS NOT NULL AND raw.station_code<>''
                                      THEN 'station:' || raw.station_code
                                  ELSE 'transport:unidentified' END
                              ORDER BY error.created_at DESC,error.id DESC) AS source_rank
                   FROM ingest_errors error
                   LEFT JOIN ingest_raw_frames raw ON raw.id=error.raw_frame_id
                   WHERE error.status='open')
               INSERT INTO station_ingest_current_state(
                   source_key,endpoint_id,station_code,latest_error_at,latest_error_type,updated_at)
               SELECT source_key,endpoint_id,station_code,created_at,error_type,?
               FROM ranked_open_errors WHERE source_rank=1
               ON CONFLICT(source_key) DO UPDATE SET
                   latest_error_at=CASE
                       WHEN excluded.latest_error_at>=COALESCE(station_ingest_current_state.latest_error_at,'')
                       THEN excluded.latest_error_at ELSE station_ingest_current_state.latest_error_at END,
                   latest_error_type=CASE
                       WHEN excluded.latest_error_at>=COALESCE(station_ingest_current_state.latest_error_at,'')
                       THEN excluded.latest_error_type ELSE station_ingest_current_state.latest_error_type END,
                   updated_at=excluded.updated_at""",
            (_now(),),
        )
        connection.commit()
        return len(endpoint_rows) + len(unknown_by_source)


def _prepare_consumer_windows(database: Path, *, cutoff: str) -> tuple[int, int]:
    with closing(sqlite3.connect(str(database), timeout=30, isolation_level=None)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        cutoff_hour = datetime.fromisoformat(cutoff).replace(
            minute=0, second=0, microsecond=0).isoformat()
        connection.execute(
            """INSERT INTO station_ingest_receipt_hourly(
                   source_key,endpoint_id,station_code,authentication_status,bucket_hour,
                   receipt_count,first_received_at,last_received_at,last_complete_raw_frame_id,updated_at)
               SELECT CASE WHEN endpoint_id IS NOT NULL THEN 'endpoint:' || endpoint_id
                           WHEN station_code IS NOT NULL AND station_code<>'' THEN 'station:' || station_code
                           ELSE 'transport:unidentified' END,
                      endpoint_id,station_code,authentication_status,
                      strftime('%Y-%m-%dT%H:00:00+00:00',received_at),
                      COUNT(*),MIN(received_at),MAX(received_at),MAX(id),?
               FROM ingest_raw_frames raw INDEXED BY idx_ingest_raw_received
               WHERE received_at>=?
                 AND NOT EXISTS (
                     SELECT 1 FROM station_ingest_recent_receipts recent
                     WHERE recent.raw_frame_id=raw.id)
               GROUP BY 1,endpoint_id,station_code,authentication_status,5
               ON CONFLICT(source_key,authentication_status,bucket_hour) DO UPDATE SET
                   endpoint_id=COALESCE(excluded.endpoint_id,station_ingest_receipt_hourly.endpoint_id),
                   station_code=COALESCE(excluded.station_code,station_ingest_receipt_hourly.station_code),
                   receipt_count=station_ingest_receipt_hourly.receipt_count+excluded.receipt_count,
                   first_received_at=MIN(station_ingest_receipt_hourly.first_received_at,
                                         excluded.first_received_at),
                   last_complete_raw_frame_id=CASE
                       WHEN excluded.last_received_at>=station_ingest_receipt_hourly.last_received_at
                       THEN excluded.last_complete_raw_frame_id
                       ELSE station_ingest_receipt_hourly.last_complete_raw_frame_id END,
                   last_received_at=MAX(station_ingest_receipt_hourly.last_received_at,
                                        excluded.last_received_at),
                   updated_at=excluded.updated_at""",
            (_now(), cutoff),
        )
        connection.execute(
            """INSERT OR IGNORE INTO station_ingest_recent_receipts(
                   raw_frame_id,source_key,endpoint_id,station_code,received_at,authentication_status)
                SELECT id,CASE WHEN endpoint_id IS NOT NULL THEN 'endpoint:' || endpoint_id
                               WHEN station_code IS NOT NULL AND station_code<>'' THEN 'station:' || station_code
                               ELSE 'transport:unidentified' END,
                       endpoint_id,station_code,received_at,authentication_status
                FROM ingest_raw_frames INDEXED BY idx_ingest_raw_received
                WHERE received_at>=?""",
            (cutoff,),
        )
        counts = (
            connection.execute(
                "SELECT COUNT(*) FROM station_ingest_recent_receipts WHERE received_at>=?", (cutoff,)
            ).fetchone()[0],
            connection.execute(
                "SELECT COUNT(*) FROM station_ingest_receipt_hourly WHERE bucket_hour>=?", (cutoff_hour,)
            ).fetchone()[0],
        )
        connection.commit()
        return int(counts[0]), int(counts[1])


def prepare_recent_slot_summaries(
    database: Path, *, now: datetime | None = None,
) -> dict[str, int]:
    """Prepare only recent HJ212 2011 slot identities needed across a restart."""
    now = now or datetime.now(timezone.utc)
    cutoff = _cutoff(now, BOOTSTRAP_CONSUMER_WINDOW)
    with closing(sqlite3.connect(str(database), timeout=30, isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        rows = connection.execute(
            """SELECT raw.id,raw.endpoint_id,raw.station_code,raw.received_at,
                      raw.frame_sha256,raw.raw_frame,raw.duplicate_of_raw_frame_id
               FROM ingest_raw_frames raw
                    INDEXED BY idx_ingest_raw_authenticated_received
               JOIN ingest_frame_protocols protocol ON protocol.raw_frame_id=raw.id
               WHERE raw.received_at>=? AND raw.endpoint_id IS NOT NULL
                 AND raw.authentication_status='authenticated'
                 AND raw.disposition IN ('accepted','duplicate','pending_parse','pending_reparse')
                 AND protocol.protocol_family='hj212'
               ORDER BY raw.received_at,raw.id""",
            (cutoff,),
        ).fetchall()
        resolver = HJ212SlotIdentityResolver(connection)
        grouped: dict[tuple[int, str, str], list[sqlite3.Row]] = {}
        eligible = 0
        unclassified = 0
        for row in rows:
            try:
                frame = parse_hj212_frame(bytes(row["raw_frame"]))
            except HJ212FrameError:
                unclassified += 1
                continue
            identity = resolver.resolve(frame, int(row["endpoint_id"]))
            if frame.command != "2011" or identity is None:
                unclassified += 1
                continue
            eligible += 1
            slot_started_at, content_sha256 = identity
            grouped.setdefault(
                (int(row["endpoint_id"]), slot_started_at, content_sha256), []
            ).append(row)

        connection.execute("BEGIN IMMEDIATE")
        for (endpoint_id, slot_started_at, content_sha256), group in grouped.items():
            canonical = min(
                group,
                key=lambda item: (
                    item["duplicate_of_raw_frame_id"] is not None, int(item["id"])),
            )
            first_received_at = min(str(item["received_at"]) for item in group)
            last_received_at = max(str(item["received_at"]) for item in group)
            connection.execute(
                """INSERT INTO station_ingest_slot_summaries(
                       endpoint_id,station_code,command,slot_started_at,content_sha256,
                       canonical_raw_frame_id,canonical_frame_sha256,receipt_count,
                       first_received_at,last_received_at,last_authentication_status,
                       created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?, ?,?,?, 'authenticated',?,?)
                   ON CONFLICT(endpoint_id,command,slot_started_at,content_sha256) DO UPDATE SET
                       receipt_count=MAX(station_ingest_slot_summaries.receipt_count,
                                         excluded.receipt_count),
                       first_received_at=MIN(station_ingest_slot_summaries.first_received_at,
                                             excluded.first_received_at),
                       last_received_at=MAX(station_ingest_slot_summaries.last_received_at,
                                            excluded.last_received_at),
                       last_authentication_status=CASE
                           WHEN excluded.last_received_at>=station_ingest_slot_summaries.last_received_at
                           THEN excluded.last_authentication_status
                           ELSE station_ingest_slot_summaries.last_authentication_status END,
                       updated_at=excluded.updated_at""",
                (
                    endpoint_id, canonical["station_code"], "2011", slot_started_at,
                    content_sha256, canonical["id"], canonical["frame_sha256"], len(group),
                    first_received_at, last_received_at, _now(), _now(),
                ),
            )
        connection.commit()
    return {
        "scanned_frames": len(rows),
        "eligible_frames": eligible,
        "slot_identities": len(grouped),
        "unclassified_frames": unclassified,
        "consumer_window_hours": int(
            BOOTSTRAP_CONSUMER_WINDOW.total_seconds() // 3600),
    }


def _prepare_protected_dedup_keys(database: Path) -> int:
    protected_ids = """SELECT batch.raw_frame_id FROM observation_batches batch
                       JOIN monitoring_business_observations business
                         ON business.observation_batch_id=batch.id
                       UNION SELECT batch.raw_frame_id FROM observation_batches batch
                       JOIN monitoring_hourly_value_series hourly
                         ON hourly.source_last_batch_id=batch.id
                       UNION SELECT batch.raw_frame_id FROM observation_batches batch
                       JOIN monitoring_hourly_values hourly
                         ON hourly.source_last_batch_id=batch.id
                       UNION SELECT batch.raw_frame_id FROM observation_batches batch
                       JOIN observation_batches replacement ON replacement.replaces_batch_id=batch.id
                       UNION SELECT raw_frame_id FROM monitoring_quality_issues WHERE status='open'
                       UNION SELECT batch.raw_frame_id FROM monitoring_quality_issues issue
                       JOIN observation_batches batch ON batch.id=issue.observation_batch_id
                       WHERE issue.status='open'
                       UNION SELECT raw_frame_id FROM monitoring_normalization_retries
                       WHERE state IN ('pending','retrying','exhausted')
                       UNION SELECT raw_frame_id FROM ingest_errors WHERE status='open'"""
    with closing(sqlite3.connect(str(database), timeout=30, isolation_level=None)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            f"""WITH protected_ids(raw_id) AS ({protected_ids}), grouped AS (
                   SELECT raw.endpoint_id,raw.logical_key_sha256,
                          MIN(raw.id) AS canonical_raw_frame_id,
                          MIN(raw.received_at) AS first_received_at,
                          MAX(raw.received_at) AS last_received_at
                   FROM protected_ids protected JOIN ingest_raw_frames raw ON raw.id=protected.raw_id
                   WHERE raw.endpoint_id IS NOT NULL AND raw.logical_key_sha256 IS NOT NULL
                   GROUP BY raw.endpoint_id,raw.logical_key_sha256)
               INSERT INTO station_ingest_dedup_keys(
                   endpoint_id,logical_key_sha256,canonical_raw_frame_id,canonical_evidence_ref_id,
                   first_received_at,last_received_at,expires_at,protected,updated_at)
               SELECT grouped.endpoint_id,grouped.logical_key_sha256,grouped.canonical_raw_frame_id,
                      evidence.id,grouped.first_received_at,grouped.last_received_at,NULL,1,?
               FROM grouped LEFT JOIN monitoring_archive_evidence_refs evidence
                 ON evidence.source_raw_frame_id=grouped.canonical_raw_frame_id
               ON CONFLICT(endpoint_id,logical_key_sha256) DO UPDATE SET
                   protected=1,expires_at=NULL,
                   canonical_evidence_ref_id=COALESCE(
                       station_ingest_dedup_keys.canonical_evidence_ref_id,
                       excluded.canonical_evidence_ref_id),
                   first_received_at=MIN(station_ingest_dedup_keys.first_received_at,
                                         excluded.first_received_at),
                   last_received_at=MAX(station_ingest_dedup_keys.last_received_at,
                                        excluded.last_received_at),
                   updated_at=excluded.updated_at""",
            (_now(),),
        )
        count = connection.execute(
            "SELECT COUNT(*) FROM station_ingest_dedup_keys WHERE protected=1"
        ).fetchone()[0]
        connection.commit()
        return int(count)


def prepare_startup_state(database: Path, *, now: datetime | None = None) -> dict[str, int]:
    """Prepare the bounded state required before Web and receiver startup."""
    now = now or datetime.now(timezone.utc)
    consumer_cutoff = _cutoff(now, BOOTSTRAP_CONSUMER_WINDOW)
    current_states = _prepare_current_state(database, recent_cutoff=consumer_cutoff)
    recent_receipts, receipt_hours = _prepare_consumer_windows(
        database, cutoff=consumer_cutoff)
    return {
        "current_states": current_states,
        "recent_receipts": recent_receipts,
        "receipt_hour_summaries": receipt_hours,
        "consumer_window_hours": int(BOOTSTRAP_CONSUMER_WINDOW.total_seconds() // 3600),
        "historical_error_summaries": 0,
    }


def bootstrap_retention_state(database: Path, *, now: datetime | None = None) -> dict[str, int]:
    """Prepare current consumers plus protected evidence for explicit governance."""
    result = prepare_startup_state(database, now=now)
    result["protected_dedup_keys"] = _prepare_protected_dedup_keys(database)
    return result


def _write_detail_archive(
    root: Path, kind: str, records: list[dict[str, Any]], *,
    deadline: float | None = None,
) -> tuple[Path, str]:
    lines = []
    for record in records:
        if _deadline_expired(deadline):
            raise _StatementDeadlineExceeded(f"detail_archive_write:{kind}")
        lines.append(
            (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    digest = hashlib.sha256(b"".join(lines)).hexdigest()
    directory = root / "detail-archives"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{kind}-{records[0]['id']}-{records[-1]['id']}-{digest[:16]}.jsonl.gz"
    if not path.exists():
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            with gzip.open(temporary, "wb") as stream:
                for line in lines:
                    if _deadline_expired(deadline):
                        raise _StatementDeadlineExceeded(f"detail_archive_write:{kind}")
                    stream.write(line)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    verified = hashlib.sha256()
    actual = []
    try:
        with gzip.open(path, "rb") as stream:
            for line in stream:
                if _deadline_expired(deadline):
                    raise _StatementDeadlineExceeded(f"detail_archive_verify:{kind}")
                verified.update(line)
                actual.append(json.loads(line.decode("utf-8")))
    except _StatementDeadlineExceeded:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetentionError("detail archive file cannot be verified") from exc
    if verified.hexdigest() != digest or actual != records:
        raise RetentionError("detail archive content verification failed")
    return path, digest


def read_detail_archive_record(
    connection: sqlite3.Connection, detail_kind: str, source_id: int,
) -> dict[str, Any]:
    """Read and verify one archived detail row through the recorded archive part."""
    parts = connection.execute(
        """SELECT archive_path,content_sha256,record_count,mirror_path,mirror_verified_at
           FROM station_ingest_detail_archive_parts
           WHERE detail_kind=? AND first_source_id<=? AND last_source_id>=?
           ORDER BY id DESC""",
        (detail_kind, source_id, source_id),
    ).fetchall()
    for part in parts:
        path = _archive_read_path(part[0], part[3], part[4])
        digest = hashlib.sha256()
        count = 0
        selected = None
        evidence: dict[str, dict[str, Any]] = {}
        try:
            with gzip.open(path, "rb") as stream:
                for line in stream:
                    digest.update(line)
                    count += 1
                    item = json.loads(line.decode("utf-8"))
                    if item.get("record_type") == "raw_evidence":
                        payload = base64.b64decode(item["frame_b64"], validate=True)
                        if hashlib.sha256(payload).hexdigest() != item["frame_sha256"]:
                            raise RetentionError("detail archive raw evidence digest mismatch")
                        evidence[str(item["evidence_key"])] = item
                    elif int(item.get("id", -1)) == source_id:
                        selected = item
        except RetentionError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError) as exc:
            raise RetentionError("detail archive cannot be read; retry after storage recovery") from exc
        if digest.hexdigest() != str(part[1]) or count != int(part[2]):
            raise RetentionError("detail archive content verification failed")
        if selected is not None:
            if selected.get("evidence_key"):
                selected = dict(selected)
                selected["evidence"] = evidence.get(str(selected["evidence_key"]))
                if selected["evidence"] is None:
                    raise RetentionError("detail archive raw evidence is unavailable")
            return selected
    raise RetentionError("archived detail record is unavailable")


def _exact_duplicate_rows(
    connection: sqlite3.Connection, raw_ids: list[int], cutoff: str,
) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    for identifiers in _batches(raw_ids):
        marks = ",".join("?" * len(identifiers))
        rows.extend(connection.execute(
            f"""SELECT duplicate.id,duplicate.endpoint_id,duplicate.station_code,
                       duplicate.received_at,duplicate.frame_sha256,
                       duplicate.logical_key_sha256,duplicate.raw_frame,
                       duplicate.body_length,duplicate.crc_status,
                       duplicate.authentication_status,duplicate.disposition,
                       duplicate.duplicate_of_raw_frame_id,duplicate.persistence_state,
                       canonical.endpoint_id AS canonical_endpoint_id,
                       canonical.station_code AS canonical_station_code,
                       canonical.received_at AS canonical_received_at,
                       canonical.frame_sha256 AS canonical_frame_sha256,
                       canonical.logical_key_sha256 AS canonical_logical_key_sha256,
                       canonical.raw_frame AS canonical_raw_frame,
                       canonical.body_length AS canonical_body_length,
                       canonical.crc_status AS canonical_crc_status,
                       canonical.authentication_status AS canonical_authentication_status,
                       canonical.disposition AS canonical_disposition,
                       canonical.persistence_state AS canonical_persistence_state
                FROM ingest_raw_frames duplicate
                JOIN ingest_raw_frames canonical
                  ON canonical.id=duplicate.duplicate_of_raw_frame_id
                 AND duplicate.disposition='duplicate'
                 AND duplicate.endpoint_id=canonical.endpoint_id
                 AND duplicate.logical_key_sha256=canonical.logical_key_sha256
                 AND duplicate.frame_sha256=canonical.frame_sha256
                WHERE duplicate.id IN ({marks}) AND duplicate.received_at<?
                ORDER BY duplicate.id""",
            (*identifiers, cutoff),
        ).fetchall())
    return rows


def _open_errors_for_raw_ids(
    connection: sqlite3.Connection, raw_ids: list[int],
) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    for identifiers in _batches(raw_ids):
        marks = ",".join("?" * len(identifiers))
        rows.extend(connection.execute(
            f"""SELECT error.*,raw.endpoint_id,raw.station_code,
                       CASE WHEN raw.endpoint_id IS NOT NULL THEN 'endpoint:' || raw.endpoint_id
                            WHEN raw.station_code IS NOT NULL AND raw.station_code<>''
                              THEN 'station:' || raw.station_code
                            ELSE 'transport:unidentified' END AS source_key
                FROM ingest_errors error
                LEFT JOIN ingest_raw_frames raw ON raw.id=error.raw_frame_id
                WHERE error.status='open' AND error.raw_frame_id IN ({marks})
                ORDER BY error.raw_frame_id,error.id""",
            identifiers,
        ).fetchall())
    return rows


def _open_errors_by_ids(
    connection: sqlite3.Connection, error_ids: list[int],
) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    for identifiers in _batches(error_ids):
        marks = ",".join("?" * len(identifiers))
        rows.extend(connection.execute(
            f"""SELECT error.*,raw.endpoint_id,raw.station_code,
                       CASE WHEN raw.endpoint_id IS NOT NULL THEN 'endpoint:' || raw.endpoint_id
                            WHEN raw.station_code IS NOT NULL AND raw.station_code<>''
                              THEN 'station:' || raw.station_code
                            ELSE 'transport:unidentified' END AS source_key
                FROM ingest_errors error
                LEFT JOIN ingest_raw_frames raw ON raw.id=error.raw_frame_id
                WHERE error.status='open' AND error.id IN ({marks})
                ORDER BY error.id""",
            identifiers,
        ).fetchall())
    return rows


def _error_group_token(group: tuple[str, str]) -> str:
    return json.dumps(group, separators=(",", ":"))


def _bounded_error_representatives(
    connection: sqlite3.Connection,
    minimums: dict[tuple[str, str], tuple[str, int]],
    batch_errors: list[sqlite3.Row], *, scan_limit: int,
    deadline: float | None, search_state: dict[str, dict[str, Any]],
) -> tuple[
    dict[tuple[str, str], sqlite3.Row], dict[str, list[str]],
    dict[str, dict[str, Any]], set[tuple[str, str]], dict[str, int],
]:
    """Find newer representatives from the batch first, then resume bounded index pages."""
    representatives: dict[tuple[str, str], sqlite3.Row] = {}
    plans: dict[str, list[str]] = {}
    for row in sorted(
            batch_errors,
            key=lambda item: (str(item["created_at"]), int(item["id"])), reverse=True):
        group = (str(row["source_key"]), str(row["error_type"]))
        minimum = minimums.get(group)
        if minimum is not None and group not in representatives and (
                str(row["created_at"]), int(row["id"])) > minimum:
            representatives[group] = row

    unresolved = [group for group in minimums if group not in representatives]
    page_limit = max(1, scan_limit // max(1, len(unresolved)))
    next_state: dict[str, dict[str, Any]] = {}
    exhausted: set[tuple[str, str]] = set()
    scanned_rows = 0
    searched_groups = 0
    for group in unresolved:
        if _deadline_expired(deadline):
            raise _StatementDeadlineExceeded("exact_error_representative_scan")
        source_key, error_type = group
        minimum_created_at, minimum_id = minimums[group]
        token = _error_group_token(group)
        state = search_state.get(token, {})
        if (state.get("minimum_created_at") != minimum_created_at
                or int(state.get("minimum_id", -1)) != minimum_id):
            state = {}
        if state.get("exhausted"):
            exhausted.add(group)
            next_state[token] = dict(state)
            continue
        remaining_budget = scan_limit - scanned_rows
        if remaining_budget <= 0:
            deferred_state = {
                "source_key": source_key,
                "error_type": error_type,
                "minimum_created_at": minimum_created_at,
                "minimum_id": minimum_id,
            }
            for key in ("after_created_at", "after_id"):
                if state.get(key) is not None:
                    deferred_state[key] = state[key]
            next_state[token] = deferred_state
            continue
        current_page_limit = min(page_limit, remaining_budget)
        after_created_at = state.get("after_created_at")
        after_id = state.get("after_id")
        cursor_clause = ""
        parameters: list[Any] = [error_type]
        if after_created_at is not None and after_id is not None:
            cursor_clause = " AND (error.created_at<? OR (error.created_at=? AND error.id<?))"
            parameters.extend([after_created_at, after_created_at, int(after_id)])
        sql = f"""SELECT error.*,raw.endpoint_id,raw.station_code,
                         CASE WHEN raw.endpoint_id IS NOT NULL THEN 'endpoint:' || raw.endpoint_id
                              WHEN raw.station_code IS NOT NULL AND raw.station_code<>''
                                THEN 'station:' || raw.station_code
                              ELSE 'transport:unidentified' END AS source_key
                  FROM ingest_errors error INDEXED BY idx_ingest_errors_open
                  LEFT JOIN ingest_raw_frames raw ON raw.id=error.raw_frame_id
                  WHERE error.error_type=? AND error.status='open'{cursor_clause}
                  ORDER BY error.created_at DESC,error.id DESC LIMIT ?"""
        parameters.append(current_page_limit)
        if error_type not in plans:
            with _statement_deadline(
                    connection, deadline, "exact_error_representative_plan"):
                plans[error_type] = [str(row[3]) for row in connection.execute(
                    "EXPLAIN QUERY PLAN " + sql, parameters)]
        with _statement_deadline(
                connection, deadline, "exact_error_representative_scan"):
            rows = connection.execute(sql, parameters).fetchall()
        searched_groups += 1
        scanned_rows += len(rows)
        reached_candidate = False
        for row in rows:
            row_group = (str(row["source_key"]), str(row["error_type"]))
            if row_group != group:
                continue
            identity = (str(row["created_at"]), int(row["id"]))
            if identity > minimums[group]:
                representatives[group] = row
            else:
                reached_candidate = True
            break
        if group in representatives:
            continue
        if reached_candidate or len(rows) < current_page_limit:
            exhausted.add(group)
            next_state[token] = {
                "source_key": source_key,
                "error_type": error_type,
                "minimum_created_at": minimum_created_at,
                "minimum_id": minimum_id,
                "exhausted": True,
            }
            continue
        last = rows[-1]
        next_state[token] = {
            "source_key": source_key,
            "error_type": error_type,
            "minimum_created_at": minimum_created_at,
            "minimum_id": minimum_id,
            "after_created_at": str(last["created_at"]),
            "after_id": int(last["id"]),
        }
    return (
        representatives, plans, next_state, exhausted,
        {
            "groups": len(minimums), "searched_groups": searched_groups,
            "page_limit": page_limit, "rows": scanned_rows,
        },
    )

def _online_raw_evidence(exact: sqlite3.Row) -> tuple[int, bytes] | None:
    candidates = (
        (int(exact["duplicate_of_raw_frame_id"]), bytes(exact["canonical_raw_frame"] or b"")),
        (int(exact["id"]), bytes(exact["raw_frame"] or b"")),
    )
    available = []
    for raw_id, payload in candidates:
        if not payload:
            continue
        if hashlib.sha256(payload).hexdigest() != str(exact["frame_sha256"]):
            raise RetentionError("online exact duplicate evidence digest mismatch")
        available.append((raw_id, payload))
    if len(available) == 2 and available[0][1] != available[1][1]:
        raise RetentionError("online exact duplicate evidence content mismatch")
    return available[0] if available else None


def _write_exact_duplicate_error_bundle(
    root: Path, error_records: list[dict[str, Any]],
    evidence_records: list[dict[str, Any]], *, deadline: float | None,
) -> tuple[Path, str, list[dict[str, Any]], bool]:
    records = evidence_records + error_records
    lines = []
    digest = hashlib.sha256()
    for record in records:
        if _deadline_expired(deadline):
            raise _StatementDeadlineExceeded("exact_error_archive_write")
        line = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        lines.append(line)
        digest.update(line)
    content_digest = digest.hexdigest()
    directory = root / "detail-archives"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (
        f"ingest_error-{error_records[0]['id']}-{error_records[-1]['id']}-"
        f"{content_digest[:16]}.jsonl.gz")
    created = not path.exists()
    if created:
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            with gzip.open(temporary, "wb") as stream:
                for line in lines:
                    if _deadline_expired(deadline):
                        raise _StatementDeadlineExceeded("exact_error_archive_write")
                    stream.write(line)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    verified = hashlib.sha256()
    actual = []
    try:
        with gzip.open(path, "rb") as stream:
            for line in stream:
                if _deadline_expired(deadline):
                    raise _StatementDeadlineExceeded("exact_error_archive_verify")
                verified.update(line)
                actual.append(json.loads(line.decode("utf-8")))
    except _StatementDeadlineExceeded:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetentionError("exact duplicate error archive cannot be verified") from exc
    if verified.hexdigest() != content_digest or actual != records:
        raise RetentionError("exact duplicate error archive content verification failed")
    return path, content_digest, records, created


def _archived_raw_evidence_batch(
    connection: sqlite3.Connection, raw_ids: list[int], *, deadline: float | None,
) -> dict[int, bytes]:
    """Read each referenced raw archive part at most once for this cleanup batch."""
    if not raw_ids:
        return {}
    references: list[sqlite3.Row] = []
    for identifiers in _batches(raw_ids):
        marks = ",".join("?" * len(identifiers))
        with _statement_deadline(connection, deadline, "exact_archive_reference_scan"):
            references.extend(connection.execute(
                f"""SELECT evidence.source_raw_frame_id,evidence.archive_part_id,
                           evidence.archive_record_index,evidence.record_sha256,
                           evidence.frame_sha256,part.archive_path,part.content_sha256,
                           metadata.mirror_path,metadata.mirror_verified_at
                    FROM monitoring_archive_evidence_refs evidence
                    JOIN monitoring_raw_archive_parts part ON part.id=evidence.archive_part_id
                    LEFT JOIN monitoring_raw_archive_part_metadata metadata
                      ON metadata.archive_part_id=part.id
                    WHERE evidence.source_raw_frame_id IN ({marks})""",
                identifiers,
            ).fetchall())
    by_part: dict[int, list[sqlite3.Row]] = {}
    for reference in references:
        by_part.setdefault(int(reference["archive_part_id"]), []).append(reference)
    recovered: dict[int, bytes] = {}
    for part_references in by_part.values():
        first = part_references[0]
        try:
            path = _archive_read_path(
                first["archive_path"], first["mirror_path"], first["mirror_verified_at"])
            wanted = {int(row["archive_record_index"]): row for row in part_references}
            selected: dict[int, bytes] = {}
            digest = hashlib.sha256()
            with gzip.open(path, "rb") as stream:
                for index, line in enumerate(stream):
                    if _deadline_expired(deadline):
                        raise _StatementDeadlineExceeded("exact_archive_evidence_read")
                    digest.update(line)
                    if index in wanted:
                        selected[index] = line
            if digest.hexdigest() != str(first["content_sha256"]):
                continue
            for index, reference in wanted.items():
                line = selected.get(index)
                if line is None or hashlib.sha256(line).hexdigest() != str(
                        reference["record_sha256"]):
                    continue
                item = json.loads(line.decode("ascii"))
                payload = base64.b64decode(item["frame_b64"], validate=True)
                if hashlib.sha256(payload).hexdigest() != str(reference["frame_sha256"]):
                    continue
                recovered[int(reference["source_raw_frame_id"])] = payload
        except _StatementDeadlineExceeded:
            raise
        except (RetentionError, OSError, ValueError, KeyError, UnicodeDecodeError,
                json.JSONDecodeError):
            continue
    return recovered


def _register_detail_archive_part(
    connection: sqlite3.Connection, kind: str, records: list[dict[str, Any]],
    path: Path, digest: str, *, source_records: list[dict[str, Any]] | None = None,
) -> None:
    source_records = records if source_records is None else source_records
    ids = [int(row["id"]) for row in source_records]
    connection.execute(
        """INSERT OR IGNORE INTO station_ingest_detail_archive_parts(
               detail_kind,archive_path,content_sha256,first_source_id,last_source_id,
               record_count,range_start,range_end,verified_at,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (kind, str(path), digest, min(ids), max(ids), len(records),
         min(str(row.get("created_at") or row.get("bucket_hour")) for row in source_records),
         max(str(row.get("created_at") or row.get("bucket_hour")) for row in source_records),
         _now(), _now()),
    )
    registered = connection.execute(
        """SELECT detail_kind,content_sha256,first_source_id,last_source_id,record_count
           FROM station_ingest_detail_archive_parts WHERE archive_path=?""",
        (str(path),),
    ).fetchone()
    expected = (kind, digest, min(ids), max(ids), len(records))
    if registered is None or tuple(registered) != expected:
        raise RetentionError("detail archive registration does not match verified content")


def _archive_detail_batch(
    database: Path, root: Path, kind: str, cutoff: str, *, batch_limit: int,
    deadline: float | None = None,
) -> int:
    table = {
        "parse_attempt": "ingest_parse_attempts",
        "ingest_error": "ingest_errors",
        "error_summary": "station_ingest_error_hourly",
    }[kind]
    with closing(sqlite3.connect(str(database))) as connection:
        connection.row_factory = sqlite3.Row
        with _statement_deadline(connection, deadline, f"detail_archive_select:{kind}"):
            if kind == "parse_attempt":
                rows = connection.execute(
                """SELECT attempt.*,evidence.id AS evidence_ref_id
                   FROM ingest_parse_attempts attempt
                   JOIN ingest_raw_frames raw ON raw.id=attempt.raw_frame_id
                   JOIN monitoring_archive_evidence_refs evidence
                     ON evidence.source_raw_frame_id=attempt.raw_frame_id
                   WHERE julianday(attempt.created_at)<julianday(?)
                     AND raw.persistence_state='persisted'
                   ORDER BY attempt.id LIMIT ?""",
                (cutoff, batch_limit),
                ).fetchall()
            elif kind == "ingest_error":
                rows = connection.execute(
                """SELECT error.*,raw.endpoint_id,raw.station_code,
                          evidence.id AS evidence_ref_id
                   FROM ingest_errors error
                   LEFT JOIN ingest_raw_frames raw ON raw.id=error.raw_frame_id
                   LEFT JOIN monitoring_archive_evidence_refs evidence
                     ON evidence.source_raw_frame_id=error.raw_frame_id
                   WHERE julianday(error.created_at)<julianday(?)
                     AND (error.raw_frame_id IS NULL OR evidence.id IS NOT NULL)
                     AND NOT (
                       error.status='open' AND NOT EXISTS (
                         SELECT 1 FROM ingest_errors newer
                         LEFT JOIN ingest_raw_frames newer_raw ON newer_raw.id=newer.raw_frame_id
                         WHERE newer.status='open' AND newer.error_type=error.error_type
                           AND (CASE
                             WHEN newer_raw.endpoint_id IS NOT NULL
                               THEN 'endpoint:' || newer_raw.endpoint_id
                             WHEN newer_raw.station_code IS NOT NULL AND newer_raw.station_code<>''
                               THEN 'station:' || newer_raw.station_code
                             ELSE 'transport:unidentified' END)
                             = (CASE
                               WHEN raw.endpoint_id IS NOT NULL THEN 'endpoint:' || raw.endpoint_id
                               WHEN raw.station_code IS NOT NULL AND raw.station_code<>''
                                 THEN 'station:' || raw.station_code
                               ELSE 'transport:unidentified' END)
                           AND newer.id>error.id))
                   ORDER BY error.id LIMIT ?""",
                (cutoff, batch_limit),
                ).fetchall()
            else:
                rows = connection.execute(
                """SELECT rowid AS id,* FROM station_ingest_error_hourly
                   WHERE julianday(bucket_hour)<julianday(?)
                   ORDER BY bucket_hour,source_key,error_type LIMIT ?""",
                (cutoff, batch_limit),
                ).fetchall()
    if not rows:
        return 0
    if _deadline_expired(deadline):
        raise _StatementDeadlineExceeded(f"detail_archive_write:{kind}")
    records = [dict(row) for row in rows]
    path, digest = _write_detail_archive(root, kind, records, deadline=deadline)
    ids = [int(row["id"]) for row in rows]
    with closing(sqlite3.connect(str(database), timeout=5, isolation_level=None)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        with _statement_deadline(connection, deadline, f"detail_archive_lock:{kind}"):
            connection.execute("BEGIN IMMEDIATE")
        placeholders = ",".join("?" * len(ids))
        with _statement_deadline(connection, deadline, f"detail_archive_commit:{kind}"):
            if kind == "error_summary":
                current = connection.execute(
                f"SELECT COUNT(*) FROM station_ingest_error_hourly WHERE rowid IN ({placeholders})", ids
                ).fetchone()[0]
            else:
                current = connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE id IN ({placeholders})", ids
                ).fetchone()[0]
            if current != len(ids):
                raise RetentionError("detail rows changed while archive was being written")
            _register_detail_archive_part(connection, kind, records, path, digest)
            if kind == "error_summary":
                connection.execute(
                f"DELETE FROM station_ingest_error_hourly WHERE rowid IN ({placeholders})", ids)
            else:
                connection.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", ids)
            connection.commit()
    return len(ids)


def _protected_raw_ids(connection: sqlite3.Connection, raw_ids: list[int]) -> set[int]:
    protected: set[int] = set()
    queries = (
        """SELECT batch.raw_frame_id FROM observation_batches batch
           JOIN observation_values value ON value.observation_batch_id=batch.id
           WHERE value.quality IN ('valid','suspect') AND value.is_published=1
             AND batch.raw_frame_id IN ({marks})""",
        """SELECT batch.raw_frame_id FROM observation_batches batch
           JOIN monitoring_business_observations business ON business.observation_batch_id=batch.id
           WHERE batch.raw_frame_id IN ({marks})""",
        """SELECT batch.raw_frame_id FROM observation_batches batch
           JOIN monitoring_hourly_value_series hourly ON hourly.source_last_batch_id=batch.id
           WHERE batch.raw_frame_id IN ({marks})""",
        """SELECT batch.raw_frame_id FROM observation_batches batch
           JOIN monitoring_hourly_values hourly ON hourly.source_last_batch_id=batch.id
           WHERE batch.raw_frame_id IN ({marks})""",
        """SELECT batch.raw_frame_id FROM observation_batches batch
           JOIN observation_batches replacement ON replacement.replaces_batch_id=batch.id
           WHERE batch.raw_frame_id IN ({marks})""",
        """SELECT raw_frame_id FROM monitoring_quality_issues
           WHERE status='open' AND raw_frame_id IN ({marks})""",
        """SELECT batch.raw_frame_id FROM monitoring_quality_issues issue
           JOIN observation_batches batch ON batch.id=issue.observation_batch_id
           WHERE issue.status='open' AND batch.raw_frame_id IN ({marks})""",
        """SELECT raw_frame_id FROM monitoring_normalization_retries
           WHERE state IN ('pending','retrying','exhausted') AND raw_frame_id IN ({marks})""",
        """SELECT raw_frame_id FROM ingest_errors
           WHERE status='open' AND raw_frame_id IN ({marks})""",
        """SELECT canonical_raw_frame_id FROM station_ingest_slot_summaries
           WHERE canonical_raw_frame_id IN ({marks})""",
        """SELECT canonical_raw_frame_id FROM station_ingest_dedup_keys
           WHERE protected=1 AND canonical_raw_frame_id IN ({marks})""",
        """SELECT canonical_raw_frame_id FROM station_ingest_duplicate_links
           WHERE canonical_raw_frame_id IN ({marks})""",
        """SELECT duplicate_of_raw_frame_id FROM ingest_raw_frames
           WHERE duplicate_of_raw_frame_id IN ({marks})""",
        """SELECT raw_frame_id FROM station_ingest_recent_receipts
           WHERE raw_frame_id IN ({marks})""",
        """SELECT latest_raw_frame_id FROM station_ingest_current_state
           WHERE latest_raw_frame_id IN ({marks})""",
        """SELECT latest_authenticated_raw_frame_id FROM station_ingest_current_state
           WHERE latest_authenticated_raw_frame_id IN ({marks})""",
        """SELECT latest_credential_failed_raw_frame_id FROM station_ingest_current_state
           WHERE latest_credential_failed_raw_frame_id IN ({marks})""",
    )
    for batch in _batches(raw_ids):
        marks = ",".join("?" * len(batch))
        for query in queries:
            protected.update(int(row[0]) for row in connection.execute(
                query.format(marks=marks), batch,
            ))
    return protected


def _exact_cleanup_non_error_protection(
    connection: sqlite3.Connection, raw_ids: list[int], receipt_cutoff: str,
) -> set[int]:
    """Return protections that remain after historical errors and receipts are retired."""
    protected: set[int] = set()
    queries = (
        """SELECT batch.raw_frame_id FROM observation_batches batch
           JOIN observation_values value ON value.observation_batch_id=batch.id
           WHERE value.quality IN ('valid','suspect') AND value.is_published=1
             AND batch.raw_frame_id IN ({marks})""",
        """SELECT batch.raw_frame_id FROM observation_batches batch
           JOIN monitoring_business_observations business ON business.observation_batch_id=batch.id
           WHERE batch.raw_frame_id IN ({marks})""",
        """SELECT batch.raw_frame_id FROM observation_batches batch
           JOIN monitoring_hourly_value_series hourly ON hourly.source_last_batch_id=batch.id
           WHERE batch.raw_frame_id IN ({marks})""",
        """SELECT batch.raw_frame_id FROM observation_batches batch
           JOIN monitoring_hourly_values hourly ON hourly.source_last_batch_id=batch.id
           WHERE batch.raw_frame_id IN ({marks})""",
        """SELECT batch.raw_frame_id FROM observation_batches batch
           JOIN observation_batches replacement ON replacement.replaces_batch_id=batch.id
           WHERE batch.raw_frame_id IN ({marks})""",
        """SELECT raw_frame_id FROM monitoring_quality_issues
           WHERE status='open' AND raw_frame_id IN ({marks})""",
        """SELECT batch.raw_frame_id FROM monitoring_quality_issues issue
           JOIN observation_batches batch ON batch.id=issue.observation_batch_id
           WHERE issue.status='open' AND batch.raw_frame_id IN ({marks})""",
        """SELECT raw_frame_id FROM monitoring_normalization_retries
           WHERE state IN ('pending','retrying','exhausted') AND raw_frame_id IN ({marks})""",
        """SELECT canonical_raw_frame_id FROM station_ingest_slot_summaries
           WHERE canonical_raw_frame_id IN ({marks})""",
        """SELECT canonical_raw_frame_id FROM station_ingest_dedup_keys
           WHERE protected=1 AND canonical_raw_frame_id IN ({marks})""",
        """SELECT canonical_raw_frame_id FROM station_ingest_duplicate_links
           WHERE canonical_raw_frame_id IN ({marks})""",
        """SELECT duplicate_of_raw_frame_id FROM ingest_raw_frames
           WHERE duplicate_of_raw_frame_id IN ({marks})""",
        """SELECT raw_frame_id FROM station_ingest_recent_receipts
           WHERE received_at>=? AND raw_frame_id IN ({marks})""",
        """SELECT latest_raw_frame_id FROM station_ingest_current_state
           WHERE latest_raw_frame_id IN ({marks})""",
        """SELECT latest_authenticated_raw_frame_id FROM station_ingest_current_state
           WHERE latest_authenticated_raw_frame_id IN ({marks})""",
        """SELECT latest_credential_failed_raw_frame_id FROM station_ingest_current_state
           WHERE latest_credential_failed_raw_frame_id IN ({marks})""",
    )
    for batch in _batches(raw_ids):
        marks = ",".join("?" * len(batch))
        for query in queries:
            parameters = (receipt_cutoff, *batch) if "received_at>=?" in query else batch
            protected.update(int(row[0]) for row in connection.execute(
                query.format(marks=marks), parameters,
            ))
    return protected


def _protected_raw_predicate(raw_alias: str = "raw") -> str:
    return f"""EXISTS (
                    SELECT 1 FROM observation_batches batch
                    JOIN observation_values value ON value.observation_batch_id=batch.id
                    WHERE batch.raw_frame_id={raw_alias}.id
                      AND value.quality IN ('valid','suspect') AND value.is_published=1)
                OR EXISTS (
                    SELECT 1 FROM observation_batches batch
                    JOIN monitoring_business_observations business
                      ON business.observation_batch_id=batch.id
                    WHERE batch.raw_frame_id={raw_alias}.id)
                OR EXISTS (
                    SELECT 1 FROM observation_batches batch
                    JOIN monitoring_hourly_value_series hourly
                      ON hourly.source_last_batch_id=batch.id
                    WHERE batch.raw_frame_id={raw_alias}.id)
                OR EXISTS (
                    SELECT 1 FROM observation_batches batch
                    JOIN monitoring_hourly_values hourly
                      ON hourly.source_last_batch_id=batch.id
                    WHERE batch.raw_frame_id={raw_alias}.id)
                OR EXISTS (
                    SELECT 1 FROM observation_batches batch
                    JOIN observation_batches replacement ON replacement.replaces_batch_id=batch.id
                    WHERE batch.raw_frame_id={raw_alias}.id)
                OR EXISTS (
                    SELECT 1 FROM monitoring_quality_issues issue
                    WHERE issue.raw_frame_id={raw_alias}.id AND issue.status='open')
                OR EXISTS (
                    SELECT 1 FROM monitoring_quality_issues issue
                    JOIN observation_batches batch ON batch.id=issue.observation_batch_id
                    WHERE batch.raw_frame_id={raw_alias}.id AND issue.status='open')
                OR EXISTS (
                    SELECT 1 FROM monitoring_normalization_retries retry
                    WHERE retry.raw_frame_id={raw_alias}.id
                      AND retry.state IN ('pending','retrying','exhausted'))
                OR EXISTS (
                    SELECT 1 FROM ingest_errors error
                    WHERE error.raw_frame_id={raw_alias}.id AND error.status='open')
                OR EXISTS (
                    SELECT 1 FROM station_ingest_slot_summaries summary
                    WHERE summary.canonical_raw_frame_id={raw_alias}.id)
                OR EXISTS (
                    SELECT 1 FROM station_ingest_dedup_keys directory
                    WHERE directory.canonical_raw_frame_id={raw_alias}.id AND directory.protected=1)
                OR EXISTS (
                    SELECT 1 FROM station_ingest_duplicate_links link
                    WHERE link.canonical_raw_frame_id={raw_alias}.id)
                OR EXISTS (
                    SELECT 1 FROM ingest_raw_frames duplicate
                    WHERE duplicate.duplicate_of_raw_frame_id={raw_alias}.id)
                OR EXISTS (
                    SELECT 1 FROM station_ingest_current_state state
                    WHERE state.latest_raw_frame_id={raw_alias}.id
                       OR state.latest_authenticated_raw_frame_id={raw_alias}.id
                       OR state.latest_credential_failed_raw_frame_id={raw_alias}.id)"""


def _delete_confirmed_duplicate_rows(
    connection: sqlite3.Connection, raw_ids: list[int],
) -> int:
    deleted = 0
    for identifiers in _batches(raw_ids):
        marks = ",".join("?" * len(identifiers))
        batch_ids = [int(row[0]) for row in connection.execute(
            f"SELECT id FROM observation_batches WHERE raw_frame_id IN ({marks})", identifiers,
        )]
        if batch_ids:
            batch_marks = ",".join("?" * len(batch_ids))
            connection.execute(
                f"""DELETE FROM monitoring_quality_issues
                    WHERE observation_batch_id IN ({batch_marks}) AND status!='open'""",
                batch_ids,
            )
            connection.execute(
                f"DELETE FROM monitoring_status_events WHERE observation_batch_id IN ({batch_marks})",
                batch_ids,
            )
            connection.execute(
                f"DELETE FROM observation_values WHERE observation_batch_id IN ({batch_marks})",
                batch_ids,
            )
            connection.execute(
                f"DELETE FROM observation_batches WHERE id IN ({batch_marks})", batch_ids,
            )
        connection.execute(
            f"DELETE FROM monitoring_quality_issues WHERE raw_frame_id IN ({marks}) AND status!='open'",
            identifiers,
        )
        connection.execute(
            f"DELETE FROM ingest_parse_attempts WHERE raw_frame_id IN ({marks})", identifiers,
        )
        connection.execute(
            f"DELETE FROM ingest_errors WHERE raw_frame_id IN ({marks}) AND status!='open'", identifiers,
        )
        connection.execute(
            f"""DELETE FROM station_ingest_dedup_keys
                WHERE protected=0 AND canonical_raw_frame_id IN ({marks})""", identifiers,
        )
        connection.execute(
            f"""UPDATE station_ingest_receipt_hourly SET last_complete_raw_frame_id=NULL
                WHERE last_complete_raw_frame_id IN ({marks})""", identifiers,
        )
        connection.execute(
            f"""UPDATE station_ingest_error_hourly SET last_raw_frame_id=NULL
                WHERE last_raw_frame_id IN ({marks})""", identifiers,
        )
        for table, column in (
            ("station_ingest_duplicate_links", "raw_frame_id"),
            ("monitoring_raw_archive_part_frames", "raw_frame_id"),
            ("ingest_frame_protocols", "raw_frame_id"),
            ("station_ingest_recent_receipts", "raw_frame_id"),
            ("ingest_raw_frames", "id"),
        ):
            connection.execute(
                f"DELETE FROM {table} WHERE {column} IN ({marks})", identifiers,
            )
        deleted += len(identifiers)
    return deleted


def ensure_duplicate_cleanup_indexes(
    database: Path, *, time_budget_seconds: float | None = None,
) -> dict[str, Any]:
    """Create only the two indexes required by measured cleanup protection queries."""
    if time_budget_seconds is not None and time_budget_seconds <= 0:
        raise RetentionError("cleanup index time budget must be positive")
    started_at = datetime.now(timezone.utc)
    deadline = None if time_budget_seconds is None else time.monotonic() + time_budget_seconds
    before_bytes = Path(database).stat().st_size
    with closing(sqlite3.connect(str(database), timeout=30, isolation_level=None)) as connection:
        with _statement_deadline(connection, deadline, "exact_index_inventory"):
            existing = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'")}
        created = []
        index_stages = []
        for name, statement in CLEANUP_INDEXES.items():
            stage_started = time.monotonic()
            stage_before_bytes = Path(database).stat().st_size
            if name not in existing:
                try:
                    _begin_immediate_with_deadline(connection, deadline, f"exact_index_lock:{name}")
                    with _statement_deadline(connection, deadline, f"exact_index_create:{name}"):
                        connection.execute(statement)
                    connection.commit()
                    created.append(name)
                    existing.add(name)
                except Exception as exc:
                    if connection.in_transaction:
                        connection.rollback()
                    if isinstance(exc, _StatementDeadlineExceeded):
                        exc.checkpoint = {"index_stages": index_stages}
                    raise
            index_stages.append({
                "name": name,
                "created": name in created,
                "elapsed_seconds": round(time.monotonic() - stage_started, 3),
                "database_growth_bytes": max(
                    0, Path(database).stat().st_size - stage_before_bytes),
            })
        try:
            plans = _exact_duplicate_cleanup_query_plans(connection, deadline=deadline)
        except _StatementDeadlineExceeded as exc:
            exc.checkpoint = {"index_stages": index_stages}
            raise
    return {
        "created": created,
        "index_stages": index_stages,
        "database_growth_bytes": max(0, Path(database).stat().st_size - before_bytes),
        "elapsed_seconds": round(
            (datetime.now(timezone.utc) - started_at).total_seconds(), 3),
        "query_plans": plans,
    }


def drop_duplicate_cleanup_indexes(database: Path) -> list[str]:
    with closing(sqlite3.connect(str(database), timeout=30, isolation_level=None)) as connection:
        existing = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        removed = [name for name in CLEANUP_INDEXES if name in existing]
        connection.execute("BEGIN IMMEDIATE")
        for name in removed:
            connection.execute(f'DROP INDEX "{name}"')
        connection.commit()
    return removed


def _exact_duplicate_cleanup_query_plans(
    connection: sqlite3.Connection, *, deadline: float | None = None,
) -> dict[str, list[str]]:
    queries = {
        "bounded_raw_scan": (
            "SELECT id FROM ingest_raw_frames WHERE id>? AND id<=? ORDER BY id LIMIT ?",
            (0, 1, 1),
        ),
        "open_error_protection": (
            "SELECT raw_frame_id FROM ingest_errors WHERE status='open' AND raw_frame_id IN (?)",
            (1,),
        ),
        "canonical_reference_protection": (
            "SELECT duplicate_of_raw_frame_id FROM ingest_raw_frames WHERE duplicate_of_raw_frame_id IN (?)",
            (1,),
        ),
    }
    plans = {}
    for name, (sql, params) in queries.items():
        with _statement_deadline(connection, deadline, f"exact_query_plan:{name}"):
            plans[name] = [
                str(row[3]) for row in connection.execute(f"EXPLAIN QUERY PLAN {sql}", params)
            ]
    return plans


def _require_exact_duplicate_cleanup_indexes(connection: sqlite3.Connection) -> None:
    existing = {str(row[0]) for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name IN (?,?)",
        tuple(CLEANUP_INDEXES),
    )}
    missing = [name for name in CLEANUP_INDEXES if name not in existing]
    if missing:
        raise RetentionError(
            "exact duplicate cleanup indexes are missing; run prepare-exact-duplicate-cleanup first: "
            + ",".join(missing))


def _require_exact_duplicate_cleanup_ready(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        """SELECT migration_version,status FROM station_ingest_cutover_state
           WHERE cutover_name='station-ingest-startup-ready-v1'"""
    ).fetchone()
    if row is None or str(row[0]) != "20261016_014_station_ingest_retention_windows":
        raise RetentionError("station-ingestion startup-ready state is missing or incompatible")
    if str(row[1]) != "completed":
        raise RetentionError("station-ingestion startup-ready state is not completed")


def _parse_fixed_cutoff(value: str, label: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise RetentionError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise RetentionError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_candidate_ranges(
    values: list[str] | tuple[tuple[int, int], ...] | None,
) -> tuple[tuple[int, int], ...]:
    if not values:
        return ()
    ranges: list[tuple[int, int]] = []
    for value in values:
        if isinstance(value, str):
            try:
                start_text, end_text = value.split("-", 1)
                start, end = int(start_text), int(end_text)
            except (TypeError, ValueError) as exc:
                raise RetentionError(
                    f"invalid exact duplicate candidate range: {value}") from exc
        else:
            start, end = int(value[0]), int(value[1])
        if start <= 0 or end < start:
            raise RetentionError(
                f"invalid exact duplicate candidate range: {start}-{end}")
        ranges.append((start, end))
    ranges.sort()
    for previous, current in zip(ranges, ranges[1:]):
        if current[0] <= previous[1]:
            raise RetentionError("exact duplicate candidate ranges must not overlap")
    return tuple(ranges)


def _candidate_scope(candidate_ranges: tuple[tuple[int, int], ...]) -> str | None:
    if not candidate_ranges:
        return None
    encoded = json.dumps(candidate_ranges, separators=(",", ":")).encode("ascii")
    return "ranges-sha256:" + hashlib.sha256(encoded).hexdigest()


def _exact_duplicate_checkpoint_state_name(
    *, cutoff: str, receipt_cutoff: str, upper_raw_id: int,
    candidate_scope: str | None,
) -> str:
    identity = json.dumps({
        "cutoff": cutoff, "receipt_cutoff": receipt_cutoff,
        "upper_raw_id": upper_raw_id, "candidate_scope": candidate_scope,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return EXACT_DUPLICATE_CHECKPOINT_VERSION + ":" + hashlib.sha256(identity).hexdigest()


def _matches_exact_duplicate_checkpoint(
    checkpoint: Any, *, cutoff: str, receipt_cutoff: str, upper_raw_id: int,
    candidate_scope: str | None,
) -> bool:
    return bool(
        isinstance(checkpoint, dict)
        and checkpoint.get("cutoff") == cutoff
        and checkpoint.get("receipt_cutoff") == receipt_cutoff
        and checkpoint.get("upper_raw_id") == upper_raw_id
        and checkpoint.get("candidate_scope") == candidate_scope)


def _store_exact_duplicate_checkpoint(
    connection: sqlite3.Connection, checkpoint: dict[str, Any],
) -> None:
    state_name = _exact_duplicate_checkpoint_state_name(
        cutoff=str(checkpoint["cutoff"]),
        receipt_cutoff=str(checkpoint["receipt_cutoff"]),
        upper_raw_id=int(checkpoint["upper_raw_id"]),
        candidate_scope=checkpoint.get("candidate_scope"),
    )
    connection.execute(
        """INSERT INTO station_ingest_cutover_state(
               cutover_name,migration_version,status,detail,updated_at)
           VALUES (?,?,'completed',?,?)
           ON CONFLICT(cutover_name) DO UPDATE SET
             migration_version=excluded.migration_version,
             status=excluded.status,detail=excluded.detail,updated_at=excluded.updated_at""",
        (state_name, EXACT_DUPLICATE_CHECKPOINT_VERSION,
         json.dumps({"exact_duplicate_checkpoint": checkpoint}, sort_keys=True), _now()),
    )


def _latest_exact_duplicate_checkpoint(
    connection: sqlite3.Connection, *, cutoff: str, receipt_cutoff: str, upper_raw_id: int,
    candidate_scope: str | None = None,
) -> dict[str, Any] | None:
    state_name = _exact_duplicate_checkpoint_state_name(
        cutoff=cutoff, receipt_cutoff=receipt_cutoff, upper_raw_id=upper_raw_id,
        candidate_scope=candidate_scope)
    state = connection.execute(
        "SELECT detail FROM station_ingest_cutover_state WHERE cutover_name=?",
        (state_name,),
    ).fetchone()
    if state is not None:
        try:
            checkpoint = json.loads(str(state[0])).get("exact_duplicate_checkpoint")
        except (TypeError, json.JSONDecodeError):
            checkpoint = None
        if _matches_exact_duplicate_checkpoint(
                checkpoint, cutoff=cutoff, receipt_cutoff=receipt_cutoff,
                upper_raw_id=upper_raw_id, candidate_scope=candidate_scope):
            return checkpoint

    cursor = connection.execute(
        """SELECT detail FROM station_ingest_retention_runs
           WHERE status='completed' AND cutoff_time=? ORDER BY id DESC""",
        (cutoff,),
    )
    while True:
        rows = cursor.fetchmany(200)
        if not rows:
            break
        for row in rows:
            try:
                checkpoint = json.loads(str(row[0])).get("exact_duplicate_checkpoint")
            except (TypeError, json.JSONDecodeError):
                continue
            if _matches_exact_duplicate_checkpoint(
                    checkpoint, cutoff=cutoff, receipt_cutoff=receipt_cutoff,
                    upper_raw_id=upper_raw_id, candidate_scope=candidate_scope):
                cursor.close()
                had_transaction = connection.in_transaction
                _store_exact_duplicate_checkpoint(connection, checkpoint)
                if not had_transaction and connection.in_transaction:
                    connection.commit()
                return checkpoint
    return None


def purge_exact_duplicate_batch(
    database: Path, *, cutoff: str, receipt_cutoff: str, upper_raw_id: int,
    cursor: int | None = None, batch_limit: int = 500, scan_limit: int = 2000,
    time_budget_seconds: float | None = None, archive_root: Path | None = None,
    candidate_ranges: tuple[tuple[int, int], ...] | None = None,
    validate_environment: bool = True,
) -> dict[str, Any]:
    """Delete one resumable batch of storage-identical rows already linked as duplicates."""
    batch_started_at = time.monotonic()
    cutoff = _parse_fixed_cutoff(cutoff, "cutoff")
    receipt_cutoff = _parse_fixed_cutoff(receipt_cutoff, "receipt cutoff")
    if cutoff >= receipt_cutoff:
        raise RetentionError("cutoff must be earlier than receipt cutoff")
    if upper_raw_id <= 0 or batch_limit <= 0 or scan_limit <= 0:
        raise RetentionError("upper raw id, batch limit and scan limit must be positive")
    if scan_limit < batch_limit:
        raise RetentionError("scan limit must be greater than or equal to batch limit")
    if time_budget_seconds is not None and time_budget_seconds <= 0:
        raise RetentionError("exact duplicate cleanup time budget must be positive")
    candidate_ranges = _normalize_candidate_ranges(candidate_ranges)
    if candidate_ranges and candidate_ranges[-1][1] > upper_raw_id:
        raise RetentionError("candidate range exceeds upper raw id")
    candidate_scope = _candidate_scope(candidate_ranges)
    scan_ranges = candidate_ranges or ((1, upper_raw_id),)
    deadline = None if time_budget_seconds is None else time.monotonic() + time_budget_seconds
    with closing(sqlite3.connect(str(database), timeout=30, isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        with _statement_deadline(connection, deadline, "exact_checkpoint_load"):
            if validate_environment:
                _require_exact_duplicate_cleanup_ready(connection)
                _require_exact_duplicate_cleanup_indexes(connection)
            checkpoint = _latest_exact_duplicate_checkpoint(
                connection, cutoff=cutoff, receipt_cutoff=receipt_cutoff,
                upper_raw_id=upper_raw_id, candidate_scope=candidate_scope)
        range_index = int(checkpoint.get("range_index", 0)) if checkpoint else 0
        initial_cursor = scan_ranges[0][0] - 1
        stored_cursor = int(checkpoint.get("cursor", initial_cursor)) if checkpoint else initial_cursor
        if cursor is not None and cursor != stored_cursor:
            raise RetentionError(
                f"cursor does not match committed checkpoint: expected {stored_cursor}")
        cursor = stored_cursor
        totals = {
            "receipts_deleted": int(checkpoint.get("receipts_deleted", 0)) if checkpoint else 0,
            "scanned": int(checkpoint.get("scanned", 0)) if checkpoint else 0,
            "exact_candidates": int(checkpoint.get("exact_candidates", 0)) if checkpoint else 0,
            "deleted": int(checkpoint.get("deleted", 0)) if checkpoint else 0,
            "retained_protected": int(checkpoint.get("retained_protected", 0)) if checkpoint else 0,
            "retained_not_exact": int(checkpoint.get("retained_not_exact", 0)) if checkpoint else 0,
            "errors_archived": int(checkpoint.get("errors_archived", 0)) if checkpoint else 0,
        }
        if checkpoint and checkpoint.get("complete"):
            return dict(checkpoint, retry_required=False)
        committed_totals = dict(totals)
        pending_ids = sorted({
            int(raw_id) for raw_id in (checkpoint or {}).get("pending_raw_ids", [])
            if int(raw_id) > 0
        })
        search_state = (checkpoint or {}).get("representative_search", {})
        if not isinstance(search_state, dict):
            search_state = {}

        window_rows: list[sqlite3.Row] = []
        if not pending_ids:
            while range_index < len(scan_ranges):
                range_start, range_end = scan_ranges[range_index]
                if cursor < range_start - 1 or cursor > range_end:
                    cursor = range_start - 1
                with _statement_deadline(connection, deadline, "exact_window_scan"):
                    window_rows = connection.execute(
                        """SELECT id FROM ingest_raw_frames
                           WHERE id>? AND id<=? ORDER BY id LIMIT ?""",
                        (cursor, range_end, scan_limit),
                    ).fetchall()
                if window_rows:
                    break
                range_index += 1
                if range_index < len(scan_ranges):
                    cursor = scan_ranges[range_index][0] - 1
        window_ids = [int(row["id"]) for row in window_rows]
        combined_ids = sorted(set(pending_ids).union(window_ids))
        with _statement_deadline(connection, deadline, "exact_identity_scan"):
            exact_rows = _exact_duplicate_rows(connection, combined_ids, cutoff)
        exact_by_id = {int(row["id"]): row for row in exact_rows}
        exact_ids = sorted(exact_by_id)

        with _statement_deadline(connection, deadline, "exact_open_error_scan"):
            batch_errors = _open_errors_for_raw_ids(connection, combined_ids)
        errors_by_raw: dict[int, list[sqlite3.Row]] = {}
        candidate_errors: list[sqlite3.Row] = []
        minimums: dict[tuple[str, str], tuple[str, int]] = {}
        for error in batch_errors:
            raw_id = int(error["raw_frame_id"])
            errors_by_raw.setdefault(raw_id, []).append(error)
            if raw_id not in exact_by_id:
                continue
            candidate_errors.append(error)
            group = (str(error["source_key"]), str(error["error_type"]))
            identity = (str(error["created_at"]), int(error["id"]))
            minimums[group] = max(minimums.get(group, identity), identity)

        representatives, representative_plans, next_search_state, exhausted_groups, search_stats = (
            _bounded_error_representatives(
                connection, minimums, batch_errors, scan_limit=scan_limit,
                deadline=deadline, search_state=search_state)
        )
        candidate_errors_by_group: dict[tuple[str, str], list[sqlite3.Row]] = {}
        for error in candidate_errors:
            group = (str(error["source_key"]), str(error["error_type"]))
            candidate_errors_by_group.setdefault(group, []).append(error)
        for group in exhausted_groups:
            candidates = candidate_errors_by_group.get(group, [])
            if candidates:
                representatives[group] = max(
                    candidates, key=lambda row: (str(row["created_at"]), int(row["id"])))
        unresolved_groups = set(minimums).difference(representatives)
        representative_pending_ids = {
            raw_id for raw_id, errors in errors_by_raw.items()
            if raw_id in exact_by_id and any(
                (str(error["source_key"]), str(error["error_type"])) in unresolved_groups
                for error in errors)
        }
        representative_raw_ids = {
            int(row["raw_frame_id"]) for row in representatives.values()
        }

        with _statement_deadline(connection, deadline, "exact_non_error_protection"):
            non_error_protected = _exact_cleanup_non_error_protection(
                connection, exact_ids, receipt_cutoff)

        planned_deletable: list[int] = []
        planned_errors: list[sqlite3.Row] = []
        retained_protected: set[int] = set()
        retained_error_representatives: set[int] = set()
        next_pending = set(pending_ids)
        processed_cursor = cursor
        stop_reason = "scan_window"
        targets = pending_ids + window_ids
        window_id_set = set(window_ids)
        for raw_id in targets:
            if _deadline_expired(deadline):
                raise _StatementDeadlineExceeded("exact_batch_plan")
            next_pending.discard(raw_id)
            exact = exact_by_id.get(raw_id)
            if exact is None:
                if raw_id in window_id_set:
                    processed_cursor = raw_id
                continue
            if raw_id in non_error_protected:
                retained_protected.add(raw_id)
                if raw_id in window_id_set:
                    processed_cursor = raw_id
                continue
            raw_errors = errors_by_raw.get(raw_id, [])
            raw_groups = {
                (str(error["source_key"]), str(error["error_type"])) for error in raw_errors
            }
            if raw_groups.intersection(unresolved_groups):
                next_pending.add(raw_id)
                if raw_id in window_id_set:
                    processed_cursor = raw_id
                continue
            if raw_id in representative_raw_ids:
                retained_protected.add(raw_id)
                retained_error_representatives.add(raw_id)
                if raw_id in window_id_set:
                    processed_cursor = raw_id
                continue
            if (len(planned_deletable) >= batch_limit
                    or len(planned_errors) + len(raw_errors) > batch_limit):
                if raw_id in pending_ids:
                    next_pending.add(raw_id)
                stop_reason = "batch_limit"
                break
            planned_deletable.append(raw_id)
            planned_errors.extend(raw_errors)
            if raw_id in window_id_set:
                processed_cursor = raw_id

        if not pending_ids and not window_rows:
            processed_cursor = upper_raw_id

        blocked_raw_ids: list[int] = []
        evidence_by_raw: dict[int, tuple[int, bytes]] = {}
        error_raw_ids = sorted({int(error["raw_frame_id"]) for error in planned_errors})
        if error_raw_ids and archive_root is None:
            blocked_raw_ids = error_raw_ids
            stop_reason = "archive_root_unavailable"
        elif error_raw_ids:
            missing_online_ids: list[int] = []
            for raw_id in error_raw_ids:
                exact = exact_by_id[raw_id]
                evidence = _online_raw_evidence(exact)
                if evidence is None:
                    missing_online_ids.extend(
                        [int(exact["duplicate_of_raw_frame_id"]), raw_id])
                else:
                    evidence_by_raw[raw_id] = evidence
            archived_evidence = _archived_raw_evidence_batch(
                connection, sorted(set(missing_online_ids)), deadline=deadline)
            for raw_id in error_raw_ids:
                if raw_id in evidence_by_raw:
                    continue
                exact = exact_by_id[raw_id]
                for source_id in (int(exact["duplicate_of_raw_frame_id"]), raw_id):
                    payload = archived_evidence.get(source_id)
                    if payload is not None and hashlib.sha256(payload).hexdigest() == str(
                            exact["frame_sha256"]):
                        evidence_by_raw[raw_id] = (source_id, payload)
                        break
                if raw_id not in evidence_by_raw:
                    blocked_raw_ids.append(raw_id)
            if blocked_raw_ids:
                stop_reason = "archive_evidence_unavailable"

        if blocked_raw_ids:
            planned_deletable = []
            planned_errors = []
            evidence_by_raw = {}
            retained_protected = set()
            retained_error_representatives = set()
            next_pending = set(pending_ids)
            processed_cursor = cursor

        planned_errors.sort(key=lambda row: int(row["id"]))
        processed_window_ids = [raw_id for raw_id in window_ids if raw_id <= processed_cursor]
        processed_new_exact_ids = [
            raw_id for raw_id in exact_ids
            if raw_id in window_id_set and raw_id <= processed_cursor
        ]
        planned_error_ids = {int(error["id"]) for error in planned_errors}
        used_representatives = {
            (str(error["source_key"]), str(error["error_type"])): representatives[
                (str(error["source_key"]), str(error["error_type"]))]
            for error in planned_errors
        }

        evidence_records_by_key: dict[str, dict[str, Any]] = {}
        error_records: list[dict[str, Any]] = []
        for error in sorted(planned_errors, key=lambda row: int(row["id"])):
            raw_id = int(error["raw_frame_id"])
            exact = exact_by_id[raw_id]
            evidence_source_id, payload = evidence_by_raw[raw_id]
            evidence_key = (
                f"{int(exact['duplicate_of_raw_frame_id'])}:{str(exact['frame_sha256'])}")
            evidence_records_by_key.setdefault(evidence_key, {
                "record_type": "raw_evidence",
                "evidence_key": evidence_key,
                "source_raw_frame_id": evidence_source_id,
                "canonical_raw_frame_id": int(exact["duplicate_of_raw_frame_id"]),
                "frame_sha256": str(exact["frame_sha256"]),
                "frame_b64": base64.b64encode(payload).decode("ascii"),
            })
            record = dict(error)
            record.update({
                "record_type": "ingest_error",
                "evidence_key": evidence_key,
                "evidence_source_raw_frame_id": evidence_source_id,
                "canonical_raw_frame_id": int(exact["duplicate_of_raw_frame_id"]),
                "duplicate_raw_frame_id": raw_id,
            })
            error_records.append(record)

        archive_path = None
        archive_digest = None
        archive_records: list[dict[str, Any]] = []
        archive_created = False
        if error_records:
            archive_path, archive_digest, archive_records, archive_created = (
                _write_exact_duplicate_error_bundle(
                    Path(archive_root), error_records,
                    list(evidence_records_by_key.values()), deadline=deadline))

        try:
            _begin_immediate_with_deadline(connection, deadline, "exact_write_lock")
            with _statement_deadline(connection, deadline, "exact_write_batch"):
                receipt_ids: list[int] = []
                for identifiers in _batches(processed_window_ids):
                    marks = ",".join("?" * len(identifiers))
                    receipt_ids.extend(int(row[0]) for row in connection.execute(
                        f"""SELECT rowid FROM station_ingest_recent_receipts
                            WHERE raw_frame_id IN ({marks}) AND received_at<?""",
                        (*identifiers, receipt_cutoff),
                    ))
                for identifiers in _batches(receipt_ids):
                    marks = ",".join("?" * len(identifiers))
                    connection.execute(
                        f"DELETE FROM station_ingest_recent_receipts WHERE rowid IN ({marks})",
                        identifiers)

                current_exact = _exact_duplicate_rows(
                    connection, planned_deletable, cutoff)
                if [dict(row) for row in current_exact] != [
                        dict(exact_by_id[raw_id]) for raw_id in planned_deletable]:
                    raise RetentionError("exact duplicate rows changed before cleanup commit")
                current_errors = _open_errors_for_raw_ids(connection, planned_deletable)
                if [dict(row) for row in current_errors] != [
                        dict(row) for row in planned_errors]:
                    raise RetentionError("ingest error rows changed before archive commit")
                current_non_error_protected = _exact_cleanup_non_error_protection(
                    connection, planned_deletable, receipt_cutoff)
                if current_non_error_protected:
                    raise RetentionError("raw frame protection changed before cleanup commit")

                expected_representatives = {
                    int(row["id"]): dict(row) for row in used_representatives.values()
                }
                current_representatives = {
                    int(row["id"]): dict(row) for row in _open_errors_by_ids(
                        connection, sorted(expected_representatives))
                }
                if current_representatives != expected_representatives:
                    raise RetentionError("error representative changed before cleanup commit")

                if error_records:
                    _register_detail_archive_part(
                        connection, "ingest_error", archive_records,
                        archive_path, archive_digest, source_records=error_records)
                    for identifiers in _batches(sorted(planned_error_ids)):
                        marks = ",".join("?" * len(identifiers))
                        connection.execute(
                            f"DELETE FROM ingest_errors WHERE id IN ({marks})", identifiers)
                remaining_protected = _protected_raw_ids(connection, planned_deletable)
                if remaining_protected:
                    raise RetentionError(
                        "raw frame became protected before cleanup commit: "
                        + ",".join(str(raw_id) for raw_id in sorted(remaining_protected)[:20]))
                deleted = _delete_confirmed_duplicate_rows(connection, planned_deletable)
                if deleted != len(planned_deletable):
                    raise RetentionError("exact duplicate delete count changed before commit")

                totals["receipts_deleted"] += len(receipt_ids)
                totals["scanned"] += len(processed_window_ids)
                totals["exact_candidates"] += len(processed_new_exact_ids)
                totals["deleted"] += deleted
                totals["retained_protected"] += len(retained_protected)
                totals["retained_not_exact"] += (
                    len(processed_window_ids) - len(processed_new_exact_ids))
                totals["errors_archived"] += len(planned_error_ids)
                pending_result = sorted(next_pending)
                representative_pending_result = sorted(
                    next_pending.intersection(representative_pending_ids))
                complete = (
                    range_index >= len(scan_ranges) and not pending_result
                    if candidate_ranges else
                    processed_cursor >= upper_raw_id and not pending_result
                )
                if complete:
                    stop_reason = "upper_bound_reached"
                elif stop_reason == "scan_window" and pending_result:
                    stop_reason = "representative_search_pending"
                result = {
                    "cutoff": cutoff, "receipt_cutoff": receipt_cutoff,
                    "upper_raw_id": upper_raw_id, "cursor": processed_cursor,
                    "candidate_scope": candidate_scope,
                    "candidate_ranges": [list(item) for item in candidate_ranges],
                    "range_index": range_index,
                    **totals, "phase": "raw", "complete": complete,
                    "retry_required": not complete, "stop_reason": stop_reason,
                    "archive_blocked_count": len(blocked_raw_ids),
                    "archive_blocked_raw_ids": blocked_raw_ids[:20],
                    "pending_raw_ids": pending_result,
                    "pending_count": len(pending_result),
                    "representative_pending_count": len(representative_pending_result),
                    "representative_pending_raw_ids": representative_pending_result[:20],
                    "representative_search": next_search_state,
                    "representative_search_groups": search_stats["groups"],
                    "representative_search_searched_groups": search_stats["searched_groups"],
                    "representative_search_page_limit": search_stats["page_limit"],
                    "representative_search_rows": search_stats["rows"],
                    "retained_error_representative_count": len(
                        retained_error_representatives),
                    "retained_error_representative_raw_ids": sorted(
                        retained_error_representatives)[:20],
                    "batch_deleted": deleted,
                    "batch_errors_archived": len(planned_error_ids),
                    "batch_evidence_records": len(evidence_records_by_key),
                    "error_representative_query_plans": representative_plans,
                    "batch_elapsed_seconds": round(time.monotonic() - batch_started_at, 3),
                }
                connection.execute(
                    """INSERT INTO station_ingest_retention_runs(
                           cutoff_time,archived_parse_attempts,archived_errors,
                           archived_error_summaries,deleted_raw_frames,
                           retained_protected_frames,status,detail,created_at)
                       VALUES (?,0,0,0,?,?,'completed',?,?)""",
                    (cutoff, deleted, len(retained_protected),
                     json.dumps({"exact_duplicate_checkpoint": result}, sort_keys=True), _now()),
                )
                _store_exact_duplicate_checkpoint(connection, result)
            if _deadline_expired(deadline):
                raise _StatementDeadlineExceeded("exact_commit")
            connection.commit()
            return result
        except Exception as exc:
            if connection.in_transaction:
                connection.rollback()
            if archive_created and archive_path is not None:
                registered = connection.execute(
                    "SELECT 1 FROM station_ingest_detail_archive_parts WHERE archive_path=?",
                    (str(archive_path),),
                ).fetchone()
                if registered is None:
                    archive_path.unlink(missing_ok=True)
            if isinstance(exc, _StatementDeadlineExceeded):
                exc.checkpoint = {
                    "cutoff": cutoff, "receipt_cutoff": receipt_cutoff,
                    "upper_raw_id": upper_raw_id, "cursor": cursor,
                    "candidate_scope": candidate_scope,
                    "candidate_ranges": [list(item) for item in candidate_ranges],
                    "range_index": range_index, **committed_totals,
                }
            raise


def run_exact_duplicate_cleanup_job(
    database: Path, *, cutoff: str, receipt_cutoff: str, upper_raw_id: int,
    candidate_ranges: tuple[tuple[int, int], ...], batch_limit: int = 500,
    scan_limit: int = 2000, time_budget_seconds: float | None = None,
    archive_root: Path | None = None,
) -> dict[str, Any]:
    """Run scoped exact cleanup batches in one process with one absolute deadline."""
    started_at = time.monotonic()
    candidate_ranges = _normalize_candidate_ranges(candidate_ranges)
    if not candidate_ranges:
        raise RetentionError("exact duplicate cleanup job requires candidate ranges")
    if time_budget_seconds is not None and time_budget_seconds <= 0:
        raise RetentionError("exact duplicate cleanup job time budget must be positive")
    deadline = None if time_budget_seconds is None else started_at + time_budget_seconds
    with closing(sqlite3.connect(str(database), timeout=30)) as connection:
        _require_exact_duplicate_cleanup_ready(connection)
        _require_exact_duplicate_cleanup_indexes(connection)
        indexes = {
            "created": [], "verified": sorted(CLEANUP_INDEXES),
            "query_plans": _exact_duplicate_cleanup_query_plans(
                connection, deadline=deadline),
        }
    batch_summaries: list[dict[str, Any]] = []
    previous_progress: tuple[Any, ...] | None = None
    while True:
        if deadline is not None and _deadline_expired(deadline):
            checkpoint = batch_summaries[-1] if batch_summaries else {
                "candidate_scope": _candidate_scope(candidate_ranges), "cursor": 0}
            raise _StatementDeadlineExceeded("exact_job", checkpoint)
        remaining = (
            EXACT_DUPLICATE_BATCH_TIME_BUDGET_SECONDS if deadline is None else
            min(EXACT_DUPLICATE_BATCH_TIME_BUDGET_SECONDS,
                max(0.001, deadline - time.monotonic())))
        result = purge_exact_duplicate_batch(
            database, cutoff=cutoff, receipt_cutoff=receipt_cutoff,
            upper_raw_id=upper_raw_id, batch_limit=batch_limit,
            scan_limit=scan_limit, time_budget_seconds=remaining,
            archive_root=archive_root, candidate_ranges=candidate_ranges,
            validate_environment=False)
        batch_summaries.append({
            "cursor": result["cursor"], "range_index": result["range_index"],
            "batch_deleted": result["batch_deleted"],
            "batch_errors_archived": result["batch_errors_archived"],
            "scanned": result["scanned"], "deleted": result["deleted"],
            "stop_reason": result["stop_reason"],
            "elapsed_seconds": result["batch_elapsed_seconds"],
        })
        progress = (
            result["range_index"], result["cursor"], result["scanned"],
            result["deleted"], result["errors_archived"],
            tuple(result["pending_raw_ids"]),
            json.dumps(result["representative_search"], sort_keys=True),
        )
        hard_stop = result["stop_reason"] in {
            "archive_root_unavailable", "archive_evidence_unavailable"}
        if result["complete"] or hard_stop or progress == previous_progress:
            return {
                "candidate_scope": result["candidate_scope"],
                "candidate_ranges": result["candidate_ranges"],
                "indexes": indexes, "batches": len(batch_summaries),
                "batch_summaries": batch_summaries, "result": result,
                "elapsed_seconds": round(time.monotonic() - started_at, 3),
                "retry_required": not result["complete"],
            }
        previous_progress = progress

def reduce_confirmed_minute_duplicates(
    database: Path, *, after_id: int = 0, batch_limit: int = 500,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Remove one resumable batch of confirmed old 2011 repeats without archiving them."""
    if batch_limit <= 0:
        raise RetentionError("duplicate reduction batch limit must be positive")
    now = now or datetime.now(timezone.utc)
    cutoff = _cutoff(now, HOT_RELATION_RETENTION)
    with closing(sqlite3.connect(str(database), timeout=30, isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        rows = connection.execute(
            """SELECT raw.id,raw.endpoint_id,raw.station_code,raw.received_at,
                      raw.frame_sha256,raw.raw_frame
               FROM ingest_raw_frames raw
               JOIN ingest_frame_protocols protocol ON protocol.raw_frame_id=raw.id
               WHERE raw.id>? AND raw.received_at<? AND raw.endpoint_id IS NOT NULL
                 AND raw.authentication_status='authenticated'
                 AND raw.persistence_state='persisted'
                 AND protocol.protocol_family='hj212'
               ORDER BY raw.id LIMIT ?""",
            (after_id, cutoff, batch_limit),
        ).fetchall()
        if not rows:
            return {
                "cursor_raw_id": after_id, "scanned": 0, "eligible": 0,
                "duplicate_candidates": 0, "deleted": 0, "retained_protected": 0,
                "retained_unclassified": 0, "canonical_kept": 0, "complete": True,
                "cutoff": cutoff,
            }

        connection.execute("BEGIN IMMEDIATE")
        resolver = HJ212SlotIdentityResolver(connection)
        grouped_rows: dict[tuple[int, str, str], list[sqlite3.Row]] = {}
        eligible = 0
        canonical_kept = 0
        retained_unclassified = 0
        for row in rows:
            try:
                frame = parse_hj212_frame(bytes(row["raw_frame"]))
            except HJ212FrameError:
                retained_unclassified += 1
                continue
            identity = resolver.resolve(frame, int(row["endpoint_id"]))
            if identity is None:
                retained_unclassified += 1
                continue
            eligible += 1
            slot_started_at, content_sha256 = identity
            grouped_rows.setdefault(
                (int(row["endpoint_id"]), slot_started_at, content_sha256), []
            ).append(row)

        candidates: list[int] = []
        for (endpoint_id, slot_started_at, content_sha256), group in grouped_rows.items():
            summary = connection.execute(
                """SELECT canonical_raw_frame_id FROM station_ingest_slot_summaries
                   WHERE endpoint_id=? AND command='2011'
                     AND slot_started_at=? AND content_sha256=?""",
                (endpoint_id, slot_started_at, content_sha256),
            ).fetchone()
            if summary is None:
                canonical = min(group, key=lambda item: int(item["id"]))
                connection.execute(
                    """INSERT INTO station_ingest_slot_summaries(
                           endpoint_id,station_code,command,slot_started_at,content_sha256,
                           canonical_raw_frame_id,canonical_frame_sha256,receipt_count,
                           first_received_at,last_received_at,last_authentication_status,
                           created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,1,?,?,?,?,?)""",
                    (
                        endpoint_id, canonical["station_code"], "2011", slot_started_at,
                        content_sha256, canonical["id"], canonical["frame_sha256"],
                        min(str(item["received_at"]) for item in group),
                        max(str(item["received_at"]) for item in group),
                        "authenticated", _now(), _now(),
                    ),
                )
                canonical_raw_id = int(canonical["id"])
            else:
                canonical_raw_id = int(summary["canonical_raw_frame_id"])
                connection.execute(
                    """UPDATE station_ingest_slot_summaries
                       SET first_received_at=MIN(first_received_at,?),
                           last_received_at=MAX(last_received_at,?),updated_at=?
                       WHERE endpoint_id=? AND command='2011'
                         AND slot_started_at=? AND content_sha256=?""",
                    (
                        min(str(item["received_at"]) for item in group),
                        max(str(item["received_at"]) for item in group), _now(),
                        endpoint_id, slot_started_at, content_sha256,
                    ),
                )
            canonical_kept += sum(
                1 for item in group if int(item["id"]) == canonical_raw_id)
            candidates.extend(
                int(item["id"]) for item in group if int(item["id"]) != canonical_raw_id)

        protected = _protected_raw_ids(connection, candidates)
        deletable = [raw_id for raw_id in candidates if raw_id not in protected]
        deleted = _delete_confirmed_duplicate_rows(connection, deletable)
        cursor = int(rows[-1]["id"])
        detail = {
            "rule": "authenticated-hj212-2011-same-endpoint-slot-normalized-content",
            "range": [int(rows[0]["id"]), cursor],
            "scanned": len(rows), "eligible": eligible,
            "duplicate_candidates": len(candidates), "deleted": deleted,
            "retained_protected": len(protected),
            "retained_unclassified": retained_unclassified,
            "historical_summary_count_semantics": "canonical-only; live receipts increment after cutover",
        }
        connection.execute(
            """INSERT INTO station_ingest_retention_runs(
                   cutoff_time,archived_parse_attempts,archived_errors,archived_error_summaries,
                   deleted_raw_frames,retained_protected_frames,status,detail,created_at)
               VALUES (?,0,0,0,?,?, 'completed',?,?)""",
            (cutoff, deleted, len(protected), json.dumps(detail, sort_keys=True), _now()),
        )
        connection.commit()
        return {
            "cursor_raw_id": cursor, "scanned": len(rows), "eligible": eligible,
            "duplicate_candidates": len(candidates), "deleted": deleted,
            "retained_protected": len(protected),
            "retained_unclassified": retained_unclassified,
            "canonical_kept": canonical_kept,
            "complete": len(rows) < batch_limit, "cutoff": cutoff,
        }


def _protect_active_dedup_keys(
    connection: sqlite3.Connection, raw_ids: list[int], protected_ids: set[int],
) -> int:
    identifiers = [raw_id for raw_id in raw_ids if raw_id in protected_ids]
    if not identifiers:
        return 0
    rows = []
    for batch in _batches(identifiers):
        marks = ",".join("?" * len(batch))
        rows.extend(connection.execute(
            f"""SELECT raw.id,raw.endpoint_id,raw.logical_key_sha256,raw.received_at,
                       evidence.id AS evidence_ref_id
                FROM ingest_raw_frames raw
                LEFT JOIN monitoring_archive_evidence_refs evidence
                  ON evidence.source_raw_frame_id=raw.id
                WHERE raw.id IN ({marks}) AND raw.endpoint_id IS NOT NULL
                  AND raw.logical_key_sha256 IS NOT NULL""",
            batch,
        ).fetchall())
    updated_at = _now()
    connection.executemany(
        """INSERT INTO station_ingest_dedup_keys(
               endpoint_id,logical_key_sha256,canonical_raw_frame_id,canonical_evidence_ref_id,
               first_received_at,last_received_at,expires_at,protected,updated_at)
           VALUES (?,?,?,?,?,?,NULL,1,?)
           ON CONFLICT(endpoint_id,logical_key_sha256) DO UPDATE SET
               protected=1,expires_at=NULL,
               first_received_at=MIN(station_ingest_dedup_keys.first_received_at,
                                     excluded.first_received_at),
               last_received_at=MAX(station_ingest_dedup_keys.last_received_at,
                                    excluded.last_received_at),
               canonical_evidence_ref_id=COALESCE(
                   station_ingest_dedup_keys.canonical_evidence_ref_id,
                   excluded.canonical_evidence_ref_id),
               updated_at=excluded.updated_at""",
        ((row["endpoint_id"], row["logical_key_sha256"], row["id"], row["evidence_ref_id"],
          row["received_at"], row["received_at"], updated_at) for row in rows),
    )
    return len(rows)


def _latest_raw_prune_checkpoint(
    connection: sqlite3.Connection,
) -> dict[str, Any] | None:
    rows = connection.execute(
        "SELECT detail FROM station_ingest_retention_runs ORDER BY id DESC LIMIT 50"
    ).fetchall()
    for row in rows:
        try:
            checkpoint = json.loads(row[0]).get("raw_prune_checkpoint")
        except (TypeError, ValueError, AttributeError):
            continue
        if not isinstance(checkpoint, dict) or not checkpoint.get("cutoff"):
            continue
        try:
            cursor = int(checkpoint["cursor"])
            upper_raw_id = int(checkpoint["upper_raw_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if cursor < 0 or upper_raw_id < 0 or cursor > upper_raw_id:
            continue
        if cursor == upper_raw_id:
            return None
        return checkpoint
    return None


def _delete_unprotected_raw_batch(
    database: Path, cutoff: str, *, now: str, batch_limit: int,
    deadline: float | None = None, scan_limit: int = RAW_PRUNE_SCAN_LIMIT,
) -> tuple[int, int, dict[str, Any], int]:
    if scan_limit <= 0:
        raise RetentionError("raw prune scan limit must be positive")
    deleted = 0
    retained = 0
    checkpoint: dict[str, Any] | None = None
    checkpoint_run_id = 0
    with closing(sqlite3.connect(str(database), timeout=10, isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with _statement_deadline(connection, deadline, "raw_checkpoint_load"):
                checkpoint = _latest_raw_prune_checkpoint(connection)
                if checkpoint is None:
                    upper_raw_id = int(connection.execute(
                        "SELECT COALESCE(MAX(id),0) FROM ingest_raw_frames"
                    ).fetchone()[0])
                    checkpoint = {
                        "cutoff": cutoff, "cursor": 0, "upper_raw_id": upper_raw_id,
                        "scanned": 0, "deleted": 0, "retained": 0,
                        "stop_reason": "new_window",
                    }
                effective_cutoff = str(checkpoint["cutoff"])
                cursor = int(checkpoint["cursor"])
                upper_raw_id = int(checkpoint["upper_raw_id"])
                window_rows = connection.execute(
                    """SELECT id,received_at FROM ingest_raw_frames
                       WHERE id>? AND id<=? ORDER BY id LIMIT ?""",
                    (cursor, upper_raw_id, scan_limit),
                ).fetchall()
                window_ids = [int(row["id"]) for row in window_rows]
                candidate_ids = [
                    int(row["id"]) for row in window_rows
                    if str(row["received_at"]) < effective_cutoff
                ]
                if candidate_ids:
                    eligible_ids: set[int] = set()
                    for batch in _batches(candidate_ids):
                        marks = ",".join("?" * len(batch))
                        eligible_ids.update(int(row[0]) for row in connection.execute(
                            f"""SELECT raw.id FROM ingest_raw_frames raw
                                JOIN monitoring_archive_evidence_refs evidence
                                  ON evidence.source_raw_frame_id=raw.id
                                WHERE raw.id IN ({marks}) AND raw.persistence_state='persisted'
                                  AND NOT EXISTS (SELECT 1 FROM ingest_parse_attempts attempt
                                                  WHERE attempt.raw_frame_id=raw.id)
                                  AND NOT EXISTS (SELECT 1 FROM ingest_errors error
                                                  WHERE error.raw_frame_id=raw.id)""",
                            batch,
                        ))
                    protected_ids = _protected_raw_ids(connection, candidate_ids)
                else:
                    eligible_ids = set()
                    protected_ids = set()

            planned_deletable: list[int] = []
            processed_ids: list[int] = []
            for raw_id in window_ids:
                processed_ids.append(raw_id)
                if raw_id in eligible_ids and raw_id not in protected_ids:
                    planned_deletable.append(raw_id)
                    if len(planned_deletable) >= batch_limit:
                        break
            if processed_ids:
                processed_cursor = processed_ids[-1]
            else:
                processed_cursor = upper_raw_id
            candidate_id_set = set(candidate_ids)
            processed_candidate_ids = [
                raw_id for raw_id in processed_ids if raw_id in candidate_id_set
            ]
            if processed_cursor >= upper_raw_id:
                stop_reason = "upper_bound_reached"
            elif len(planned_deletable) >= batch_limit:
                stop_reason = "delete_batch_limit"
            else:
                stop_reason = "scan_window_limit"

            _begin_immediate_with_deadline(connection, deadline, "raw_write_lock")
            with _statement_deadline(connection, deadline, "raw_write_revalidation"):
                write_protected_ids = _protected_raw_ids(connection, processed_candidate_ids)
                _protect_active_dedup_keys(
                    connection, processed_candidate_ids, write_protected_ids)
                deletable_rows = []
                for batch in _batches(planned_deletable):
                    marks = ",".join("?" * len(batch))
                    deletable_rows.extend(connection.execute(
                        f"""SELECT raw.id,evidence.id AS evidence_ref_id
                            FROM ingest_raw_frames raw
                            JOIN monitoring_archive_evidence_refs evidence
                              ON evidence.source_raw_frame_id=raw.id
                            WHERE raw.id IN ({marks}) AND raw.persistence_state='persisted'
                              AND NOT EXISTS (SELECT 1 FROM ingest_parse_attempts attempt
                                              WHERE attempt.raw_frame_id=raw.id)
                              AND NOT EXISTS (SELECT 1 FROM ingest_errors error
                                              WHERE error.raw_frame_id=raw.id)""",
                        batch,
                    ).fetchall())
                deletable_rows = [
                    row for row in deletable_rows if int(row["id"]) not in write_protected_ids
                ]
                retained = len(set(processed_candidate_ids) & write_protected_ids)
                for batch in _batches(deletable_rows):
                    identifiers = [int(row["id"]) for row in batch]
                    evidence_refs = {
                        int(row["id"]): int(row["evidence_ref_id"]) for row in batch
                    }
                    marks = ",".join("?" * len(identifiers))
                    batch_ids = [int(item[0]) for item in connection.execute(
                        f"SELECT id FROM observation_batches WHERE raw_frame_id IN ({marks})",
                        identifiers,
                    )]
                    if batch_ids:
                        batch_marks = ",".join("?" * len(batch_ids))
                        connection.execute(
                            f"""DELETE FROM monitoring_quality_issues
                                WHERE observation_batch_id IN ({batch_marks}) AND status!='open'""",
                            batch_ids,
                        )
                        connection.execute(
                            f"""DELETE FROM monitoring_status_events
                                WHERE observation_batch_id IN ({batch_marks})""",
                            batch_ids,
                        )
                        connection.execute(
                            f"""DELETE FROM observation_values
                                WHERE observation_batch_id IN ({batch_marks})""",
                            batch_ids,
                        )
                        connection.execute(
                            f"DELETE FROM observation_batches WHERE id IN ({batch_marks})",
                            batch_ids,
                        )
                    connection.execute(
                        f"""DELETE FROM monitoring_quality_issues
                            WHERE raw_frame_id IN ({marks}) AND status!='open'""",
                        identifiers,
                    )
                    duplicate_rows = connection.execute(
                        f"""SELECT id,duplicate_of_raw_frame_id FROM ingest_raw_frames
                            WHERE duplicate_of_raw_frame_id IN ({marks})""",
                        identifiers,
                    ).fetchall()
                    link_created_at = _now()
                    connection.executemany(
                        """INSERT OR IGNORE INTO station_ingest_duplicate_links(
                               raw_frame_id,canonical_raw_frame_id,canonical_evidence_ref_id,
                               created_at) VALUES (?,?,?,?)""",
                        ((duplicate[0], duplicate[1], evidence_refs[int(duplicate[1])],
                          link_created_at) for duplicate in duplicate_rows),
                    )
                    connection.execute(
                        f"""UPDATE ingest_raw_frames SET duplicate_of_raw_frame_id=NULL
                            WHERE duplicate_of_raw_frame_id IN ({marks})""",
                        identifiers,
                    )
                    for table, column in (
                        ("station_ingest_duplicate_links", "raw_frame_id"),
                        ("monitoring_raw_archive_part_frames", "raw_frame_id"),
                        ("ingest_frame_protocols", "raw_frame_id"),
                        ("station_ingest_recent_receipts", "raw_frame_id"),
                        ("ingest_raw_frames", "id"),
                    ):
                        connection.execute(
                            f"DELETE FROM {table} WHERE {column} IN ({marks})", identifiers,
                        )
                    deleted += len(identifiers)
                expired_dedup_rowids = [int(row[0]) for row in connection.execute(
                    """SELECT rowid FROM station_ingest_dedup_keys
                       WHERE protected=0 AND expires_at IS NOT NULL
                         AND julianday(expires_at)<julianday(?) ORDER BY rowid LIMIT ?""",
                    (now, batch_limit),
                )]
                if expired_dedup_rowids:
                    marks = ",".join("?" * len(expired_dedup_rowids))
                    connection.execute(
                        f"DELETE FROM station_ingest_dedup_keys WHERE rowid IN ({marks})",
                        expired_dedup_rowids,
                    )
                checkpoint = {
                    "cutoff": effective_cutoff,
                    "cursor": processed_cursor,
                    "upper_raw_id": upper_raw_id,
                    "scanned": int(checkpoint.get("scanned", 0)) + len(processed_ids),
                    "deleted": int(checkpoint.get("deleted", 0)) + deleted,
                    "retained": int(checkpoint.get("retained", 0)) + retained,
                    "stop_reason": stop_reason,
                }
                checkpoint_run_id = int(connection.execute(
                    """INSERT INTO station_ingest_retention_runs(
                           cutoff_time,archived_parse_attempts,archived_errors,
                           archived_error_summaries,deleted_raw_frames,
                           retained_protected_frames,status,detail,created_at)
                       VALUES (?,0,0,0,0,0,'completed',?,?)""",
                    (effective_cutoff, json.dumps({
                        "status": "checkpoint",
                        "raw_prune_checkpoint": checkpoint,
                    }, sort_keys=True), _now()),
                ).lastrowid)
                connection.commit()
        except _StatementDeadlineExceeded as exc:
            if connection.in_transaction:
                connection.rollback()
            current = dict(checkpoint or {
                "cutoff": cutoff, "cursor": 0, "upper_raw_id": 0,
                "scanned": 0, "deleted": 0, "retained": 0,
            })
            current["stop_reason"] = str(exc)
            exc.checkpoint = current
            raise
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
    return deleted, retained, checkpoint, checkpoint_run_id


def _delete_rowid_batch(
    database: Path, table: str, cutoff_column: str, cutoff: str, *, batch_limit: int,
    deadline: float | None = None,
) -> int:
    allowed = {
        ("station_ingest_recent_receipts", "received_at"),
        ("station_ingest_receipt_hourly", "last_received_at"),
        ("station_ingest_slot_summaries", "last_received_at"),
    }
    if (table, cutoff_column) not in allowed:
        raise RetentionError("retention batch target is not allowed")
    with closing(sqlite3.connect(str(database), timeout=5, isolation_level=None)) as connection:
        _begin_immediate_with_deadline(connection, deadline, f"{table}_lock")
        try:
            with _statement_deadline(connection, deadline, f"{table}_delete"):
                rowids = [int(row[0]) for row in connection.execute(
                    f"SELECT rowid FROM {table} WHERE {cutoff_column}<? ORDER BY rowid LIMIT ?",
                    (cutoff, batch_limit),
                )]
                if rowids:
                    marks = ",".join("?" * len(rowids))
                    connection.execute(f"DELETE FROM {table} WHERE rowid IN ({marks})", rowids)
            with _statement_deadline(connection, deadline, f"{table}_commit"):
                connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
    return len(rowids)


def _bounded_count(
    connection: sqlite3.Connection, query: str, parameters: tuple[Any, ...], limit: int,
) -> tuple[int, bool]:
    rows = connection.execute(
        f"SELECT 1 FROM ({query}) LIMIT ?", (*parameters, limit + 1),
    ).fetchall()
    return min(len(rows), limit), len(rows) > limit


def _retire_archive_parts_to_mirror(
    database: Path, local_root: Path, mirror_root: Path, cutoff: str, *,
    batch_limit: int, deadline: float | None = None,
) -> dict[str, Any]:
    mirror_root = Path(mirror_root)
    if not mirror_root.exists() or not mirror_root.is_dir():
        raise RetentionError("mirror storage root must be an existing directory")
    with closing(sqlite3.connect(str(database))) as connection:
        connection.row_factory = sqlite3.Row
        raw_parts = connection.execute(
            """SELECT 'raw' AS archive_kind,part.id,part.archive_path,part.content_sha256,
                      part.frame_count AS record_count
               FROM monitoring_raw_archive_parts part
               JOIN monitoring_raw_archive_part_metadata metadata
                 ON metadata.archive_part_id=part.id
               WHERE metadata.last_received_at<? AND metadata.local_deleted_at IS NULL
               ORDER BY part.id LIMIT ?""",
            (cutoff, batch_limit),
        ).fetchall()
        remaining = max(0, batch_limit - len(raw_parts))
        detail_parts = connection.execute(
            """SELECT 'detail' AS archive_kind,id,archive_path,content_sha256,record_count
               FROM station_ingest_detail_archive_parts
               WHERE range_end<? AND local_deleted_at IS NULL ORDER BY id LIMIT ?""",
            (cutoff, remaining),
        ).fetchall() if remaining else []
    retired = {"raw": 0, "detail": 0}
    failures = []
    processed = 0
    budget_exhausted = False
    for part in [*raw_parts, *detail_parts]:
        if _deadline_expired(deadline):
            budget_exhausted = True
            break
        processed += 1
        kind = str(part["archive_kind"])
        part_id = int(part["id"])
        local_path = Path(part["archive_path"])
        try:
            mirror_path = _mirror_archive_path(local_path, local_root, mirror_root)
            if not mirror_path.is_file():
                raise RetentionError("matching mirror archive is missing")
            _verify_archive_copy(
                mirror_path, str(part["content_sha256"]), int(part["record_count"]))
            if kind == "raw":
                with closing(sqlite3.connect(str(database))) as connection:
                    evidence_count = connection.execute(
                        """SELECT COUNT(*) FROM monitoring_archive_evidence_refs
                           WHERE archive_part_id=?""",
                        (part_id,),
                    ).fetchone()[0]
                    sample = connection.execute(
                        """SELECT archive_record_index,record_sha256,frame_sha256
                           FROM monitoring_archive_evidence_refs
                           WHERE archive_part_id=? ORDER BY archive_record_index LIMIT 1""",
                        (part_id,),
                    ).fetchone()
                if evidence_count != int(part["record_count"]) or sample is None:
                    raise RetentionError("raw archive evidence references are incomplete")
                _read_archived_payload(
                    mirror_path, int(sample[0]), str(sample[1]), str(sample[2]),
                    str(part["content_sha256"]), content_verified=True,
                )
            verified_at = _now()
            with closing(sqlite3.connect(str(database), timeout=5, isolation_level=None)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                if kind == "raw":
                    connection.execute(
                        """UPDATE monitoring_raw_archive_part_metadata
                           SET mirror_path=?,mirror_verified_at=? WHERE archive_part_id=?""",
                        (str(mirror_path.resolve()), verified_at, part_id),
                    )
                else:
                    connection.execute(
                        """UPDATE station_ingest_detail_archive_parts
                           SET mirror_path=?,mirror_verified_at=? WHERE id=?""",
                        (str(mirror_path.resolve()), verified_at, part_id),
                    )
                connection.commit()
            local_path.unlink(missing_ok=True)
            with closing(sqlite3.connect(str(database), timeout=5, isolation_level=None)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                if kind == "raw":
                    connection.execute(
                        """UPDATE monitoring_raw_archive_part_metadata
                           SET local_deleted_at=? WHERE archive_part_id=?""",
                        (_now(), part_id),
                    )
                else:
                    connection.execute(
                        """UPDATE station_ingest_detail_archive_parts
                           SET local_deleted_at=? WHERE id=?""",
                        (_now(), part_id),
                    )
                connection.commit()
            retired[kind] += 1
        except (OSError, RetentionError, sqlite3.Error) as exc:
            failures.append(f"{kind} archive part {part_id}: {exc}")
    return {
        "retired_raw_archive_parts": retired["raw"],
        "retired_detail_archive_parts": retired["detail"],
        "mirror_failures": len(failures),
        "mirror_failure_details": failures,
        "mirror_parts_processed": processed,
        "mirror_budget_exhausted": budget_exhausted,
    }


def maintain_retention(
    database: Path, policy: StoragePolicy, *, now: datetime | None = None, batch_limit: int = 1000,
    mirror_storage_dir: Path | None = None, time_budget_seconds: float | None = None,
) -> dict[str, Any]:
    """Archive first, then prune bounded hot detail and unreferenced process rows."""
    if batch_limit <= 0:
        raise RetentionError("retention batch limit must be positive")
    if time_budget_seconds is not None and time_budget_seconds <= 0:
        raise RetentionError("retention time budget must be positive")
    deadline = None if time_budget_seconds is None else time.monotonic() + time_budget_seconds
    now = now or datetime.now(timezone.utc)
    now = (now.replace(tzinfo=timezone.utc) if now.tzinfo is None
           else now.astimezone(timezone.utc))
    # Maintenance must remain able to archive and delete while the hot volume is low.
    # Receiver admission still checks both archive and hot-volume budgets separately.
    root = _require_storage(policy)
    raw_cutoff = _cutoff(now, HOT_RELATION_RETENTION)
    detail_cutoff = _cutoff(now, DETAIL_RETENTION)
    partial_reasons: list[str] = []

    def can_continue() -> bool:
        if _deadline_expired(deadline):
            if "time_budget_exhausted" not in partial_reasons:
                partial_reasons.append("time_budget_exhausted")
            return False
        return True

    backfilled_evidence = 0
    archived_raw = 0
    parse_count = 0
    error_count = 0
    summary_count = 0
    recent_receipts_deleted = 0
    receipt_summaries_deleted = 0
    slot_summaries_deleted = 0
    diagnostic_samples_deleted = 0
    diagnostic_summaries_deleted = 0
    deleted_raw = 0
    retained_raw = 0
    raw_prune_checkpoint = None
    raw_checkpoint_run_id = 0

    # The legacy whole-raw archive is retained as an explicit offline tool. A bounded
    # periodic run must not enter that uninterruptible path before useful cleanup.
    if can_continue() and deadline is None:
        with closing(sqlite3.connect(str(database))) as connection:
            row = connection.execute(
                """SELECT substr(received_at,1,10) FROM ingest_raw_frames
                   WHERE received_at<? AND persistence_state='persisted' AND length(raw_frame)>0
                   ORDER BY received_at,id LIMIT 1""",
                (detail_cutoff,),
            ).fetchone()
        if row is not None:
            archived_raw = archive_raw_day(
                database, str(row[0]), policy, batch_limit=batch_limit)
            if archived_raw >= batch_limit:
                partial_reasons.append("raw_archive_batch_limit")
    if can_continue():
        try:
            with closing(sqlite3.connect(str(database), timeout=5, isolation_level=None)) as connection:
                connection.row_factory = sqlite3.Row
                _begin_immediate_with_deadline(connection, deadline, "diagnostic_cleanup_lock")
                cleanup = prune_ingest_error_samples(
                    connection, now=now, batch_limit=batch_limit, deadline=deadline)
                with _statement_deadline(connection, deadline, "diagnostic_cleanup_commit"):
                    connection.commit()
            diagnostic_samples_deleted = int(cleanup["samples_deleted"])
            diagnostic_summaries_deleted = int(cleanup["summaries_deleted"])
            if cleanup["partial"]:
                partial_reasons.append("diagnostic_cleanup_batch_limit")
        except _StatementDeadlineExceeded as exc:
            partial_reasons.append(str(exc))
    for table, column, cutoff, reason, target in (
        ("station_ingest_recent_receipts", "received_at",
         _cutoff(now, RECENT_RECEIPT_RETENTION), "recent_receipt_batch_limit", "recent"),
        ("station_ingest_receipt_hourly", "last_received_at",
         _cutoff(now, RECEIPT_SUMMARY_RETENTION), "receipt_summary_batch_limit", "receipt"),
        ("station_ingest_slot_summaries", "last_received_at",
         _cutoff(now, SLOT_SUMMARY_RETENTION), "slot_summary_batch_limit", "slot"),
    ):
        if not can_continue():
            break
        try:
            deleted = _delete_rowid_batch(
                database, table, column, cutoff, batch_limit=batch_limit, deadline=deadline)
        except _StatementDeadlineExceeded as exc:
            partial_reasons.append(str(exc))
            break
        if target == "recent":
            recent_receipts_deleted = deleted
        elif target == "receipt":
            receipt_summaries_deleted = deleted
        else:
            slot_summaries_deleted = deleted
        if deleted >= batch_limit:
            partial_reasons.append(reason)
    if can_continue() and deadline is not None:
        try:
            with closing(sqlite3.connect(str(database))) as connection:
                with _statement_deadline(
                    connection, deadline, "raw_archive_oldest_day"
                ):
                    row = connection.execute(
                        """SELECT substr(received_at,1,10) FROM ingest_raw_frames
                           WHERE received_at<? AND persistence_state='persisted'
                             AND length(raw_frame)>0
                           ORDER BY received_at,id LIMIT 1""",
                        (detail_cutoff,),
                    ).fetchone()
            if row is not None:
                archived_raw = archive_raw_day(
                    database, str(row[0]), policy, batch_limit=batch_limit,
                    deadline=deadline)
                if archived_raw >= batch_limit:
                    partial_reasons.append("raw_archive_batch_limit")
        except _StatementDeadlineExceeded as exc:
            partial_reasons.append(str(exc))
    if deadline is None:
        for kind, cutoff, reason in (
            ("parse_attempt", detail_cutoff, "parse_archive_batch_limit"),
            ("ingest_error", detail_cutoff, "error_archive_batch_limit"),
            ("error_summary", _cutoff(now, ERROR_SUMMARY_RETENTION),
             "error_summary_batch_limit"),
        ):
            count = _archive_detail_batch(
                database, root, kind, cutoff, batch_limit=batch_limit)
            if kind == "parse_attempt":
                parse_count = count
            elif kind == "ingest_error":
                error_count = count
            else:
                summary_count = count
            if count >= batch_limit:
                partial_reasons.append(reason)
    if can_continue():
        try:
            (deleted_raw, retained_raw, raw_prune_checkpoint,
             raw_checkpoint_run_id) = _delete_unprotected_raw_batch(
                database, raw_cutoff,
                now=now.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
                batch_limit=batch_limit, deadline=deadline)
            if raw_prune_checkpoint["stop_reason"] != "upper_bound_reached":
                partial_reasons.append("raw_prune_window_incomplete")
        except _StatementDeadlineExceeded as exc:
            raw_prune_checkpoint = exc.checkpoint
            partial_reasons.append(str(exc))
    for kind, cutoff, reason in (() if deadline is None else (
        ("parse_attempt", detail_cutoff, "parse_archive_batch_limit"),
        ("ingest_error", detail_cutoff, "error_archive_batch_limit"),
        ("error_summary", _cutoff(now, ERROR_SUMMARY_RETENTION),
         "error_summary_batch_limit"),
    )):
        if not can_continue():
            break
        if (deadline is not None
                and deadline - time.monotonic()
                < MAINTENANCE_ARCHIVE_MINIMUM_BUDGET_SECONDS):
            partial_reasons.append("time_budget_reserved_for_cleanup")
            break
        try:
            count = _archive_detail_batch(
                database, root, kind, cutoff, batch_limit=batch_limit,
                deadline=deadline)
        except _StatementDeadlineExceeded as exc:
            partial_reasons.append(str(exc))
            break
        if kind == "parse_attempt":
            parse_count = count
        elif kind == "ingest_error":
            error_count = count
        else:
            summary_count = count
        if count >= batch_limit:
            partial_reasons.append(reason)
    archive_cutoff = _cutoff(now, CLOUD_ARCHIVE_RETENTION)
    mirror_result = {
        "retired_raw_archive_parts": 0,
        "retired_detail_archive_parts": 0,
        "mirror_failures": 0,
        "mirror_failure_details": [],
        "mirror_parts_processed": 0,
        "mirror_budget_exhausted": False,
    }
    if mirror_storage_dir is not None and can_continue():
        mirror_result = _retire_archive_parts_to_mirror(
            database, root, mirror_storage_dir, archive_cutoff,
            batch_limit=batch_limit, deadline=deadline)
        if mirror_result["mirror_parts_processed"] >= batch_limit:
            partial_reasons.append("mirror_batch_limit")
        if mirror_result["mirror_budget_exhausted"]:
            partial_reasons.append("time_budget_exhausted")

    overdue_raw_parts = 0
    unmirrored_raw_parts = 0
    overdue_detail_parts = 0
    unmirrored_detail_parts = 0
    backlog_counts_truncated = False
    backlog_scan_completed = can_continue()
    if backlog_scan_completed:
        with closing(sqlite3.connect(str(database))) as connection:
            overdue_raw_parts, truncated = _bounded_count(
                connection,
                """SELECT metadata.archive_part_id FROM monitoring_raw_archive_part_metadata metadata
                   WHERE metadata.last_received_at<? AND metadata.local_deleted_at IS NULL""",
                (archive_cutoff,), batch_limit)
            backlog_counts_truncated |= truncated
            unmirrored_raw_parts, truncated = _bounded_count(
                connection,
                """SELECT metadata.archive_part_id FROM monitoring_raw_archive_part_metadata metadata
                   WHERE metadata.last_received_at<?
                     AND (metadata.mirror_path IS NULL OR metadata.mirror_verified_at IS NULL)""",
                (archive_cutoff,), batch_limit)
            backlog_counts_truncated |= truncated
            overdue_detail_parts, truncated = _bounded_count(
                connection,
                """SELECT id FROM station_ingest_detail_archive_parts
                   WHERE range_end<? AND local_deleted_at IS NULL""",
                (archive_cutoff,), batch_limit)
            backlog_counts_truncated |= truncated
            unmirrored_detail_parts, truncated = _bounded_count(
                connection,
                """SELECT id FROM station_ingest_detail_archive_parts
                   WHERE range_end<?
                     AND (mirror_path IS NULL OR mirror_verified_at IS NULL)""",
                (archive_cutoff,), batch_limit)
            backlog_counts_truncated |= truncated
    if backlog_counts_truncated:
        partial_reasons.append("backlog_count_limit")
    overdue_archive_parts = overdue_raw_parts + overdue_detail_parts
    unmirrored_archive_parts = unmirrored_raw_parts + unmirrored_detail_parts
    retry_required = bool(
        partial_reasons or overdue_archive_parts or mirror_result["mirror_failures"])
    status = "partial" if retry_required else "completed"
    if raw_prune_checkpoint is None:
        with closing(sqlite3.connect(str(database))) as connection:
            raw_prune_checkpoint = _latest_raw_prune_checkpoint(connection)
    result = {
        "status": status,
        "retry_required": retry_required,
        "partial_reasons": sorted(set(partial_reasons)),
        "time_budget_seconds": time_budget_seconds,
        "backlog_scan_completed": backlog_scan_completed,
        "backlog_counts_truncated": backlog_counts_truncated,
        "backfilled_evidence_refs": backfilled_evidence,
        "bootstrapped_current_states": 0,
        "archived_raw_frames": archived_raw,
        "archived_parse_attempts": parse_count,
        "archived_errors": error_count,
        "archived_error_summaries": summary_count,
        "deleted_recent_receipts": recent_receipts_deleted,
        "deleted_receipt_summaries": receipt_summaries_deleted,
        "deleted_slot_summaries": slot_summaries_deleted,
        "deleted_diagnostic_samples": diagnostic_samples_deleted,
        "deleted_diagnostic_summaries": diagnostic_summaries_deleted,
        "deleted_raw_frames": deleted_raw,
        "retained_protected_frames": retained_raw,
        "raw_prune_checkpoint": raw_prune_checkpoint,
        "overdue_archive_parts": overdue_archive_parts,
        "unmirrored_archive_parts": unmirrored_archive_parts,
        **mirror_result,
    }
    with closing(sqlite3.connect(str(database), timeout=5, isolation_level=None)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        persisted_cutoff = (
            str(raw_prune_checkpoint["cutoff"]) if raw_prune_checkpoint else raw_cutoff)
        run_values = (
            persisted_cutoff, parse_count, error_count, summary_count, deleted_raw, retained_raw,
            status, json.dumps(result, sort_keys=True), _now(),
        )
        if raw_checkpoint_run_id:
            connection.execute(
                """UPDATE station_ingest_retention_runs
                   SET cutoff_time=?,archived_parse_attempts=?,archived_errors=?,
                       archived_error_summaries=?,deleted_raw_frames=?,
                       retained_protected_frames=?,status=?,detail=?,created_at=?
                   WHERE id=?""",
                (*run_values, raw_checkpoint_run_id),
            )
        else:
            connection.execute(
                """INSERT INTO station_ingest_retention_runs(
                       cutoff_time,archived_parse_attempts,archived_errors,
                       archived_error_summaries,deleted_raw_frames,
                       retained_protected_frames,status,detail,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                run_values,
            )
        if retry_required:
            failure = mirror_result["mirror_failure_details"][:1]
            failure_text = f", failure={failure[0]}" if failure else ""
            _record_health(
                connection, "degraded",
                f"archive retention backlog: overdue={overdue_archive_parts}, "
                f"unmirrored={unmirrored_archive_parts}{failure_text}",
            )
        else:
            _record_health(connection, "healthy", "retention maintenance completed")
        connection.commit()
    return result


def _record_periodic_failure(database: Path, error: Exception) -> None:
    try:
        with closing(sqlite3.connect(str(database), timeout=5, isolation_level=None)) as connection:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO station_ingest_retention_runs(
                       cutoff_time,archived_parse_attempts,archived_errors,archived_error_summaries,
                       deleted_raw_frames,retained_protected_frames,status,detail,created_at)
                   VALUES (?,0,0,0,0,0,'failed',?,?)""",
                (_now(), json.dumps({"error": str(error)}, sort_keys=True), _now()),
            )
            connection.commit()
    except sqlite3.Error:
        pass


def run_periodic_maintenance(
    database: Path, policy: StoragePolicy, *, lock_file: Path, now: datetime | None = None,
    batch_limit: int = 1000, mirror_storage_dir: Path | None = None,
    time_budget_seconds: float = 2700,
) -> dict[str, Any]:
    """Use one lock and one time budget to run resumable maintenance batches."""
    started = time.monotonic()
    deadline = started + time_budget_seconds
    count_keys = (
        "backfilled_evidence_refs", "archived_raw_frames", "archived_parse_attempts",
        "archived_errors", "archived_error_summaries", "deleted_recent_receipts",
        "deleted_receipt_summaries", "deleted_slot_summaries",
        "deleted_diagnostic_samples", "deleted_diagnostic_summaries",
        "deleted_raw_frames",
        "retained_protected_frames", "retired_raw_archive_parts",
        "retired_detail_archive_parts", "mirror_parts_processed",
    )
    totals = {key: 0 for key in count_keys}
    batches: list[dict[str, Any]] = []
    last_checkpoint: tuple[Any, ...] | None = None
    stop_reason = "completed"
    try:
        with MaintenanceLock(lock_file):
            with closing(sqlite3.connect(str(database))) as connection:
                existing_checkpoint = _latest_raw_prune_checkpoint(connection) or {}
            last_checkpoint = (
                existing_checkpoint.get("cutoff"), existing_checkpoint.get("cursor"),
                existing_checkpoint.get("upper_raw_id"), existing_checkpoint.get("scanned"),
            )
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    stop_reason = "time_budget_exhausted"
                    break
                result = maintain_retention(
                    database, policy, now=now, batch_limit=batch_limit,
                    mirror_storage_dir=mirror_storage_dir,
                    time_budget_seconds=remaining,
                )
                for key in count_keys:
                    totals[key] += int(result.get(key, 0) or 0)
                checkpoint = result.get("raw_prune_checkpoint") or {}
                checkpoint_token = (
                    checkpoint.get("cutoff"), checkpoint.get("cursor"),
                    checkpoint.get("upper_raw_id"), checkpoint.get("scanned"),
                )
                work_count = sum(int(result.get(key, 0) or 0) for key in count_keys)
                checkpoint_advanced = checkpoint_token != last_checkpoint
                batches.append({
                    "status": result["status"],
                    "partial_reasons": result["partial_reasons"],
                    "work_count": work_count,
                    "raw_prune_checkpoint": checkpoint,
                })
                if not result["retry_required"]:
                    stop_reason = "completed"
                    break
                if work_count == 0 and not checkpoint_advanced:
                    stop_reason = "no_progress"
                    break
                last_checkpoint = checkpoint_token
            if not batches:
                result = {
                    "status": "partial", "retry_required": True,
                    "partial_reasons": ["time_budget_exhausted"],
                    "raw_prune_checkpoint": None,
                }
    except Exception as exc:
        _record_periodic_failure(database, exc)
        raise
    retry_required = stop_reason != "completed" or bool(result["retry_required"])
    partial_reasons = set(result.get("partial_reasons", ()))
    if stop_reason != "completed":
        partial_reasons.add(stop_reason)
    return {
        "status": "partial" if retry_required else "completed",
        "historical_maintenance_called": 1,
        "retry_required": retry_required,
        "maintenance_batches": len(batches),
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "stop_reason": stop_reason,
        "partial_reasons": sorted(partial_reasons),
        "totals": totals,
        "batches": batches,
        "maintenance": result,
    }


def reduce_legacy_indexes(database: Path, *, now: datetime | None = None) -> list[str]:
    """Keep the r25 rollback contract intact until a later release retires it."""
    del database, now
    raise RetentionError(
        "legacy index reduction is disabled while r25 remains the rollback version")


def compact_isolated_copy(
    source: Path, target: Path, policy: StoragePolicy, *, now: datetime | None = None,
    batch_limit: int = 5000,
) -> dict[str, Any]:
    """Retired: copying the large source before reduction repeats the failed R26 path."""
    del source, target, policy, now, batch_limit
    raise RetentionError(
        "compact-copy is retired; prepare the protected source first, then use compact-prepared")


def compact_prepared_database(source: Path, target: Path) -> dict[str, Any]:
    """Write one final compact database from a prepared source without another work copy."""
    source = Path(source)
    target = Path(target)
    if not source.is_file():
        raise RetentionError("prepared source database does not exist")
    if target.exists():
        raise RetentionError("compaction target already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        from .station_ingest_cutover import require_cutover_ready
    except ImportError:  # pragma: no cover - direct script execution
        from station_ingest_cutover import require_cutover_ready
    require_cutover_ready(source)
    started_at = datetime.now(timezone.utc)
    free_before = shutil.disk_usage(target.parent).free
    try:
        with closing(sqlite3.connect(str(source), timeout=30, isolation_level=None)) as connection:
            connection.execute("VACUUM INTO ?", (str(target),))
        with closing(sqlite3.connect(str(target), timeout=30)) as compacted:
            if compacted.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RetentionError("compacted database failed quick verification")
            if compacted.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RetentionError("compacted database contains foreign-key violations")
        require_cutover_ready(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return {
        "source": str(source), "target": str(target),
        "source_bytes": source.stat().st_size, "target_bytes": target.stat().st_size,
        "target_filesystem_free_bytes_before": free_before,
        "elapsed_seconds": round((datetime.now(timezone.utc) - started_at).total_seconds(), 3),
        "consistency_point": "prepared-source-vacuum-into",
        "source_modified": False,
    }


def _storage_policy_from_arguments(arguments: argparse.Namespace) -> StoragePolicy:
    budget_values = (
        arguments.archive_minimum_total_bytes,
        arguments.archive_minimum_free_bytes,
        arguments.hot_minimum_total_bytes,
        arguments.hot_minimum_free_bytes,
    )
    if arguments.storage_mode == "governance":
        if any(value is None for value in budget_values):
            raise RetentionError(
                "governance mode requires explicit archive and hot storage capacity budgets")
        return storage_policy_for_governance(
            arguments.raw_storage_dir,
            archive_minimum_total_bytes=arguments.archive_minimum_total_bytes,
            archive_minimum_free_bytes=arguments.archive_minimum_free_bytes,
            hot_minimum_total_bytes=arguments.hot_minimum_total_bytes,
            hot_minimum_free_bytes=arguments.hot_minimum_free_bytes,
        )
    if any(value is not None for value in budget_values):
        raise RetentionError("explicit storage budgets require --storage-mode governance")
    return storage_policy_for_mode(arguments.raw_storage_dir, arguments.storage_mode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline station-ingestion retention maintenance")
    parser.add_argument(
        "action",
        choices=(
            "archive", "archive-next", "aggregate", "bootstrap", "maintain", "maintain-once",
            "reduce-indexes", "compact-copy", "compact-prepared",
            "prepare-exact-duplicate-cleanup", "purge-exact-duplicates",
            "run-exact-duplicate-cleanup", "drop-exact-duplicate-cleanup-indexes",
        ),
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--day")
    parser.add_argument("--target", type=Path)
    parser.add_argument("--batch-limit", type=int, default=1000)
    parser.add_argument("--raw-storage-dir", type=Path)
    parser.add_argument("--mirror-storage-dir", type=Path)
    parser.add_argument("--lock-file", type=Path)
    parser.add_argument(
        "--storage-mode", choices=("long_term", "validation", "governance"),
        default="long_term")
    parser.add_argument("--archive-minimum-total-bytes", type=int)
    parser.add_argument("--archive-minimum-free-bytes", type=int)
    parser.add_argument("--hot-minimum-total-bytes", type=int)
    parser.add_argument("--hot-minimum-free-bytes", type=int)
    parser.add_argument("--time-budget-seconds", type=float)
    parser.add_argument("--cutoff")
    parser.add_argument("--receipt-cutoff")
    parser.add_argument("--upper-raw-id", type=int)
    parser.add_argument("--cursor", type=int)
    parser.add_argument("--scan-limit", type=int, default=2000)
    parser.add_argument("--candidate-range", action="append", default=[])
    arguments = parser.parse_args(argv)
    try:
        if arguments.action == "aggregate":
            if not arguments.day:
                raise RetentionError("aggregate requires --day")
            result = aggregate_hourly(arguments.database, arguments.day)
        elif arguments.action == "archive":
            if not arguments.day:
                raise RetentionError("archive requires --day")
            if arguments.raw_storage_dir is None:
                raise RetentionError("archive requires --raw-storage-dir")
            result = archive_raw_day(
                arguments.database, arguments.day,
                _storage_policy_from_arguments(arguments),
                batch_limit=arguments.batch_limit,
            )
        elif arguments.action == "archive-next":
            if not arguments.day:
                raise RetentionError("archive-next requires --day as an exclusive boundary")
            if arguments.raw_storage_dir is None:
                raise RetentionError("archive-next requires --raw-storage-dir")
            result = archive_oldest_raw_day(
                arguments.database, arguments.day,
                _storage_policy_from_arguments(arguments),
                batch_limit=arguments.batch_limit,
            )
        elif arguments.action == "bootstrap":
            result = bootstrap_retention_state(arguments.database)
        elif arguments.action == "reduce-indexes":
            result = reduce_legacy_indexes(arguments.database)
        elif arguments.action == "prepare-exact-duplicate-cleanup":
            lock_file = arguments.lock_file or arguments.database.with_suffix(
                arguments.database.suffix + ".station-ingest-retention.lock")
            with MaintenanceLock(lock_file):
                result = ensure_duplicate_cleanup_indexes(
                    arguments.database, time_budget_seconds=arguments.time_budget_seconds)
        elif arguments.action == "drop-exact-duplicate-cleanup-indexes":
            lock_file = arguments.lock_file or arguments.database.with_suffix(
                arguments.database.suffix + ".station-ingest-retention.lock")
            with MaintenanceLock(lock_file):
                result = {"dropped": drop_duplicate_cleanup_indexes(arguments.database)}
        elif arguments.action in ("purge-exact-duplicates", "run-exact-duplicate-cleanup"):
            if not arguments.cutoff or not arguments.receipt_cutoff:
                raise RetentionError(
                    f"{arguments.action} requires --cutoff and --receipt-cutoff")
            if arguments.upper_raw_id is None:
                raise RetentionError(f"{arguments.action} requires --upper-raw-id")
            if arguments.raw_storage_dir is None:
                raise RetentionError(f"{arguments.action} requires --raw-storage-dir")
            lock_file = arguments.lock_file or arguments.database.with_suffix(
                arguments.database.suffix + ".station-ingest-retention.lock")
            with MaintenanceLock(lock_file):
                if arguments.action == "run-exact-duplicate-cleanup":
                    result = run_exact_duplicate_cleanup_job(
                        arguments.database, cutoff=arguments.cutoff,
                        receipt_cutoff=arguments.receipt_cutoff,
                        upper_raw_id=arguments.upper_raw_id,
                        candidate_ranges=_normalize_candidate_ranges(
                            arguments.candidate_range),
                        batch_limit=arguments.batch_limit,
                        scan_limit=arguments.scan_limit,
                        time_budget_seconds=arguments.time_budget_seconds,
                        archive_root=arguments.raw_storage_dir)
                else:
                    result = purge_exact_duplicate_batch(
                        arguments.database, cutoff=arguments.cutoff,
                        receipt_cutoff=arguments.receipt_cutoff,
                        upper_raw_id=arguments.upper_raw_id, cursor=arguments.cursor,
                        batch_limit=arguments.batch_limit, scan_limit=arguments.scan_limit,
                        time_budget_seconds=arguments.time_budget_seconds,
                        archive_root=arguments.raw_storage_dir,
                        candidate_ranges=_normalize_candidate_ranges(
                            arguments.candidate_range))
        elif arguments.action == "compact-prepared":
            if arguments.target is None:
                raise RetentionError("compact-prepared requires --target")
            result = compact_prepared_database(arguments.database, arguments.target)
        else:
            if arguments.raw_storage_dir is None:
                raise RetentionError(f"{arguments.action} requires --raw-storage-dir")
            policy = _storage_policy_from_arguments(arguments)
            if arguments.action == "maintain":
                result = maintain_retention(
                    arguments.database, policy, batch_limit=arguments.batch_limit,
                    mirror_storage_dir=arguments.mirror_storage_dir,
                    time_budget_seconds=arguments.time_budget_seconds)
            elif arguments.action == "maintain-once":
                lock_file = arguments.lock_file or arguments.database.with_suffix(
                    arguments.database.suffix + ".station-ingest-retention.lock")
                result = run_periodic_maintenance(
                    arguments.database, policy, lock_file=lock_file,
                    batch_limit=arguments.batch_limit,
                    mirror_storage_dir=arguments.mirror_storage_dir,
                    time_budget_seconds=(
                        2700 if arguments.time_budget_seconds is None
                        else arguments.time_budget_seconds),
                )
            else:
                if arguments.target is None:
                    raise RetentionError("compact-copy requires --target")
                result = compact_isolated_copy(
                    arguments.database, arguments.target, policy,
                    batch_limit=arguments.batch_limit,
                )
    except _StatementDeadlineExceeded as exc:
        print(json.dumps({
            "action": arguments.action, "status": "partial", "error": str(exc),
            "checkpoint": exc.checkpoint,
        }, sort_keys=True), file=sys.stderr)
        return 75
    except (RetentionError, sqlite3.Error) as exc:
        print(json.dumps({
            "action": arguments.action, "status": "failed", "error": str(exc),
        }, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps({"action": arguments.action, "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
