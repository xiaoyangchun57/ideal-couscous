"""Offline retention maintenance for station-ingestion evidence."""
from __future__ import annotations

import argparse
import base64
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile


class RetentionError(RuntimeError):
    pass


LONG_TERM_MINIMUM_TOTAL_BYTES = 100 * 1024 ** 3
LONG_TERM_MINIMUM_FREE_BYTES = 10 * 1024 ** 3
VALIDATION_MINIMUM_TOTAL_BYTES = 20 * 1024 ** 3
VALIDATION_MINIMUM_FREE_BYTES = 5 * 1024 ** 3


@dataclass(frozen=True)
class StoragePolicy:
    root: Path
    minimum_total_bytes: int = LONG_TERM_MINIMUM_TOTAL_BYTES
    minimum_free_bytes: int = LONG_TERM_MINIMUM_FREE_BYTES
    mode: str = "long_term"

    def __post_init__(self) -> None:
        if self.mode not in {"long_term", "validation"}:
            raise RetentionError("storage mode is invalid")
        if self.mode == "long_term" and (self.minimum_total_bytes < LONG_TERM_MINIMUM_TOTAL_BYTES
                                          or self.minimum_free_bytes < LONG_TERM_MINIMUM_FREE_BYTES):
            raise RetentionError("long-term storage requires at least 100 GiB")
        if self.mode == "validation" and (self.minimum_total_bytes < VALIDATION_MINIMUM_TOTAL_BYTES
                                            or self.minimum_free_bytes < VALIDATION_MINIMUM_FREE_BYTES):
            raise RetentionError("validation storage requires at least 20 GiB total and 5 GiB free")


def storage_policy_for_mode(root: Path, mode: str) -> StoragePolicy:
    if mode == "validation":
        return StoragePolicy(root, VALIDATION_MINIMUM_TOTAL_BYTES, VALIDATION_MINIMUM_FREE_BYTES, mode)
    return StoragePolicy(root, mode=mode)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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
    checked_roots = [("archive storage", root)]
    if database is not None:
        database_root = Path(database).parent
        if database_root.resolve() != root.resolve():
            checked_roots.append(("SQLite hot storage", database_root))
    for label, checked_root in checked_roots:
        if not checked_root.exists() or not checked_root.is_dir():
            raise RetentionError(f"{label} root must be an existing directory")
        usage = shutil.disk_usage(checked_root)
        if usage.total < policy.minimum_total_bytes:
            raise RetentionError(f"{label} does not meet the minimum capacity")
        if usage.free < policy.minimum_free_bytes:
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


def _archive_records(rows: list[sqlite3.Row]) -> tuple[list[bytes], str]:
    lines = []
    digest = hashlib.sha256()
    for row in rows:
        item = {"id": row["id"], "received_at": row["received_at"], "sha256": row["frame_sha256"],
                "frame_b64": base64.b64encode(row["raw_frame"]).decode("ascii")}
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


def archive_raw_day(database: Path, day: str, policy: StoragePolicy) -> int:
    """Append immutable archive parts, preserving late receipts and retry safety."""
    try:
        start_day = date.fromisoformat(day)
    except ValueError as exc:
        raise RetentionError("archive day must use YYYY-MM-DD") from exc
    end_day = start_day + timedelta(days=1)
    try:
        root = _require_storage(policy, database)
        with closing(sqlite3.connect(str(database), timeout=5, isolation_level=None)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """SELECT raw.id,raw.received_at,raw.frame_sha256,raw.raw_frame FROM ingest_raw_frames raw
                   LEFT JOIN monitoring_raw_archive_part_frames archive_link ON archive_link.raw_frame_id=raw.id
                   WHERE raw.received_at>=? AND raw.received_at<? AND raw.persistence_state='persisted'
                     AND length(raw.raw_frame)>0 AND archive_link.raw_frame_id IS NULL ORDER BY raw.id""",
                (f"{start_day.isoformat()}T00:00:00+00:00", f"{end_day.isoformat()}T00:00:00+00:00"),
            ).fetchall()
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
            for row in rows:
                connection.execute(
                    "INSERT OR IGNORE INTO monitoring_raw_archive_part_frames(raw_frame_id,archive_part_id,frame_sha256) VALUES (?,?,?)",
                    (row["id"], archive_id, row["frame_sha256"]),
                )
                linked = connection.execute(
                    "SELECT archive_part_id,frame_sha256 FROM monitoring_raw_archive_part_frames WHERE raw_frame_id=?", (row["id"],)
                ).fetchone()
                if linked is None or linked[0] != archive_id or linked[1] != row["frame_sha256"]:
                    raise RetentionError("raw archive index conflicts with existing evidence")
                connection.execute("UPDATE ingest_raw_frames SET raw_frame=X'' WHERE id=?", (row["id"],))
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline station-ingestion retention maintenance")
    parser.add_argument("action", choices=("archive", "aggregate"))
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--day", required=True)
    parser.add_argument("--raw-storage-dir", type=Path)
    parser.add_argument("--storage-mode", choices=("long_term", "validation"), default="long_term")
    arguments = parser.parse_args(argv)
    try:
        if arguments.action == "aggregate":
            result = aggregate_hourly(arguments.database, arguments.day)
        else:
            if arguments.raw_storage_dir is None:
                raise RetentionError("archive requires --raw-storage-dir")
            result = archive_raw_day(arguments.database, arguments.day, storage_policy_for_mode(arguments.raw_storage_dir, arguments.storage_mode))
    except (RetentionError, sqlite3.Error):
        return 2
    print(json.dumps({"action": arguments.action, "day": arguments.day, "processed": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
