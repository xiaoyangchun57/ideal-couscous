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
ERROR_SUMMARY_RETENTION = timedelta(days=30)
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
    "idx_ingest_parse_raw_cleanup":
        "CREATE INDEX IF NOT EXISTS idx_ingest_parse_raw_cleanup ON ingest_parse_attempts(raw_frame_id)",
    "idx_ingest_errors_raw_cleanup":
        "CREATE INDEX IF NOT EXISTS idx_ingest_errors_raw_cleanup ON ingest_errors(raw_frame_id)",
    "idx_observation_batches_raw_cleanup":
        "CREATE INDEX IF NOT EXISTS idx_observation_batches_raw_cleanup ON observation_batches(raw_frame_id)",
    "idx_monitoring_quality_raw_cleanup":
        "CREATE INDEX IF NOT EXISTS idx_monitoring_quality_raw_cleanup ON monitoring_quality_issues(raw_frame_id)",
    "idx_ingest_raw_duplicate_cleanup":
        "CREATE INDEX IF NOT EXISTS idx_ingest_raw_duplicate_cleanup ON ingest_raw_frames(duplicate_of_raw_frame_id)",
}
RAW_PRUNE_SCAN_LIMIT = 256


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


def _archive_records(rows: list[sqlite3.Row]) -> tuple[list[bytes], str]:
    lines = []
    digest = hashlib.sha256()
    for row in rows:
        item = _archive_item(row)
        line = (json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
        lines.append(line)
        digest.update(line)
    return lines, digest.hexdigest()


def _write_and_verify_archive(path: Path, lines: list[bytes], expected_digest: str, rows: list[sqlite3.Row]) -> None:
    """Verify decompressed IDs, hashes, payload bytes, and the content-lines digest."""
    if not path.exists():
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            with gzip.open(temporary, "wb") as stream:
                for line in lines:
                    stream.write(line)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    digest = hashlib.sha256()
    actual: list[tuple[int, str, bytes]] = []
    try:
        with gzip.open(path, "rb") as stream:
            for line in stream:
                digest.update(line)
                item = json.loads(line.decode("ascii"))
                actual.append((int(item["id"]), str(item["sha256"]), base64.b64decode(item["frame_b64"], validate=True)))
    except (OSError, ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetentionError("raw archive file cannot be verified") from exc
    expected = [(int(row["id"]), str(row["frame_sha256"]), bytes(row["raw_frame"])) for row in rows]
    if digest.hexdigest() != expected_digest or actual != expected:
        raise RetentionError("raw archive content verification failed")


def archive_raw_day(
    database: Path, day: str, policy: StoragePolicy, *, batch_limit: int | None = None,
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
        root = _require_storage(policy, database)
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
            rows = connection.execute(query, parameters).fetchall()
        if not rows:
            return 0
        lines, content_digest = _archive_records(rows)
        archive_dir = root / "raw-archives"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"{day}-{rows[0]['id']}-{rows[-1]['id']}-{content_digest[:16]}.jsonl.gz"
        _write_and_verify_archive(archive_path, lines, content_digest, rows)
        with closing(sqlite3.connect(str(database), timeout=5, isolation_level=None)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
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
        if isinstance(exc, RetentionError):
            raise
        raise RetentionError("raw archive could not be completed") from exc


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


def _write_detail_archive(root: Path, kind: str, records: list[dict[str, Any]]) -> tuple[Path, str]:
    lines = [
        (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for record in records
    ]
    digest = hashlib.sha256(b"".join(lines)).hexdigest()
    directory = root / "detail-archives"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{kind}-{records[0]['id']}-{records[-1]['id']}-{digest[:16]}.jsonl.gz"
    if not path.exists():
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            with gzip.open(temporary, "wb") as stream:
                stream.writelines(lines)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    verified = hashlib.sha256()
    actual = []
    try:
        with gzip.open(path, "rb") as stream:
            for line in stream:
                verified.update(line)
                actual.append(json.loads(line.decode("utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetentionError("detail archive file cannot be verified") from exc
    if verified.hexdigest() != digest or actual != records:
        raise RetentionError("detail archive content verification failed")
    return path, digest


def _archive_detail_batch(
    database: Path, root: Path, kind: str, cutoff: str, *, batch_limit: int,
) -> int:
    table = {
        "parse_attempt": "ingest_parse_attempts",
        "ingest_error": "ingest_errors",
        "error_summary": "station_ingest_error_hourly",
    }[kind]
    with closing(sqlite3.connect(str(database))) as connection:
        connection.row_factory = sqlite3.Row
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
    records = [dict(row) for row in rows]
    path, digest = _write_detail_archive(root, kind, records)
    ids = [int(row["id"]) for row in rows]
    with closing(sqlite3.connect(str(database), timeout=5, isolation_level=None)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        placeholders = ",".join("?" * len(ids))
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
        connection.execute(
            """INSERT OR IGNORE INTO station_ingest_detail_archive_parts(
                   detail_kind,archive_path,content_sha256,first_source_id,last_source_id,
                   record_count,range_start,range_end,verified_at,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (kind, str(path), digest, ids[0], ids[-1], len(ids),
             min(str(row.get("created_at") or row.get("bucket_hour")) for row in records),
             max(str(row.get("created_at") or row.get("bucket_hour")) for row in records),
             _now(), _now()),
        )
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


def ensure_duplicate_cleanup_indexes(database: Path) -> dict[str, Any]:
    """Create resumable cleanup-only indexes and report their file-space impact."""
    started_at = datetime.now(timezone.utc)
    before_bytes = Path(database).stat().st_size
    with closing(sqlite3.connect(str(database), timeout=30, isolation_level=None)) as connection:
        existing = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        created = []
        for name, statement in CLEANUP_INDEXES.items():
            if name not in existing:
                connection.execute(statement)
                created.append(name)
    return {
        "created": created,
        "database_growth_bytes": max(0, Path(database).stat().st_size - before_bytes),
        "elapsed_seconds": round(
            (datetime.now(timezone.utc) - started_at).total_seconds(), 3),
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
) -> int:
    allowed = {
        ("station_ingest_recent_receipts", "received_at"),
        ("station_ingest_receipt_hourly", "last_received_at"),
        ("station_ingest_slot_summaries", "last_received_at"),
    }
    if (table, cutoff_column) not in allowed:
        raise RetentionError("retention batch target is not allowed")
    with closing(sqlite3.connect(str(database), timeout=5, isolation_level=None)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        rowids = [int(row[0]) for row in connection.execute(
            f"SELECT rowid FROM {table} WHERE {cutoff_column}<? ORDER BY rowid LIMIT ?",
            (cutoff, batch_limit),
        )]
        if rowids:
            marks = ",".join("?" * len(rowids))
            connection.execute(f"DELETE FROM {table} WHERE rowid IN ({marks})", rowids)
        connection.commit()
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
    root = _require_storage(policy, database)
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
    deleted_raw = 0
    retained_raw = 0
    raw_prune_checkpoint = None
    raw_checkpoint_run_id = 0

    # Legacy whole-part backfill is not bounded by this run's row/time budget.
    # Missing evidence references already exclude those raw identities from deletion.
    # Keep their archive dependencies and let independent cleanup proceed.
    if can_continue():
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
        parse_count = _archive_detail_batch(
            database, root, "parse_attempt", detail_cutoff, batch_limit=batch_limit)
        if parse_count >= batch_limit:
            partial_reasons.append("parse_archive_batch_limit")
    if can_continue():
        error_count = _archive_detail_batch(
            database, root, "ingest_error", detail_cutoff, batch_limit=batch_limit)
        if error_count >= batch_limit:
            partial_reasons.append("error_archive_batch_limit")
    if can_continue():
        summary_count = _archive_detail_batch(
            database, root, "error_summary", _cutoff(now, ERROR_SUMMARY_RETENTION),
            batch_limit=batch_limit)
        if summary_count >= batch_limit:
            partial_reasons.append("error_summary_batch_limit")
    if can_continue():
        recent_receipts_deleted = _delete_rowid_batch(
            database, "station_ingest_recent_receipts", "received_at",
            _cutoff(now, RECENT_RECEIPT_RETENTION), batch_limit=batch_limit)
        if recent_receipts_deleted >= batch_limit:
            partial_reasons.append("recent_receipt_batch_limit")
    if can_continue():
        receipt_summaries_deleted = _delete_rowid_batch(
            database, "station_ingest_receipt_hourly", "last_received_at",
            _cutoff(now, RECEIPT_SUMMARY_RETENTION), batch_limit=batch_limit)
        if receipt_summaries_deleted >= batch_limit:
            partial_reasons.append("receipt_summary_batch_limit")
    if can_continue():
        slot_summaries_deleted = _delete_rowid_batch(
            database, "station_ingest_slot_summaries", "last_received_at",
            _cutoff(now, SLOT_SUMMARY_RETENTION), batch_limit=batch_limit)
        if slot_summaries_deleted >= batch_limit:
            partial_reasons.append("slot_summary_batch_limit")
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
    # Migration 014 persists only completed/failed; resumable partial state lives in detail.
    persisted_status = "completed"
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
            persisted_status, json.dumps(result, sort_keys=True), _now(),
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
    """Run one exclusive, bounded maintenance pass and expose resumable backlog."""
    try:
        with MaintenanceLock(lock_file):
            result = maintain_retention(
                database, policy, now=now, batch_limit=batch_limit,
                mirror_storage_dir=mirror_storage_dir,
                time_budget_seconds=time_budget_seconds,
            )
    except Exception as exc:
        _record_periodic_failure(database, exc)
        raise
    return {
        "status": result["status"],
        "historical_maintenance_called": 1,
        "retry_required": result["retry_required"],
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
            "archive", "aggregate", "bootstrap", "maintain", "maintain-once",
            "reduce-indexes", "compact-copy", "compact-prepared",
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
        elif arguments.action == "bootstrap":
            result = bootstrap_retention_state(arguments.database)
        elif arguments.action == "reduce-indexes":
            result = reduce_legacy_indexes(arguments.database)
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
    except (RetentionError, sqlite3.Error) as exc:
        print(json.dumps({
            "action": arguments.action, "status": "failed", "error": str(exc),
        }, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps({"action": arguments.action, "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
