"""Independent, loopback-by-default TCP receiver for the station-ingestion evidence base.

The receiver persists raw frames before acknowledging them. It deliberately does not
project values, update station status, or invoke alert/work-order logic.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Literal, Protocol

try:  # Support both package tests and direct container execution.
    from .sl651_parser import (
        MAX_FRAME_BYTES, PARSER_VERSION, FrameError, ParsedFrame, build_ack, parse_frame,
    )
    from .hj212_parser import HJ212_PARSER_VERSION, ParsedHJ212Frame, build_hj212_9011_response, parse_hj212_frame
    from .ingestion_framing import extract_ingestion_frames
    from .station_ingest_retention import RetentionError, StoragePolicy, check_storage_policy, storage_policy_for_mode
except ImportError:  # pragma: no cover - direct `python sl651_server.py` entry point
    from sl651_parser import (
        MAX_FRAME_BYTES, PARSER_VERSION, FrameError, ParsedFrame, build_ack, parse_frame,
    )
    from hj212_parser import HJ212_PARSER_VERSION, ParsedHJ212Frame, build_hj212_9011_response, parse_hj212_frame
    from ingestion_framing import extract_ingestion_frames
    from station_ingest_retention import RetentionError, StoragePolicy, check_storage_policy, storage_policy_for_mode
try:
    from .station_monitoring import normalize_raw_frame
except ImportError:  # pragma: no cover - direct `python sl651_server.py` entry point
    from station_monitoring import normalize_raw_frame
try:
    from .migrate_station_ingestion import (
        MigrationError, station_ingestion_foreign_key_violations, verify_station_monitoring_contract,
    )
except ImportError:  # pragma: no cover - direct `python sl651_server.py` entry point
    from migrate_station_ingestion import (
        MigrationError, station_ingestion_foreign_key_violations, verify_station_monitoring_contract,
    )

LOGGER = logging.getLogger("station_ingest")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 0
DEFAULT_MAX_CONNECTIONS = 32
DEFAULT_MAX_CONNECTIONS_PER_SOURCE = 4
DEFAULT_SOURCE_ERROR_BUDGET = 8
DEFAULT_SOURCE_COOLDOWN_SECONDS = 5.0
DEFAULT_SOURCE_CONNECTION_BURST = 8
DEFAULT_SOURCE_CONNECTION_WINDOW_SECONDS = 1.0
DEFAULT_SOURCE_STATE_CAPACITY = 1024
DEFAULT_QUEUE_MAX_FRAMES = 10_000
DEFAULT_QUEUE_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_ERROR_QUEUE_MAX = 256
DEFAULT_NORMALIZATION_SCAN_PAGE_SIZE = 64
DEFAULT_NORMALIZATION_SCAN_SECONDS = 0.1
DEFAULT_MAPPING_PATH = Path(__file__).with_name("sl651_mapping.json")


class StorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessingResult:
    """Persistence outcome kept separate from the optional protocol reply."""
    reply: bytes | None
    keep_connection: bool


class IngestFrame(Protocol):
    raw: bytes
    station_code: str
    password: bytes
    body_length: int
    protocol_family: str
    parser_version: str

    @property
    def logical_key(self) -> str: ...


@dataclass(frozen=True)
class AuthenticationResult:
    status: Literal["authenticated", "unbound_authenticated", "unknown_endpoint", "credential_failed"]
    endpoint_id: int | None

    @property
    def may_acknowledge(self) -> bool:
        return self.status in {"authenticated", "unbound_authenticated"}


@dataclass
class _QueuedWork:
    kind: Literal["frame", "error", "stop"]
    raw: bytes = b""
    received_at: str = ""
    reply: asyncio.Future[ProcessingResult] | None = None
    error_code: str | None = None


@dataclass
class _SourceState:
    active_connections: int = 0
    error_count: int = 0
    cooldown_until: float = 0.0
    attempts: deque[float] = field(default_factory=deque)
    last_seen: float = 0.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def credential_hmac(password: bytes, pepper: str) -> str:
    if not pepper:
        raise StorageError("credential pepper is not configured")
    return hmac.new(pepper.encode("utf-8"), password, hashlib.sha256).hexdigest()


def load_supported_uplink_function_codes(mapping_path: Path = DEFAULT_MAPPING_PATH) -> frozenset[int]:
    """Load the explicitly approved first-phase uplink function codes.

    The bootstrap limits this receiver to water-quality timing reports (32H). A mapping
    file that attempts to silently expand that boundary is rejected at startup.
    """
    try:
        document = json.loads(Path(mapping_path).read_text(encoding="utf-8"))
        values = document["supported_uplink_function_codes"]
        codes = frozenset(int(str(value), 16) for value in values)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise StorageError("supported uplink function-code mapping is unavailable") from exc
    if codes != frozenset({0x32}):
        raise StorageError("first-phase station ingestion supports only uplink function code 32H")
    return codes


class IngestionStorage:
    def __init__(self, database: Path, credential_pepper: str, *, storage_policy: StoragePolicy | None = None):
        self.database = Path(database)
        self.credential_pepper = credential_pepper
        self.storage_policy = storage_policy

    def _require_raw_storage_capacity(self) -> None:
        if self.storage_policy is None:
            return
        try:
            check_storage_policy(self.database, self.storage_policy)
        except RetentionError as exc:
            raise StorageError("raw archive storage is unavailable") from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database), timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        # WAL is established by the versioned migration. Do not change journal mode per
        # receiver connection: that operation itself competes with a concurrent Web writer.
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def healthcheck(self) -> None:
        """Lightweight schema/readability probe suitable for a frequent container check."""
        self._require_raw_storage_capacity()
        with closing(self._connect()) as connection:
            try:
                verify_station_monitoring_contract(connection)
            except MigrationError as exc:
                raise StorageError(f"station monitoring schema unavailable: {exc}") from exc
            if connection.execute("SELECT 1").fetchone()[0] != 1:
                raise StorageError("database readability probe failed")
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0].lower()
            if journal_mode != "wal":
                raise StorageError("station ingestion requires WAL mode")

    def full_integrity_check(self) -> None:
        """Low-frequency maintenance/recovery validation; never used by container healthchecks."""
        with closing(self._connect()) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise StorageError("database integrity check failed")
            if station_ingestion_foreign_key_violations(connection):
                raise StorageError("station ingestion foreign key check failed")

    def authenticate(self, frame: IngestFrame) -> AuthenticationResult:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT id, credential_hmac, enabled, endpoint_state FROM trusted_endpoints WHERE station_code=?",
                (frame.station_code,),
            ).fetchone()
        if not row or not row["enabled"] or row["endpoint_state"] == "disabled":
            return AuthenticationResult("unknown_endpoint", None)
        supplied = credential_hmac(frame.password, self.credential_pepper)
        if not hmac.compare_digest(supplied, row["credential_hmac"]):
            return AuthenticationResult("credential_failed", None)
        if row["endpoint_state"] == "unbound":
            return AuthenticationResult("unbound_authenticated", row["id"])
        return AuthenticationResult("authenticated", row["id"])

    def _write_attempt(
        self, connection: sqlite3.Connection, raw_id: int, status: str, error_code: str | None = None,
        *, parser_version: str = PARSER_VERSION,
    ) -> None:
        connection.execute(
            "INSERT INTO ingest_parse_attempts(raw_frame_id, parser_version, parse_status, error_code) VALUES (?, ?, ?, ?)",
            (raw_id, parser_version, status, error_code),
        )

    def _write_error(self, connection: sqlite3.Connection, raw_id: int | None, error_type: str) -> None:
        connection.execute(
            "INSERT INTO ingest_errors(raw_frame_id, error_type, error_detail) VALUES (?, ?, '')",
            (raw_id, error_type),
        )

    def persist_unparseable(
        self, raw: bytes, error_code: str, received_at: str, *, protocol_family: str = "unknown",
        parser_version: str = "unknown",
    ) -> int:
        """Persist a complete frame that failed structural/CRC validation; never acknowledge it."""
        self._require_raw_storage_capacity()
        frame_hash = hashlib.sha256(raw).hexdigest()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """INSERT INTO ingest_raw_frames(
                    endpoint_id, station_code, received_at, frame_sha256, logical_key_sha256,
                    raw_frame, body_length, crc_status, authentication_status, disposition, persistence_state
                ) VALUES (NULL, NULL, ?, ?, NULL, ?, NULL, ?, 'not_checked', 'quarantined', 'persisted')""",
                (received_at, frame_hash, raw, "invalid" if error_code == "crc_mismatch" else "not_checked"),
            )
            raw_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO ingest_frame_protocols(raw_frame_id, protocol_family, parser_version) VALUES (?, ?, ?)",
                (raw_id, protocol_family, parser_version),
            )
            self._write_attempt(connection, raw_id, "failed_header", error_code, parser_version=parser_version)
            self._write_error(connection, raw_id, error_code)
            connection.commit()
            return raw_id

    def record_connection_error(self, error_code: str) -> None:
        # Framing errors are raw-receipt evidence too. They may not bypass the same
        # capacity guard that protects complete parseable and unparseable frames.
        self._require_raw_storage_capacity()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._write_error(connection, None, error_code)
            connection.commit()

    def persist_parsed(
        self,
        frame: IngestFrame,
        auth: AuthenticationResult,
        received_at: str,
        *,
        quarantine_error: str | None = None,
        configuration_pending: bool = False,
    ) -> tuple[int, bool]:
        """Persist one parsed frame and its attempt. Returns raw id and duplicate status."""
        self._require_raw_storage_capacity()
        frame_hash = hashlib.sha256(frame.raw).hexdigest()
        logical_hash = hashlib.sha256(frame.logical_key.encode("ascii")).hexdigest()
        disposition = "pending_parse" if auth.status == "authenticated" and not quarantine_error and not configuration_pending else "quarantined"
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            duplicate_of = None
            if auth.endpoint_id is not None:
                existing = connection.execute(
                    """SELECT id FROM ingest_raw_frames
                       WHERE endpoint_id=? AND logical_key_sha256=? AND duplicate_of_raw_frame_id IS NULL
                       ORDER BY id ASC LIMIT 1""",
                    (auth.endpoint_id, logical_hash),
                ).fetchone()
                duplicate_of = int(existing["id"]) if existing else None
            if duplicate_of is not None:
                disposition = "duplicate"
            cursor = connection.execute(
                """INSERT INTO ingest_raw_frames(
                    endpoint_id, station_code, received_at, frame_sha256, logical_key_sha256, raw_frame,
                    body_length, crc_status, authentication_status, disposition, duplicate_of_raw_frame_id, persistence_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'valid', ?, ?, ?, ?)""",
                (
                    auth.endpoint_id,
                    frame.station_code,
                    received_at,
                    frame_hash,
                    logical_hash,
                    frame.raw,
                    frame.body_length,
                    auth.status,
                    disposition,
                    duplicate_of,
                    "persisted" if disposition in {"duplicate", "quarantined"} else "pending_parse",
                ),
            )
            raw_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO ingest_frame_protocols(raw_frame_id, protocol_family, parser_version) VALUES (?, ?, ?)",
                (raw_id, frame.protocol_family, frame.parser_version),
            )
            self._write_attempt(connection, raw_id, "parsed_header", quarantine_error, parser_version=frame.parser_version)
            if quarantine_error:
                self._write_error(connection, raw_id, quarantine_error)
            elif configuration_pending:
                self._write_error(connection, raw_id, "configuration_pending")
            elif auth.status in {"unknown_endpoint", "credential_failed"}:
                self._write_error(connection, raw_id, auth.status)
            elif auth.status == "unbound_authenticated":
                self._write_error(connection, raw_id, "unbound_authenticated_endpoint")
            connection.commit()
            return raw_id, duplicate_of is not None

    def endpoint_has_monitoring_profile(self, endpoint_id: int) -> bool:
        with closing(self._connect()) as connection:
            return connection.execute(
                "SELECT 1 FROM monitoring_endpoint_profiles WHERE endpoint_id=? AND enabled=1 LIMIT 1", (endpoint_id,)
            ).fetchone() is not None

    def pending_normalization_page(self, after_id: int, limit: int) -> list[int]:
        """Read a bounded due-work page; callers must not materialize the full backlog."""
        with closing(self._connect()) as connection:
            return [
                int(row[0])
                for row in connection.execute(
                    """SELECT raw.id FROM ingest_raw_frames raw
                       LEFT JOIN monitoring_normalization_retries retry
                         ON retry.raw_frame_id=raw.id AND retry.normalization_version=?
                       WHERE raw.id>? AND raw.persistence_state IN ('pending_parse', 'pending_reparse')
                         AND (retry.raw_frame_id IS NULL OR (retry.state!='exhausted'
                              AND (retry.next_attempt_at IS NULL OR retry.next_attempt_at<=?)))
                       ORDER BY raw.id LIMIT ?""",
                    ("station-monitoring-normalizer-v1", after_id, _utc_now(), limit),
                )
            ]

    def record_normalization_retry(self, raw_id: int, error_type: str, *, max_attempts: int = 3) -> None:
        """Persist bounded retry state; exhausted work becomes a stable diagnostic."""
        version = "station-monitoring-normalizer-v1"
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT attempt_count FROM monitoring_normalization_retries WHERE raw_frame_id=? AND normalization_version=?",
                (raw_id, version),
            ).fetchone()
            attempt = int(row[0]) + 1 if row else 1
            now = datetime.now(timezone.utc)
            if attempt >= max_attempts:
                connection.execute(
                    """INSERT INTO monitoring_normalization_retries(raw_frame_id, normalization_version, attempt_count, last_error, state)
                       VALUES (?, ?, ?, ?, 'exhausted')
                       ON CONFLICT(raw_frame_id, normalization_version) DO UPDATE SET
                         attempt_count=excluded.attempt_count, last_error=excluded.last_error, state='exhausted', next_attempt_at=NULL""",
                    (raw_id, version, max_attempts, error_type),
                )
                connection.execute("UPDATE ingest_raw_frames SET persistence_state='persisted' WHERE id=?", (raw_id,))
                issue = connection.execute(
                    "SELECT id FROM monitoring_quality_issues WHERE raw_frame_id=? AND issue_type='normalization_retry_exhausted' AND status='open'",
                    (raw_id,),
                ).fetchone()
                if issue:
                    connection.execute("UPDATE monitoring_quality_issues SET last_seen_at=? WHERE id=?", (_utc_now(), issue[0]))
                else:
                    connection.execute(
                        """INSERT INTO monitoring_quality_issues(raw_frame_id, issue_type, object_summary, first_seen_at, last_seen_at, reparse_allowed)
                           VALUES (?, 'normalization_retry_exhausted', ?, ?, ?, 1)""",
                        (raw_id, error_type[:160], _utc_now(), _utc_now()),
                    )
            else:
                next_attempt = (now + timedelta(seconds=2 ** (attempt - 1))).replace(microsecond=0).isoformat()
                connection.execute(
                    """INSERT INTO monitoring_normalization_retries(raw_frame_id, normalization_version, attempt_count, next_attempt_at, last_error, state)
                       VALUES (?, ?, ?, ?, ?, 'retrying')
                       ON CONFLICT(raw_frame_id, normalization_version) DO UPDATE SET
                         attempt_count=excluded.attempt_count, next_attempt_at=excluded.next_attempt_at,
                         last_error=excluded.last_error, state='retrying'""",
                    (raw_id, version, attempt, next_attempt, error_type),
                )
            connection.commit()


class StationIngestServer:
    def __init__(
        self,
        storage: IngestionStorage,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
        max_connections_per_source: int = DEFAULT_MAX_CONNECTIONS_PER_SOURCE,
        source_error_budget: int = DEFAULT_SOURCE_ERROR_BUDGET,
        source_cooldown_seconds: float = DEFAULT_SOURCE_COOLDOWN_SECONDS,
        source_connection_burst: int = DEFAULT_SOURCE_CONNECTION_BURST,
        source_connection_window_seconds: float = DEFAULT_SOURCE_CONNECTION_WINDOW_SECONDS,
        source_state_capacity: int = DEFAULT_SOURCE_STATE_CAPACITY,
        queue_max_frames: int = DEFAULT_QUEUE_MAX_FRAMES,
        queue_max_bytes: int = DEFAULT_QUEUE_MAX_BYTES,
        error_queue_max: int = DEFAULT_ERROR_QUEUE_MAX,
        read_timeout_seconds: float = 10.0,
        mapping_path: Path = DEFAULT_MAPPING_PATH,
    ):
        self.storage = storage
        self.host = host
        self.port = port
        self.max_connections = max_connections
        self.max_connections_per_source = max_connections_per_source
        self.source_error_budget = source_error_budget
        self.source_cooldown_seconds = source_cooldown_seconds
        self.source_connection_burst = source_connection_burst
        self.source_connection_window_seconds = source_connection_window_seconds
        self.source_state_capacity = source_state_capacity
        self.queue_max_bytes = queue_max_bytes
        self.error_queue_max = error_queue_max
        self.read_timeout_seconds = read_timeout_seconds
        self.supported_uplink_function_codes = load_supported_uplink_function_codes(mapping_path)
        self._queue: asyncio.Queue[_QueuedWork] = asyncio.Queue(maxsize=queue_max_frames + error_queue_max)
        self._queue_max_frames = queue_max_frames
        self._queued_frames = 0
        self._queued_errors = 0
        self._queued_bytes = 0
        self.queue_peak_frames = 0
        self.queue_peak_bytes = 0
        self.dropped_error_events = 0
        self.failed_error_persistence = 0
        self._queued_bytes_lock = asyncio.Lock()
        self._active_connections = 0
        self._connection_lock = asyncio.Lock()
        self._sources: dict[str, _SourceState] = {}
        self._server: asyncio.AbstractServer | None = None
        self._worker: asyncio.Task[None] | None = None
        self._normalization_queue: asyncio.Queue[int] = asyncio.Queue(maxsize=queue_max_frames)
        self._normalizer: asyncio.Task[None] | None = None
        self._normalization_scanner: asyncio.Task[None] | None = None
        self._normalization_wakeup = asyncio.Event()
        self._scheduled_normalizations: set[int] = set()
        self._normalization_stopping = False
        self._event_loop: asyncio.AbstractEventLoop | None = None

    @property
    def bound_port(self) -> int:
        if not self._server or not self._server.sockets:
            return 0
        return int(self._server.sockets[0].getsockname()[1])

    @property
    def queued_frames(self) -> int:
        return self._queued_frames

    @property
    def queued_bytes(self) -> int:
        return self._queued_bytes

    async def start(self) -> None:
        self.storage.healthcheck()
        self._event_loop = asyncio.get_running_loop()
        self._worker = asyncio.create_task(self._persistence_worker(), name="station-ingest-persistence")
        self._normalizer = asyncio.create_task(self._normalization_worker(), name="station-monitoring-normalization")
        self._normalization_scanner = asyncio.create_task(self._normalization_scan_loop(), name="station-monitoring-scan")
        self._normalization_wakeup.set()
        self._server = await asyncio.start_server(self._handle_connection, self.host, self.port, limit=MAX_FRAME_BYTES)

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if self._worker:
            await self._queue.join()
            await self._queue.put(_QueuedWork("stop"))
            await self._worker
        if self._normalization_scanner:
            self._normalization_stopping = True
            self._normalization_wakeup.set()
            await self._normalization_scanner
        if self._normalizer:
            # Let in-flight SQLite work close its connection before cancelling the idle
            # task.  Cancelling asyncio.to_thread() alone leaves its worker thread alive.
            await self._normalization_queue.join()
            self._normalizer.cancel()
            try:
                await self._normalizer
            except asyncio.CancelledError:
                pass

    def _source_key(self, writer: asyncio.StreamWriter) -> str:
        peer = writer.get_extra_info("peername")
        return str(peer[0]) if isinstance(peer, tuple) and peer else "unknown"

    def _prune_sources(self, now: float) -> None:
        stale = [
            key for key, state in self._sources.items()
            if not state.active_connections and state.cooldown_until <= now and now - state.last_seen > self.source_connection_window_seconds
        ]
        for key in stale:
            del self._sources[key]

    async def _admit_connection(self, source: str) -> str | None:
        now = asyncio.get_running_loop().time()
        async with self._connection_lock:
            self._prune_sources(now)
            if self._active_connections >= self.max_connections:
                return "global_connection_limit"
            state = self._sources.get(source)
            if state is None:
                if len(self._sources) >= self.source_state_capacity:
                    return "source_tracking_capacity"
                state = self._sources[source] = _SourceState(last_seen=now)
            while state.attempts and now - state.attempts[0] > self.source_connection_window_seconds:
                state.attempts.popleft()
            state.last_seen = now
            if state.cooldown_until > now:
                return "source_cooling"
            if len(state.attempts) >= self.source_connection_burst:
                state.cooldown_until = now + self.source_cooldown_seconds
                return "source_connection_burst"
            if state.active_connections >= self.max_connections_per_source:
                return "source_connection_limit"
            state.attempts.append(now)
            state.active_connections += 1
            self._active_connections += 1
            return None

    async def _release_connection(self, source: str) -> None:
        async with self._connection_lock:
            self._active_connections = max(0, self._active_connections - 1)
            state = self._sources.get(source)
            if state:
                state.active_connections = max(0, state.active_connections - 1)
                state.last_seen = asyncio.get_running_loop().time()

    def _note_source_error(self, source: str, *, count_toward_budget: bool) -> None:
        if not count_toward_budget:
            return
        state = self._sources.get(source)
        if not state:
            return
        state.error_count += 1
        state.last_seen = asyncio.get_running_loop().time()
        if state.error_count >= self.source_error_budget:
            state.cooldown_until = max(state.cooldown_until, state.last_seen + self.source_cooldown_seconds)

    def _enqueue_error(self, error_code: str, source: str | None = None, *, count_toward_budget: bool = True) -> None:
        """Schedule bounded error persistence without opening SQLite on the event loop."""
        if source:
            self._note_source_error(source, count_toward_budget=count_toward_budget)
        if self._queued_errors >= self.error_queue_max:
            self.dropped_error_events += 1
            return
        try:
            self._queue.put_nowait(_QueuedWork("error", error_code=error_code))
            self._queued_errors += 1
        except asyncio.QueueFull:
            self.dropped_error_events += 1

    def _schedule_normalization(self, raw_id: int) -> None:
        if self._event_loop and self._normalizer:
            self._event_loop.call_soon_threadsafe(self._normalization_wakeup.set)

    async def _normalization_scan_loop(self) -> None:
        """Continuously page durable work into the bounded queue without restart reliance."""
        cursor = 0
        while not self._normalization_stopping:
            available = self._normalization_queue.maxsize - self._normalization_queue.qsize()
            if available <= 0:
                await self._normalization_wakeup.wait()
                self._normalization_wakeup.clear()
                continue
            raw_ids = await asyncio.to_thread(
                self.storage.pending_normalization_page, cursor, min(available, DEFAULT_NORMALIZATION_SCAN_PAGE_SIZE)
            )
            if self._normalization_stopping:
                return
            if not raw_ids:
                cursor = 0
                try:
                    await asyncio.wait_for(self._normalization_wakeup.wait(), timeout=DEFAULT_NORMALIZATION_SCAN_SECONDS)
                except asyncio.TimeoutError:
                    pass
                self._normalization_wakeup.clear()
                continue
            for raw_id in raw_ids:
                if self._normalization_stopping:
                    return
                cursor = raw_id
                if raw_id in self._scheduled_normalizations:
                    continue
                self._scheduled_normalizations.add(raw_id)
                await self._normalization_queue.put(raw_id)

    async def _normalization_worker(self) -> None:
        while True:
            raw_id = await self._normalization_queue.get()
            try:
                # Normalization is retryable projection work. Never let its SQLite
                # write transaction compete with an acknowledged raw-receipt backlog.
                while self._queued_frames:
                    await asyncio.sleep(0.01)
                await asyncio.to_thread(normalize_raw_frame, self.storage.database, raw_id)
            except Exception as exc:
                LOGGER.warning("station monitoring normalization deferred: %s", type(exc).__name__)
                try:
                    await asyncio.to_thread(self.storage.record_normalization_retry, raw_id, type(exc).__name__)
                except Exception as retry_exc:
                    LOGGER.warning("station monitoring retry evidence failed: %s", type(retry_exc).__name__)
            finally:
                self._scheduled_normalizations.discard(raw_id)
                self._normalization_queue.task_done()
                self._normalization_wakeup.set()

    async def _persistence_worker(self) -> None:
        """Keep the sole persistence worker alive across individual SQLite failures.

        Raw-frame persistence failures suppress their matching acknowledgement. Error-event
        persistence is best-effort and bounded: it must not take the receiver down or
        prevent later raw frames from reaching the same worker.
        """
        while True:
            item = await self._queue.get()
            try:
                if item.kind == "stop":
                    return
                if item.kind == "error":
                    try:
                        await asyncio.to_thread(
                            self.storage.record_connection_error,
                            item.error_code or "unknown_connection_error",
                        )
                    except Exception as exc:
                        self.failed_error_persistence += 1
                        LOGGER.warning("station ingest error evidence persistence failed: %s", type(exc).__name__)
                    continue
                try:
                    outcome = await asyncio.to_thread(self._process_raw_result, item.raw, item.received_at)
                except Exception as exc:  # raw persistence failure must never generate an acknowledgement
                    LOGGER.warning("station ingest raw persistence failed: %s", type(exc).__name__)
                    outcome = ProcessingResult(None, False)
                if item.reply is not None and not item.reply.done():
                    item.reply.set_result(outcome)
            except Exception as exc:  # defensive last line: only an explicit stop may end this worker
                LOGGER.warning("station ingest persistence worker item failed: %s", type(exc).__name__)
                if item.reply is not None and not item.reply.done():
                    item.reply.set_result(ProcessingResult(None, False))
            finally:
                if item.kind == "frame":
                    async with self._queued_bytes_lock:
                        self._queued_frames = max(0, self._queued_frames - 1)
                        self._queued_bytes = max(0, self._queued_bytes - len(item.raw))
                elif item.kind == "error":
                    self._queued_errors = max(0, self._queued_errors - 1)
                self._queue.task_done()

    def _process_raw_result(self, raw: bytes, received_at: str) -> ProcessingResult:
        try:
            frame: ParsedFrame | ParsedHJ212Frame
            frame = parse_hj212_frame(raw) if raw.startswith(b"##") else parse_frame(raw)
        except FrameError as exc:
            self.storage.persist_unparseable(
                raw, exc.code, received_at,
                protocol_family="hj212" if raw.startswith(b"##") else "sl651",
                parser_version=HJ212_PARSER_VERSION if raw.startswith(b"##") else PARSER_VERSION,
            )
            return ProcessingResult(None, False)
        if isinstance(frame, ParsedHJ212Frame):
            auth = self.storage.authenticate(frame)
            if frame.command == "3020":
                self.storage.persist_parsed(frame, auth, received_at, quarantine_error="hj212_non_data_command")
                return ProcessingResult(build_hj212_9011_response(frame), True) if auth.may_acknowledge else ProcessingResult(None, False)
            if frame.command != "2011":
                self.storage.persist_parsed(frame, auth, received_at, quarantine_error="hj212_non_data_command")
                return ProcessingResult(None, False)
            configuration_pending = (
                auth.status == "authenticated"
                and not self.storage.endpoint_has_monitoring_profile(int(auth.endpoint_id or 0))
            )
            raw_id, duplicate = self.storage.persist_parsed(
                frame, auth, received_at, configuration_pending=configuration_pending,
            )
            if auth.status == "authenticated" and not duplicate:
                if not configuration_pending:
                    self._schedule_normalization(raw_id)
            # CN=2011 has no confirmed application reply, but a durable receipt is
            # successful and must not discard later sticky frames on this TCP stream.
            return ProcessingResult(None, auth.status in {"authenticated", "unbound_authenticated"})
        if frame.direction != "up":
            self.storage.persist_unparseable(raw, "unexpected_downlink", received_at, protocol_family="sl651", parser_version=PARSER_VERSION)
            return ProcessingResult(None, False)
        auth = self.storage.authenticate(frame)
        if frame.function_code not in self.supported_uplink_function_codes:
            self.storage.persist_parsed(frame, auth, received_at, quarantine_error="unsupported_function_code")
            return ProcessingResult(None, False)
        raw_id, duplicate = self.storage.persist_parsed(frame, auth, received_at)
        # Normalization is deliberately after the durable raw receipt and ACK path.
        # A mapping or parser defect remains retryable evidence on restart.
        if auth.status == "authenticated" and not duplicate:
            self._schedule_normalization(raw_id)
        if not auth.may_acknowledge:
            return ProcessingResult(None, False)
        return ProcessingResult(build_ack(frame), True)

    def _process_raw(self, raw: bytes, received_at: str) -> bytes | None:
        """Compatibility seam for direct storage tests; networking uses the full result."""
        return self._process_raw_result(raw, received_at).reply

    async def _enqueue(self, raw: bytes, source: str) -> ProcessingResult:
        if len(raw) > MAX_FRAME_BYTES:
            self._enqueue_error("frame_too_large", source)
            return ProcessingResult(None, False)
        async with self._queued_bytes_lock:
            if self._queued_frames >= self._queue_max_frames or self._queued_bytes + len(raw) > self.queue_max_bytes:
                self._enqueue_error("queue_full", source)
                return ProcessingResult(None, False)
            self._queued_frames += 1
            self._queued_bytes += len(raw)
            self.queue_peak_frames = max(self.queue_peak_frames, self._queued_frames)
            self.queue_peak_bytes = max(self.queue_peak_bytes, self._queued_bytes)
            reply: asyncio.Future[ProcessingResult] = asyncio.get_running_loop().create_future()
            try:
                self._queue.put_nowait(_QueuedWork("frame", raw, _utc_now(), reply))
            except asyncio.QueueFull:  # reserved error capacity should make this unreachable.
                self._queued_frames -= 1
                self._queued_bytes -= len(raw)
                self._enqueue_error("queue_full", source)
                return ProcessingResult(None, False)
        try:
            return await asyncio.wait_for(reply, timeout=self.read_timeout_seconds)
        except asyncio.TimeoutError:
            self._enqueue_error("persistence_timeout", source)
            return ProcessingResult(None, False)

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        source = self._source_key(writer)
        rejection = await self._admit_connection(source)
        if rejection:
            self._enqueue_error(rejection, source, count_toward_budget=False)
            writer.close()
            await writer.wait_closed()
            return
        buffer = b""
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=self.read_timeout_seconds)
                except asyncio.TimeoutError:
                    self._enqueue_error("read_timeout", source)
                    break
                if not chunk:
                    break
                buffer += chunk
                frames, buffer, errors = extract_ingestion_frames(buffer)
                for error_code in errors:
                    self._enqueue_error(error_code, source)
                close_after_batch = False
                for raw in frames:
                    outcome = await self._enqueue(raw, source)
                    if not outcome.keep_connection:
                        close_after_batch = True
                    if outcome.reply is not None:
                        writer.write(outcome.reply)
                        await writer.drain()
                if close_after_batch:
                    writer.close()
                    await writer.wait_closed()
                    return
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            await self._release_connection(source)


async def run_server(
    database: Path,
    *,
    credential_pepper: str,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    max_connections: int = DEFAULT_MAX_CONNECTIONS,
    mapping_path: Path = DEFAULT_MAPPING_PATH,
    storage_policy: StoragePolicy | None = None,
) -> StationIngestServer:
    server = StationIngestServer(
        IngestionStorage(database, credential_pepper, storage_policy=storage_policy),
        host=host,
        port=port,
        max_connections=max_connections,
        mapping_path=mapping_path,
    )
    await server.start()
    return server


async def _listener_healthcheck(host: str, port: int) -> None:
    if port <= 0:
        raise StorageError("healthcheck requires the receiver listener port")
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3)
    del reader
    writer.close()
    await writer.wait_closed()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--host", default=os.environ.get("SL651_BIND_HOST", DEFAULT_HOST))
    parser.add_argument("--port", default=int(os.environ.get("SL651_PORT", DEFAULT_PORT)), type=int)
    parser.add_argument("--max-connections", default=DEFAULT_MAX_CONNECTIONS, type=int)
    parser.add_argument("--mapping", default=DEFAULT_MAPPING_PATH, type=Path)
    parser.add_argument("--raw-storage-dir", default=os.environ.get("SL651_RAW_STORAGE_DIR"), type=Path)
    parser.add_argument("--storage-mode", choices=("long_term", "validation"), default=os.environ.get("SL651_STORAGE_MODE", "long_term"))
    parser.add_argument("--healthcheck", action="store_true")
    arguments = parser.parse_args()
    pepper = os.environ.get("SL651_CREDENTIAL_PEPPER", "")
    storage_policy = storage_policy_for_mode(arguments.raw_storage_dir, arguments.storage_mode) if arguments.raw_storage_dir else None
    storage = IngestionStorage(arguments.database, pepper, storage_policy=storage_policy)
    if arguments.healthcheck:
        storage.healthcheck()
        asyncio.run(_listener_healthcheck(arguments.host, arguments.port))
        return 0
    if not pepper:
        raise SystemExit("SL651_CREDENTIAL_PEPPER must be injected through private runtime configuration")
    if storage_policy is None:
        raise SystemExit("SL651_RAW_STORAGE_DIR must name the independent raw archive storage")

    async def serve() -> None:
        server = await run_server(
            arguments.database,
            credential_pepper=pepper,
            host=arguments.host,
            port=arguments.port,
            max_connections=arguments.max_connections,
            mapping_path=arguments.mapping,
            storage_policy=storage_policy,
        )
        LOGGER.info("station ingestion receiver started on loopback-or-explicit host")
        try:
            await asyncio.Future()
        finally:
            await server.close()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
