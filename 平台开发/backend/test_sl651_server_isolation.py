import ast
import asyncio
import inspect
import json
import sqlite3
import signal
from contextlib import closing
from datetime import datetime, timedelta, timezone
import tempfile
import os
import subprocess
import threading
import time
import unittest
from unittest import mock

import yaml
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import app as web_app
import migrate_station_ingestion as migration
from migrate_station_ingestion import apply_migration
from sl651_parser import UP_FLOW_CONTROL, crc16_modbus, encode_bcd_observation_time, encode_bcd_time, encode_station_code, parse_frame
from hj212_parser import build_hj212_9011_response, hj212_crc, parse_hj212_frame
import sl651_server as ingest_server
import station_ingest_retention as retention
from sl651_server import IngestionStorage, StationIngestServer, StorageError, _listener_healthcheck, credential_hmac


ACK_BYTES = 25


def make_uplink(station="0012345678", password=b"\x12\x34", serial=1, sent_at=None, function_code=0x32, payload=b""):
    sent_at = sent_at or datetime(2020, 6, 12, 2, 0, 0)
    content = serial.to_bytes(2, "big") + encode_bcd_time(sent_at) + payload
    prefix = (
        b"\x7e\x7e\x10" + encode_station_code(station) + password + bytes((function_code,))
        + len(content).to_bytes(2, "big") + b"\x02" + content + bytes((UP_FLOW_CONTROL,))
    )
    return prefix + crc16_modbus(prefix).to_bytes(2, "big")


def make_hj212(station="TESTHJ01", password="long-ascii-password", command="2011", qn="20260911164900001", cp=None):
    if cp is None:
        cp = "DataTime=20260911164900;w01001-Rtd=7.0;w01001-Flag=N" if command == "2011" else ""
    body = f"QN={qn};ST=91;CN={command};PW={password};MN={station};CP=&&{cp}&&".encode("ascii")
    return b"##" + f"{len(body):04d}".encode("ascii") + body + hj212_crc(body).encode("ascii") + b"\r\n"


class StationIngestIsolationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database = self.root / "isolated.db"
        self._previous_app_database = web_app.DB_PATH
        web_app.DB_PATH = str(self.database)
        # Initialise the actual Flask application's schema first so the L1 test can
        # exercise its authenticated write route against the same SQLite database.
        web_app.init_db()
        apply_migration(self.database, self.root / "backups")
        self.pepper = "ephemeral-test-pepper"
        self.storage = IngestionStorage(self.database, self.pepper)
        self.server = StationIngestServer(
            self.storage,
            host="127.0.0.1",
            port=0,
            max_connections=128,
            max_connections_per_source=128,
            source_connection_burst=256,
            queue_max_frames=512,
            queue_max_bytes=2 * 1024 * 1024,
            error_queue_max=32,
            read_timeout_seconds=4,
        )
        await self.server.start()

    async def asyncTearDown(self):
        await self.server.close()
        web_app.DB_PATH = self._previous_app_database
        self.temp_dir.cleanup()

    def add_endpoint(self, state="bound"):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO trusted_endpoints(station_code, credential_hmac, endpoint_state) VALUES (?, ?, ?)",
                ("0012345678", credential_hmac(b"\x12\x34", self.pepper), state),
            )
            connection.commit()

    async def connect(self):
        return await asyncio.open_connection("127.0.0.1", self.server.bound_port)

    async def send_one(self, raw: bytes) -> float:
        started = time.perf_counter()
        reader, writer = await self.connect()
        writer.write(raw)
        await writer.drain()
        acknowledgement = await asyncio.wait_for(reader.readexactly(ACK_BYTES), timeout=self.server.read_timeout_seconds)
        self.assertEqual(parse_frame(acknowledgement).direction, "down")
        writer.close()
        await writer.wait_closed()
        return time.perf_counter() - started

    async def test_dynamic_loopback_port_and_unbound_32h_ack(self):
        self.add_endpoint("unbound")
        reader, writer = await self.connect()
        writer.write(make_uplink())
        await writer.drain()
        ack = await asyncio.wait_for(reader.readexactly(ACK_BYTES), timeout=2)
        self.assertEqual(parse_frame(ack).direction, "down")
        writer.close()
        await writer.wait_closed()
        with closing(sqlite3.connect(self.database)) as connection:
            row = connection.execute("SELECT authentication_status, disposition FROM ingest_raw_frames").fetchone()
        self.assertEqual(tuple(row), ("unbound_authenticated", "quarantined"))

    async def test_unsupported_function_code_is_quarantined_without_success_ack(self):
        self.add_endpoint("bound")
        reader, writer = await self.connect()
        writer.write(make_uplink(function_code=0x31))
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(), timeout=2), b"")
        writer.close()
        await writer.wait_closed()
        with closing(sqlite3.connect(self.database)) as connection:
            raw = connection.execute("SELECT disposition, persistence_state FROM ingest_raw_frames").fetchone()
            error = connection.execute("SELECT error_type FROM ingest_errors").fetchone()
        self.assertEqual(tuple(raw), ("quarantined", "persisted"))
        self.assertEqual(error[0], "unsupported_function_code")

    async def test_unknown_endpoint_is_closed_without_success_ack(self):
        reader, writer = await self.connect()
        writer.write(make_uplink())
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(), timeout=2), b"")
        writer.close()
        await writer.wait_closed()

    async def test_half_frames_and_duplicate_frames_have_separate_receipts(self):
        self.add_endpoint("bound")
        first = make_uplink(serial=8)
        reader, writer = await self.connect()
        writer.write(first[:7])
        await writer.drain()
        await asyncio.sleep(0)
        writer.write(first[7:] + first)
        await writer.drain()
        first_ack = await asyncio.wait_for(reader.readexactly(ACK_BYTES), timeout=2)
        second_ack = await asyncio.wait_for(reader.readexactly(ACK_BYTES), timeout=2)
        self.assertEqual(parse_frame(first_ack).serial_number, 8)
        self.assertEqual(parse_frame(second_ack).serial_number, 8)
        writer.close()
        await writer.wait_closed()
        with closing(sqlite3.connect(self.database)) as connection:
            rows = connection.execute("SELECT disposition, duplicate_of_raw_frame_id FROM ingest_raw_frames ORDER BY id").fetchall()
        self.assertNotEqual(rows[0][0], "duplicate")
        self.assertEqual(rows[1][0], "duplicate")
        self.assertEqual(rows[1][1], 1)

    async def test_hj212_silent_receipts_keep_sticky_connection_for_later_3020(self):
        password = "long-ascii-password"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO trusted_endpoints(station_code,credential_hmac,endpoint_state) VALUES (?,?, 'bound')",
                ("TESTHJ01", credential_hmac(password.encode("ascii"), self.pepper)),
            )
            connection.commit()
        first = make_hj212(qn="20260911164900001")
        second = make_hj212(qn="20260911164900002")
        inquiry = make_hj212(command="3020", qn="20260911164900003")
        reader, writer = await self.connect()
        writer.write(first + second + inquiry)
        await writer.drain()
        reply = await asyncio.wait_for(reader.readuntil(b"\r\n"), timeout=3)
        self.assertEqual(reply, build_hj212_9011_response(parse_hj212_frame(inquiry)))
        writer.close()
        await writer.wait_closed()
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM ingest_raw_frames WHERE station_code='TESTHJ01'"
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT receipt_count FROM station_ingest_slot_summaries"
            ).fetchone()[0], 2)

    async def test_hj212_without_profile_or_schedule_folds_same_value_within_four_hour_slot(self):
        station = "NOSCHEDULE"
        password = "long-ascii-password"
        with closing(sqlite3.connect(self.database)) as connection:
            site_id = connection.execute(
                "INSERT INTO sites(code,name,type) VALUES ('HJ-NO-SCHEDULE','No schedule','water')"
            ).lastrowid
            endpoint_id = connection.execute(
                """INSERT INTO trusted_endpoints(
                       station_code,credential_hmac,business_site_id,endpoint_state)
                   VALUES (?,?,?,'bound')""",
                (station, credential_hmac(password.encode("ascii"), self.pepper), site_id),
            ).lastrowid
            connection.commit()

        first = parse_hj212_frame(make_hj212(
            station=station, password=password, qn="20261020100100001",
            cp=("DataTime=20261020080000;w01001-Rtd=7.1;w01001-Flag=N;"
                "w01014-Rtd=74.5;w01014-Flag=N"),
        ))
        repeated = parse_hj212_frame(make_hj212(
            station=station, password=password, qn="20261020100200001",
            cp=("DataTime=20261020080000;w01001-Rtd=7.1;w01001-Flag=N;"
                "w01014-Rtd=74.5;w01014-Flag=N"),
        ))
        next_minute = parse_hj212_frame(make_hj212(
            station=station, password=password, qn="20261020100300001",
            cp=("DataTime=20261020080100;w01001-Rtd=7.1;w01001-Flag=N;"
                "w01014-Rtd=74.5;w01014-Flag=N"),
        ))
        auth = self.storage.authenticate(first)
        first_id, first_folded = self.storage.persist_parsed(
            first, auth, "2026-10-20T02:01:00+00:00")
        repeated_id, repeated_folded = self.storage.persist_parsed(
            repeated, auth, "2026-10-20T02:02:00+00:00")
        next_id, next_folded = self.storage.persist_parsed(
            next_minute, auth, "2026-10-20T02:03:00+00:00")
        self.assertEqual((first_folded, repeated_folded, next_folded), (False, True, True))
        self.assertEqual(repeated_id, first_id)
        self.assertEqual(next_id, first_id)
        self.assertEqual(ingest_server.normalize_raw_frame(self.database, first_id), "accepted")

        with closing(sqlite3.connect(self.database)) as connection:
            counts = tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (
                "ingest_raw_frames", "observation_batches", "observation_values",
                "monitoring_business_observations", "monitoring_status_events",
            ))
            canonical_time = connection.execute(
                """SELECT batch.observed_at,batch.received_at
                   FROM observation_batches batch WHERE batch.raw_frame_id=?""",
                (first_id,),
            ).fetchone()
            summary = connection.execute(
                """SELECT COUNT(*),SUM(receipt_count) FROM station_ingest_slot_summaries
                   WHERE endpoint_id=?""", (endpoint_id,)
            ).fetchone()
        self.assertEqual(counts, (1, 1, 2, 0, 0))
        self.assertEqual(tuple(canonical_time), (
            "2026-10-20T00:00:00+00:00", "2026-10-20T02:01:00+00:00"))
        self.assertEqual(tuple(summary), (1, 3))

    async def test_hj212_explicit_schedule_folds_repeats_and_retains_changes_and_new_cycle(self):
        station = "SCHEDULED"
        password = "long-ascii-password"
        with closing(sqlite3.connect(self.database)) as connection:
            site_id = connection.execute(
                "INSERT INTO sites(code,name,type) VALUES ('HJ-SCHEDULED','Scheduled','water')"
            ).lastrowid
            endpoint_id = connection.execute(
                """INSERT INTO trusted_endpoints(
                       station_code,credential_hmac,business_site_id,endpoint_state)
                   VALUES (?,?,?,'bound')""",
                (station, credential_hmac(password.encode("ascii"), self.pepper), site_id),
            ).lastrowid
            connection.execute(
                """INSERT INTO monitoring_endpoint_profiles(
                       endpoint_id,business_site_id,timezone,enabled,expected_granularity,
                       expected_interval_seconds,effective_from)
                   VALUES (?,?,'Asia/Shanghai',1,'realtime',14400,'2020-01-01T00:00:00+00:00')""",
                (endpoint_id, site_id),
            )
            connection.execute(
                """INSERT INTO monitoring_business_schedules(
                       endpoint_id,protocol_code,timezone,interval_seconds,anchor_local_time,
                       tolerance_seconds,result_delay_seconds,effective_from)
                   VALUES (?,NULL,'Asia/Shanghai',14400,'00:00:00',1800,7200,
                           '2020-01-01T00:00:00+00:00')""",
                (endpoint_id,),
            )
            connection.commit()

        payloads = (
            ("20261020100100001", "20261020100000", 74.5),
            ("20261020100200001", "20261020100100", 74.5),
            ("20261020100300001", "20261020100200", 75.5),
            ("20261020140100001", "20261020140000", 75.5),
        )
        raw_ids = []
        folded = []
        for offset, (qn, data_time, conductivity) in enumerate(payloads):
            frame = parse_hj212_frame(make_hj212(
                station=station, password=password, qn=qn,
                cp=(f"DataTime={data_time};w01001-Rtd=7.1;w01001-Flag=N;"
                    f"w01014-Rtd={conductivity};w01014-Flag=N"),
            ))
            raw_id, was_folded = self.storage.persist_parsed(
                frame, self.storage.authenticate(frame),
                f"2026-10-20T{2 + offset:02d}:01:00+00:00",
            )
            raw_ids.append(raw_id)
            folded.append(was_folded)
            if not was_folded:
                self.assertEqual(
                    ingest_server.normalize_raw_frame(self.database, raw_id), "accepted")

        self.assertEqual(folded, [False, True, False, False])
        self.assertEqual(raw_ids[1], raw_ids[0])
        with closing(sqlite3.connect(self.database)) as connection:
            counts = tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (
                "ingest_raw_frames", "observation_batches", "observation_values",
                "monitoring_business_observations", "monitoring_status_events",
            ))
            summary = connection.execute(
                """SELECT COUNT(*),SUM(receipt_count) FROM station_ingest_slot_summaries
                   WHERE endpoint_id=?""", (endpoint_id,)
            ).fetchone()
            values_per_batch = [row[0] for row in connection.execute(
                """SELECT COUNT(value.id) FROM observation_batches batch
                   LEFT JOIN observation_values value ON value.observation_batch_id=batch.id
                   GROUP BY batch.id ORDER BY batch.id"""
            )]
        self.assertEqual(counts, (3, 3, 5, 5, 0))
        self.assertEqual(tuple(summary), (3, 4))
        self.assertEqual(values_per_batch, [2, 1, 2])

    async def test_unbound_hj212_receipts_keep_the_same_connection_without_an_application_reply(self):
        password = "long-ascii-password"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO trusted_endpoints(station_code,credential_hmac,endpoint_state) VALUES (?,?, 'unbound')",
                ("TESTHJ01", credential_hmac(password.encode("ascii"), self.pepper)),
            )
            connection.commit()
        reader, writer = await self.connect()
        writer.write(make_hj212(qn="20260911164900001") + make_hj212(qn="20260911164900002"))
        await writer.drain()
        for _ in range(100):
            with closing(sqlite3.connect(self.database)) as connection:
                received = connection.execute(
                    "SELECT COUNT(*) FROM ingest_raw_frames WHERE station_code='TESTHJ01'"
                ).fetchone()[0]
            if received == 2:
                break
            await asyncio.sleep(0.01)
        await asyncio.wait_for(self.server._queue.join(), timeout=3)
        with closing(sqlite3.connect(self.database)) as connection:
            rows = connection.execute(
                "SELECT authentication_status,disposition FROM ingest_raw_frames WHERE station_code='TESTHJ01' ORDER BY id"
            ).fetchall()
            self.assertEqual(rows, [("unbound_authenticated", "quarantined")] * 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM observation_batches").fetchone()[0], 0)
        self.assertFalse(writer.is_closing())
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(reader.readexactly(1), timeout=0.1)
        writer.close()
        await writer.wait_closed()
        del reader

    async def test_hj212_persistence_failure_closes_without_a_silent_success(self):
        password = "long-ascii-password"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO trusted_endpoints(station_code,credential_hmac,endpoint_state) VALUES (?,?, 'bound')",
                ("TESTHJ01", credential_hmac(password.encode("ascii"), self.pepper)),
            )
            connection.commit()
        reader, writer = await self.connect()
        with mock.patch.object(self.storage, "persist_parsed", side_effect=sqlite3.OperationalError("isolated lock")):
            writer.write(make_hj212())
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(), timeout=3), b"")
        writer.close()
        await writer.wait_closed()

    async def test_bad_frame_does_not_discard_later_complete_frame_from_same_read(self):
        self.add_endpoint("bound")
        bad = bytearray(make_uplink(serial=401))
        bad[-1] ^= 1
        good = make_uplink(serial=402)
        reader, writer = await self.connect()
        writer.write(bytes(bad) + good)
        await writer.drain()
        acknowledgement = await asyncio.wait_for(reader.readexactly(ACK_BYTES), timeout=3)
        self.assertEqual(parse_frame(acknowledgement).serial_number, 402)
        self.assertEqual(await asyncio.wait_for(reader.read(), timeout=3), b"")
        writer.close()
        await writer.wait_closed()
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ingest_raw_frames").fetchone()[0], 2)
            self.assertIn("crc_mismatch", {row[0] for row in connection.execute("SELECT error_type FROM ingest_errors")})

    async def test_framing_error_and_following_complete_frame_both_reach_receiver_chain(self):
        self.add_endpoint("bound")
        reader, writer = await self.connect()
        writer.write(b"##ABCD" + make_uplink(serial=403))
        await writer.drain()
        acknowledgement = await asyncio.wait_for(reader.readexactly(ACK_BYTES), timeout=3)
        self.assertEqual(parse_frame(acknowledgement).serial_number, 403)
        await asyncio.wait_for(self.server._queue.join(), timeout=3)
        writer.close()
        await writer.wait_closed()
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ingest_raw_frames").fetchone()[0], 1)
            self.assertIn("hj212_invalid_length", {row[0] for row in connection.execute("SELECT error_type FROM ingest_errors")})

    async def test_error_evidence_write_failure_does_not_kill_persistence_worker(self):
        self.add_endpoint("bound")
        noisy_reader, noisy_writer = await self.connect()
        with mock.patch.object(
            self.storage,
            "record_connection_errors",
            side_effect=sqlite3.OperationalError("isolated error sink failure"),
        ):
            noisy_writer.write(b"noise-for-error-worker")
            await noisy_writer.drain()
            for _ in range(100):
                if self.server.failed_error_persistence:
                    break
                await asyncio.sleep(0.01)
            await asyncio.wait_for(self.server._queue.join(), timeout=2)
            self.assertEqual(self.server.failed_error_persistence, 1)
            self.assertIsNotNone(self.server._worker)
            self.assertFalse(self.server._worker.done())
            self.assertLess(await self.send_one(make_uplink(serial=222)), 2.0)
            self.assertFalse(self.server._worker.done())
        noisy_writer.close()
        await noisy_writer.wait_closed()
        del noisy_reader

    async def test_queue_capacity_exhaustion_never_returns_success_ack(self):
        self.add_endpoint("bound")
        self.server.queue_max_bytes = 1
        reader, writer = await self.connect()
        writer.write(make_uplink(serial=1))
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(), timeout=2), b"")
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.05)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ingest_raw_frames").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ingest_errors WHERE error_type='queue_full'").fetchone()[0], 1)

    async def test_real_write_lock_diagnostics_preserve_no_ack_and_worker_recovery(self):
        self.add_endpoint('unbound')
        original_connect = self.storage._connect

        def impatient_connect(**kwargs):
            connection = original_connect(**kwargs)
            connection.execute('PRAGMA busy_timeout=0')
            return connection

        with closing(sqlite3.connect(self.database, isolation_level=None)) as blocker:
            blocker.execute('BEGIN IMMEDIATE')
            try:
                with mock.patch.object(self.storage, '_connect', side_effect=impatient_connect), mock.patch.object(ingest_server, 'CONNECTION_ERROR_RETRY_BUDGET_SECONDS', 0.1), self.assertLogs('station_ingest', level='WARNING') as logs:
                    reader, writer = await self.connect()
                    writer.write(make_uplink(serial=921))
                    await writer.drain()
                    self.assertEqual(await asyncio.wait_for(reader.read(), timeout=3), b'')
                    writer.close()
                    await writer.wait_closed()
                    self.server._enqueue_error('diagnostic-test-only')
                    await asyncio.wait_for(self.server._queue.join(), timeout=3)
                output = '\n'.join(logs.output)
                self.assertIn('stage=raw_persistence operation=persist_parsed', output)
                self.assertIn('stage=error_evidence operation=record_connection_errors', output)
                self.assertIn('sqlite_code=5 sqlite_name=SQLITE_BUSY reason=database_busy', output)
                self.assertNotIn('diagnostic-test-only', output)
                self.assertNotIn(str(self.database), output)
                self.assertNotIn('BEGIN IMMEDIATE', output)
                self.assertTrue(all(len(line) < 400 for line in logs.output))
                self.assertFalse(self.server._worker.done())
                self.assertEqual(blocker.execute('SELECT COUNT(*) FROM ingest_raw_frames').fetchone()[0], 0)
            finally:
                blocker.rollback()
        await self.send_one(make_uplink(serial=921))
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ingest_raw_frames').fetchone()[0], 1)

    async def test_connection_error_batch_preserves_hours_samples_and_capacity(self):
        boundary = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        events = [('noise' if i % 2 else 'source_cooling',
                   (boundary + timedelta(seconds=i-16)).isoformat()) for i in range(32)]
        statements = []
        original_connect = self.storage._connect

        def traced_connect(**kwargs):
            db = original_connect(**kwargs)
            db.set_trace_callback(statements.append)
            return db

        def snapshot():
            with closing(sqlite3.connect(self.database)) as db:
                return {
                    'hourly': db.execute('''SELECT error_type,bucket_hour,occurrence_count,first_seen_at,last_seen_at
                        FROM station_ingest_error_hourly ORDER BY error_type,bucket_hour''').fetchall(),
                    'samples': db.execute('SELECT error_type,created_at FROM ingest_errors ORDER BY created_at,id').fetchall(),
                    'current': db.execute('SELECT latest_error_at,latest_error_type FROM station_ingest_current_state').fetchall(),
                }

        with mock.patch.object(self.storage, '_connect', side_effect=traced_connect), mock.patch.object(
            ingest_server, 'prune_ingest_error_samples', wraps=retention.prune_ingest_error_samples,
        ) as prune:
            for event in events:
                await asyncio.to_thread(self.storage.record_connection_errors, [event])
            baseline = snapshot()
            self.assertEqual(prune.call_count, 32)
            self.assertEqual(statements.count('BEGIN IMMEDIATE'), 32)
            self.assertEqual(statements.count('COMMIT'), 32)
            with closing(sqlite3.connect(self.database)) as db:
                db.execute('DELETE FROM ingest_errors')
                db.execute('DELETE FROM station_ingest_error_hourly')
                db.execute('DELETE FROM station_ingest_current_state')
                db.commit()
            statements.clear()
            prune.reset_mock()
            with mock.patch.object(ingest_server, '_utc_now', side_effect=[stamp for _, stamp in events]):
                for code, _ in events:
                    self.server._enqueue_error(code)
            self.server._enqueue_error('over-capacity')
            self.assertEqual(self.server._queued_errors, 32)
            self.assertEqual(self.server.dropped_error_events, 1)
            await asyncio.wait_for(self.server._queue.join(), timeout=3)
            self.assertEqual(prune.call_count, 2)
            self.assertEqual(statements.count('BEGIN IMMEDIATE'), 2)
            self.assertEqual(statements.count('COMMIT'), 2)
        self.assertEqual(snapshot(), baseline)
        self.assertEqual(sum(row[2] for row in baseline['hourly']), 32)
        self.assertEqual(len(baseline['hourly']), 4)
        self.assertEqual(len(baseline['samples']), 16)
        self.assertEqual(baseline['samples'][0][1], (boundary-timedelta(seconds=15)).strftime('%Y-%m-%d %H:%M:%S'))
        self.assertEqual(self.server._queued_errors, 0)
        self.assertEqual(self.server.failed_error_persistence, 0)
        # A successful batch still enforces the existing sample cap; no indefinite
        # cleanup postponement is hidden behind the lower number of cleanup calls.
        with mock.patch.object(retention, 'INGEST_ERROR_SAMPLE_MAX_ROWS', 3):
            self.server._enqueue_error('noise')
            await asyncio.wait_for(self.server._queue.join(), timeout=3)
            self.assertEqual(len(snapshot()['samples']), 3)
        print('ERROR_BATCH_COUNTS events=32 hourly_buckets=4 samples=16 transactions=32->2 cleanup=32->2 capacity_drop=1')

    async def test_connection_errors_and_frames_take_bounded_alternating_turns(self):
        self.add_endpoint('unbound')
        order = []
        record = self.storage.record_connection_errors
        process = self.server._process_raw_result

        def errors(events):
            order.append(('errors', len(events)))
            return record(events)

        def frame(*args):
            order.append(('frame', 1))
            return process(*args)

        with mock.patch.object(self.storage, 'record_connection_errors', side_effect=errors), mock.patch.object(
            self.server, '_process_raw_result', side_effect=frame,
        ):
            tasks = [asyncio.create_task(self.server._enqueue(make_uplink(serial=950+i), 'test')) for i in range(8)]
            for _ in range(32):
                self.server._enqueue_error('source_cooling')
            outcomes = await asyncio.wait_for(asyncio.gather(*tasks), timeout=3)
            await asyncio.wait_for(self.server._queue.join(), timeout=3)
        self.assertTrue(all(outcome.reply and parse_frame(outcome.reply).direction == 'down' for outcome in outcomes))
        self.assertEqual([count for kind,count in order if kind=='errors'], [16,16])
        error_positions = [i for i,(kind,_) in enumerate(order) if kind=='errors']
        self.assertTrue(any(kind=='frame' for kind,_ in order[error_positions[0]+1:error_positions[1]]))
        self.assertLess(error_positions[-1], len(order)-1, 'queued errors must run before continuous frames finish')
        self.assertEqual((self.server._queued_errors,self.server.queued_frames,self.server.queued_bytes), (0,0,0))
        print('ERROR_FRAME_TURNS '+json.dumps(order))

    def _error_contention_storage_policy(self):
        archive = self.root / 'error-contention-archive'
        archive.mkdir(exist_ok=True)
        gib = 1024 ** 3
        return retention.storage_policy_for_governance(
            archive, archive_minimum_total_bytes=8*gib, archive_minimum_free_bytes=gib,
            hot_minimum_total_bytes=10*gib, hot_minimum_free_bytes=2*gib,
        )

    async def test_governance_error_capacity_contention_yields_to_tcp_and_results(self):
        from station_monitoring import latest_values, result_trend_page
        site_id = self._recovery_endpoint()
        self.add_endpoint('unbound')
        self.storage.storage_policy = self._error_contention_storage_policy()
        entered = threading.Event()
        attempts, raw_starts, values = [], [], []
        record = self.storage.record_connection_errors
        process = self.server._process_raw_result
        started = time.perf_counter()

        def measured_errors(events):
            began = time.perf_counter()
            entered.set()
            try:
                return record(events)
            finally:
                attempts.append(time.perf_counter()-began)

        def measured_raw(*args):
            raw_starts.append(time.perf_counter()-started)
            return process(*args)

        async def hj_exchange(raw, qn):
            inquiry = make_hj212(command='3020', qn=qn)
            reader, writer = await self.connect()
            try:
                writer.write(raw + inquiry)
                await writer.drain()
                reply = await asyncio.wait_for(reader.readuntil(b'\r\n'), timeout=3)
                self.assertEqual(reply, build_hj212_9011_response(parse_hj212_frame(inquiry)))
                return time.perf_counter()-started
            finally:
                writer.close()
                await writer.wait_closed()

        # Only disk readings are synthetic. Directory probes, health writes, SQLite
        # writer contention, queue dispatch, TCP and normalization execute for real.
        usage = mock.Mock(total=16*1024**3, free=4*1024**3)
        with mock.patch.object(retention.shutil, 'disk_usage', return_value=usage), mock.patch.object(
            self.storage, 'record_connection_errors', side_effect=measured_errors,
        ), mock.patch.object(self.server, '_process_raw_result', side_effect=measured_raw):
            with closing(sqlite3.connect(self.database, isolation_level=None)) as blocker:
                blocker.execute('BEGIN IMMEDIATE')
                locked_at = time.perf_counter()
                try:
                    for _ in range(16):
                        self.server._enqueue_error('capacity_contention')
                    self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                    inquiry_task = asyncio.create_task(hj_exchange(b'', '20261124083000001'))
                    # 3020 needs authenticated reads, not a fictitious 2011 ACK.
                    done, _ = await asyncio.wait({inquiry_task}, timeout=0.4)
                    inquiry_before_unlock = bool(done)
                    tcp_task = asyncio.create_task(self.send_one(make_uplink(serial=991)))
                    await asyncio.sleep(max(0.06, locked_at + 0.5 - time.perf_counter()))
                    self.assertFalse(tcp_task.done(), 'SL651 must not ACK an unsaved frame')
                    with closing(sqlite3.connect(self.database)) as db:
                        self.assertEqual(db.execute('SELECT COUNT(*) FROM ingest_raw_frames').fetchone()[0], 0)
                finally:
                    blocker.rollback()
                inquiry_seconds, ack_seconds = await asyncio.gather(inquiry_task, tcp_task)
            await asyncio.wait_for(self.server._queue.join(), timeout=3)
            for index, (stamp, value) in enumerate((('20261124083100', 7.1), ('20261124093100', 7.2))):
                await hj_exchange(self._recovery_frame(stamp, value), f'20261124100{index}00001')
                await asyncio.wait_for(self.server._queue.join(), timeout=3)
                await asyncio.wait_for(self.server._normalization_queue.join(), timeout=3)
                with closing(sqlite3.connect(self.database)) as db:
                    db.row_factory = sqlite3.Row
                    latest = latest_values(db, site_id, at_time='2026-11-24T03:00:00+00:00')
                    trend = result_trend_page(db, site_id, 'ph', '2026-11-24T00:00:00+00:00', '2026-11-24T03:00:00+00:00')[0]
                    self.assertEqual(latest[0]['standard_value'], value)
                    self.assertEqual(trend[-1]['value'], value)
                    values.append(value)
            with closing(sqlite3.connect(self.database)) as db:
                self.assertEqual(db.execute("SELECT SUM(occurrence_count) FROM station_ingest_error_hourly WHERE error_type='capacity_contention'").fetchone()[0], 16)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM ingest_raw_frames WHERE station_code='0012345678' AND persistence_state='persisted'").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM ingest_raw_frames WHERE persistence_state IN ('pending_parse','pending_reparse')").fetchone()[0], 0)
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        evidence = {'policy_enabled': True, 'synthetic_disk_readings_only': True,
            'inquiry_9011_before_unlock': inquiry_before_unlock, 'inquiry_seconds': inquiry_seconds,
            'raw_dispatch_seconds': raw_starts, 'error_attempt_seconds': attempts,
            'sl651_send_to_ack_seconds': ack_seconds, 'sl651_ack_observed_after_unlock': True,
            'sl651_saved_before_ack': True,
            'latest_and_trend_values': values, 'failed_error_events': self.server.failed_error_persistence}
        print('GOVERNANCE_ERROR_CAPACITY_CONTENTION '+json.dumps(evidence))
        self.assertEqual(self.server.failed_error_persistence, 0)
        self.assertTrue(inquiry_before_unlock, 'auxiliary capacity health write blocked authenticated TCP processing')
        self.assertLess(attempts[0], 0.3, 'error capacity guard bypassed the short lock attempt')

    async def test_governance_error_capacity_failures_reject_writes_and_recover(self):
        self.add_endpoint('unbound')
        policy = self._error_contention_storage_policy()
        self.storage.storage_policy = policy
        gib = 1024 ** 3
        events = [('capacity_failure', '2026-11-24T01:00:00+00:00')]
        rejected = []
        for volume, dimension in (('archive', 'total'), ('archive', 'free'), ('hot', 'total'), ('hot', 'free')):
            def usage(path):
                is_target = (Path(path).resolve() == policy.root.resolve()) == (volume == 'archive')
                total, free = 16*gib, 4*gib
                if is_target:
                    if dimension == 'total':
                        total = (policy.minimum_total_bytes if volume == 'archive' else policy.hot_minimum_total_bytes) - 1
                    else:
                        free = (policy.minimum_free_bytes if volume == 'archive' else policy.hot_minimum_free_bytes) - 1
                return mock.Mock(total=total, free=free)

            with self.subTest(volume=volume, dimension=dimension), mock.patch.object(retention.shutil, 'disk_usage', side_effect=usage):
                with self.assertRaisesRegex(StorageError, 'raw archive storage is unavailable'):
                    self.storage.record_connection_errors(events)
                rejected.append(volume+'_'+dimension)
                if volume == 'archive' and dimension == 'free':
                    with self.assertLogs('station_ingest', level='WARNING') as logs:
                        self.server._enqueue_error('capacity_failure')
                        reader, writer = await self.connect()
                        writer.write(make_uplink(serial=992))
                        await writer.drain()
                        self.assertEqual(await asyncio.wait_for(reader.read(), timeout=2), b'')
                        writer.close()
                        await writer.wait_closed()
                        await asyncio.wait_for(self.server._queue.join(), timeout=2)
                    self.assertTrue(any('stage=error_evidence ' in line for line in logs.output))
                    self.assertTrue(any('stage=raw_persistence ' in line for line in logs.output))
                    self.assertEqual(self.server.failed_error_persistence, 1)
        policy.root.rmdir()
        try:
            with self.assertRaisesRegex(StorageError, 'raw archive storage is unavailable'):
                self.storage.record_connection_errors(events)
            rejected.append('missing_archive')
        finally:
            policy.root.mkdir()
        with mock.patch.object(retention.shutil, 'disk_usage', return_value=mock.Mock(total=16*gib, free=4*gib)):
            with mock.patch.object(retention.tempfile, 'mkstemp', side_effect=PermissionError('isolated unwritable archive')):
                with self.assertRaisesRegex(StorageError, 'raw archive storage is unavailable'):
                    self.storage.record_connection_errors(events)
                rejected.append('unwritable_archive_probe')
            with closing(sqlite3.connect(self.database)) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ingest_raw_frames').fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM ingest_errors WHERE error_type='capacity_failure'").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT status FROM monitoring_storage_health WHERE storage_key='raw_archive'").fetchone()[0], 'degraded')
            self.server._enqueue_error('capacity_recovered')
            await self.send_one(make_uplink(serial=993))
            await asyncio.wait_for(self.server._queue.join(), timeout=2)
            with closing(sqlite3.connect(self.database)) as db:
                self.assertEqual(db.execute("SELECT SUM(occurrence_count) FROM station_ingest_error_hourly WHERE error_type='capacity_recovered'").fetchone()[0], 1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ingest_raw_frames').fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT status FROM monitoring_storage_health WHERE storage_key='raw_archive'").fetchone()[0], 'healthy')
        self.assertEqual(self.server._queued_errors, 0)
        print('GOVERNANCE_CAPACITY_PROTECTION '+json.dumps({'rejected': rejected,
            'failed_error_events': 1, 'no_raw_or_success_ack_during_failure': True,
            'recovered_error_events': 1, 'recovered_sl651_saved_and_acknowledged': 1,
            'health_degraded_then_healthy': True}))

    async def test_governance_error_lock_exhaustion_remains_visible_and_tcp_recovers(self):
        self.add_endpoint('unbound')
        self.storage.storage_policy = self._error_contention_storage_policy()
        with mock.patch.object(retention.shutil, 'disk_usage', return_value=mock.Mock(total=16*1024**3, free=4*1024**3)), mock.patch.object(
            ingest_server, 'CONNECTION_ERROR_RETRY_BUDGET_SECONDS', 0.15,
        ), self.assertLogs('station_ingest', level='WARNING') as logs:
            with closing(sqlite3.connect(self.database, isolation_level=None)) as blocker:
                blocker.execute('BEGIN IMMEDIATE')
                try:
                    for _ in range(16):
                        self.server._enqueue_error('capacity_lock_budget')
                    await asyncio.wait_for(self.server._queue.join(), timeout=1)
                    self.assertEqual(self.server.failed_error_persistence, 16)
                    self.assertEqual(self.server._queued_errors, 0)
                    with closing(sqlite3.connect(self.database)) as db:
                        self.assertEqual(db.execute("SELECT COUNT(*) FROM ingest_errors WHERE error_type='capacity_lock_budget'").fetchone()[0], 0)
                finally:
                    blocker.rollback()
            await self.send_one(make_uplink(serial=994))
        self.assertTrue(any('stage=error_evidence_retry ' in line for line in logs.output))
        self.assertTrue(any('stage=error_evidence ' in line for line in logs.output))
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ingest_raw_frames').fetchone()[0], 1)
        print('GOVERNANCE_ERROR_DEGRADED '+json.dumps({'test_retry_budget_seconds': 0.15,
            'retry_attempts': self.server.error_persistence_retries, 'finally_failed_error_events': 16,
            'terminal_failure_logged': True, 'later_sl651_saved_and_acknowledged': 1}))

    async def test_connection_error_lock_retry_yields_to_real_tcp_and_web(self):
        self.add_endpoint('unbound')
        operator_id, token = self._create_l1_web_credentials()
        attempts = []
        attempted = threading.Event()
        record = self.storage.record_connection_errors

        def measured(events):
            started = time.perf_counter()
            try:
                return record(events)
            finally:
                attempts.append(time.perf_counter()-started)
                attempted.set()

        def web_writes():
            with web_app.app.test_client() as client:
                return [client.post('/api/sites', headers={'Authorization':'Bearer '+token},
                    json={'code':f'LOCK-{i}', 'name':f'lock probe {i}', 'manager_id':operator_id,
                          'gps_lat':28.6, 'gps_lng':115.9}).status_code for i in range(4)]

        with closing(sqlite3.connect(self.database, isolation_level=None)) as blocker:
            blocker.execute('BEGIN IMMEDIATE')
            try:
                with mock.patch.object(self.storage, 'record_connection_errors', side_effect=measured), self.assertLogs('station_ingest', level='WARNING') as logs:
                    for _ in range(32):
                        self.server._enqueue_error('noise')
                    self.assertTrue(await asyncio.to_thread(attempted.wait, 1))
                    tcp_tasks = [asyncio.create_task(self.send_one(make_uplink(serial=970+i))) for i in range(8)]
                    web_task = asyncio.create_task(asyncio.to_thread(web_writes))
                    await asyncio.sleep(0.35)
                    blocker.rollback()
                    latencies = await asyncio.wait_for(asyncio.gather(*tcp_tasks), timeout=4)
                    statuses = await asyncio.wait_for(web_task, timeout=4)
                    await asyncio.wait_for(self.server._queue.join(), timeout=3)
            finally:
                blocker.rollback()
        self.assertEqual(statuses, [201]*4)
        self.assertLess(max(latencies), 2.5)
        self.assertLess(max(attempts), 0.5)
        self.assertEqual(self.server.failed_error_persistence, 0)
        self.assertEqual(self.server._queued_errors, 0)
        self.assertEqual(sum('stage=error_evidence' in line for line in logs.output), 1)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT SUM(occurrence_count) FROM station_ingest_error_hourly WHERE error_type='noise'").fetchone()[0], 32)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ingest_errors WHERE error_type='noise'").fetchone()[0], 32)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ingest_raw_frames').fetchone()[0], 8)
        print(f'ERROR_LOCK_REAL_IO events=32 tcp=8 web_201=4 lock_seconds=0.35 max_ack={max(latencies):.4f} max_error_attempt={max(attempts):.4f} error_attempts={len(attempts)} lost=0')

    async def test_normalization_cleanup_lock_distinguishes_retries_from_final_loss(self):
        import station_monitoring as monitoring
        await self.server.close()
        site_id = self._recovery_endpoint()
        self.add_endpoint('unbound')
        operator_id, token = self._create_l1_web_credentials()
        raw_id = self._save_recovery_sample('20261123083700', 7.8, '2026-11-23T00:37:01+00:00')
        entered = threading.Event()
        release = threading.Event()
        attempted = threading.Event()
        cleanup = monitoring._retire_empty_batch
        record = self.storage.record_connection_errors
        prune = ingest_server.prune_ingest_error_samples
        attempts, cleanups, prunes = [], [], []

        def held_cleanup(db, batch_id, source_id):
            if source_id != raw_id:
                return cleanup(db, batch_id, source_id)
            started = time.perf_counter()
            self.assertTrue(db.in_transaction)
            entered.set()
            self.assertTrue(release.wait(5))
            result = cleanup(db, batch_id, source_id)
            cleanups.append({'held_seconds':time.perf_counter()-started, 'in_write_transaction':db.in_transaction})
            return result

        def measured_record(events):
            started = time.perf_counter()
            try:
                return record(events)
            finally:
                attempts.append(time.perf_counter()-started)
                attempted.set()

        def measured_prune(db, **kwargs):
            started = time.perf_counter()
            result = prune(db, **kwargs)
            prunes.append({'seconds':time.perf_counter()-started, 'in_write_transaction':db.in_transaction})
            return result

        def write_web():
            with web_app.app.test_client() as client:
                return client.post('/api/sites', headers={'Authorization':'Bearer '+token},
                    json={'code':'NORMALIZER-LOCK', 'name':'isolated lock probe', 'manager_id':operator_id,
                          'gps_lat':28.6, 'gps_lng':115.9}).status_code

        with mock.patch.object(monitoring, '_retire_empty_batch', side_effect=held_cleanup), mock.patch.object(
            self.storage, 'record_connection_errors', side_effect=measured_record,
        ), mock.patch.object(ingest_server, 'prune_ingest_error_samples', side_effect=measured_prune), self.assertLogs('station_ingest', level='WARNING') as logs:
            try:
                await self.server.start()
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                for _ in range(32):
                    self.server._enqueue_error('noise')
                self.assertTrue(await asyncio.to_thread(attempted.wait, 1))
                tcp = [asyncio.create_task(self.send_one(make_uplink(serial=980+i))) for i in range(2)]
                web = asyncio.create_task(asyncio.to_thread(write_web))
                await asyncio.sleep(0.25)
                release.set()
                latencies = await asyncio.wait_for(asyncio.gather(*tcp), timeout=4)
                web_status = await asyncio.wait_for(web, timeout=4)
                await asyncio.wait_for(self.server._queue.join(), timeout=3)
                await asyncio.wait_for(self.server._normalization_queue.join(), timeout=3)
            finally:
                release.set()
                await self.server.close()
        self.assertEqual(web_status, 201)
        self.assertGreater(self.server.error_persistence_retries, 0)
        self.assertEqual((self.server.failed_error_persistence, self.server._queued_errors), (0,0))
        self.assertTrue(any('stage=error_evidence_retry ' in line for line in logs.output))
        self.assertFalse(any('stage=error_evidence ' in line for line in logs.output))
        self.assertLess(max(latencies), 2.5)
        self.assertTrue(cleanups and prunes)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT SUM(occurrence_count) FROM station_ingest_error_hourly WHERE error_type='noise'").fetchone()[0], 32)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ingest_errors WHERE error_type='noise'").fetchone()[0], 32)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ingest_raw_frames').fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ingest_raw_frames WHERE persistence_state IN ('pending_parse','pending_reparse')").fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT standard_value FROM monitoring_hourly_last_values WHERE business_site_id=?', (site_id,)).fetchone()[0], 7.8)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        print('NORMALIZER_WRITE_CONTENTION '+json.dumps({'synthetic_hold_not_production_root_cause':True,
            'error_attempt_seconds':attempts, 'retry_attempts':self.server.error_persistence_retries,
            'finally_failed_events':self.server.failed_error_persistence, 'persisted_errors':32,
            'tcp_ack_seconds':latencies, 'tcp_raw_persisted':2, 'web_status':web_status,
            'cleanup':cleanups, 'error_prune':prunes, 'pending_remaining':0}))

    async def test_connection_error_batch_rolls_back_before_retry_without_double_count(self):
        original = self.storage._write_error
        writes = 0

        def fail_inside_batch(*args, **kwargs):
            nonlocal writes
            original(*args, **kwargs)
            writes += 1
            if writes == 2:
                failure = sqlite3.OperationalError('database is locked')
                failure.sqlite_errorcode = sqlite3.SQLITE_BUSY
                failure.sqlite_errorname = 'SQLITE_BUSY'
                raise failure

        with mock.patch.object(self.storage, '_write_error', side_effect=fail_inside_batch):
            for _ in range(32):
                self.server._enqueue_error('noise')
            await asyncio.wait_for(self.server._queue.join(), timeout=3)
        self.assertEqual(writes, 34)
        self.assertEqual(self.server.failed_error_persistence, 0)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT SUM(occurrence_count) FROM station_ingest_error_hourly WHERE error_type='noise'").fetchone()[0], 32)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ingest_errors WHERE error_type='noise'").fetchone()[0], 32)

    async def test_connection_error_lock_budget_counts_each_failed_event_and_finishes(self):
        self.add_endpoint('unbound')
        record = self.storage.record_connection_errors
        with closing(sqlite3.connect(self.database, isolation_level=None)) as blocker:
            blocker.execute('BEGIN IMMEDIATE')
            try:
                with mock.patch.object(ingest_server, 'CONNECTION_ERROR_RETRY_BUDGET_SECONDS', 0.15), mock.patch.object(
                    self.storage, 'record_connection_errors', wraps=record,
                ) as attempts, self.assertLogs('station_ingest', level='WARNING') as logs:
                    for _ in range(32):
                        self.server._enqueue_error('noise')
                    await asyncio.wait_for(self.server._queue.join(), timeout=2)
                self.assertGreaterEqual(attempts.call_count, 4)
                self.assertLessEqual(attempts.call_count, 8)
                self.assertEqual(self.server.failed_error_persistence, 32)
                self.assertEqual(self.server.dropped_error_events, 0)
                self.assertEqual(self.server._queued_errors, 0)
                self.assertEqual(len(logs.output), 2)
                self.assertEqual(sum('stage=error_evidence_retry ' in line for line in logs.output), 1)
                self.assertEqual(sum('stage=error_evidence ' in line for line in logs.output), 1)
                self.assertGreater(self.server.error_persistence_retries, 0)
                self.assertEqual(blocker.execute('SELECT COUNT(*) FROM ingest_errors').fetchone()[0], 0)
            finally:
                blocker.rollback()
        await self.send_one(make_uplink(serial=989))
        self.assertFalse(self.server._worker.done())

    async def test_close_drains_connection_errors_during_retry_then_restarts(self):
        self.add_endpoint('unbound')
        with closing(sqlite3.connect(self.database, isolation_level=None)) as blocker:
            blocker.execute('BEGIN IMMEDIATE')
            try:
                for _ in range(32):
                    self.server._enqueue_error('noise')
                await asyncio.sleep(0.12)
                close_task = asyncio.create_task(self.server.close())
                await asyncio.sleep(0.12)
                self.assertFalse(close_task.done())
                self.assertEqual(self.server._queued_errors, 32)
                blocker.rollback()
                await asyncio.wait_for(close_task, timeout=3)
            finally:
                blocker.rollback()
        self.assertEqual(self.server._queued_errors, 0)
        self.assertEqual(self.server.failed_error_persistence, 0)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT SUM(occurrence_count) FROM station_ingest_error_hourly WHERE error_type='noise'").fetchone()[0], 32)
        await self.server.start()
        await self.send_one(make_uplink(serial=990))

    def _recovery_endpoint(self):
        with closing(sqlite3.connect(self.database)) as db:
            site_id = db.execute("INSERT INTO sites(code,name,type) VALUES ('RECOVERY','recovery','water')").lastrowid
            endpoint = db.execute("""INSERT INTO trusted_endpoints(
                station_code,credential_hmac,business_site_id,endpoint_state)
                VALUES ('TESTHJ01',?,?,'bound')""",
                (credential_hmac(b'long-ascii-password', self.pepper), site_id)).lastrowid
            db.execute("""INSERT INTO monitoring_endpoint_profiles(endpoint_id,business_site_id,
                timezone,expected_granularity,effective_from)
                VALUES (?,?,'Asia/Shanghai','realtime','2020-01-01T00:00:00+00:00')""", (endpoint, site_id))
            db.execute("""INSERT INTO monitoring_factor_mappings(endpoint_id,protocol_code,
                expected_interval_seconds,tolerance_seconds,effective_from)
                VALUES (?,'HJ212:w01001',60,0,'2020-01-01T00:00:00+00:00')""", (endpoint,))
            db.commit()
        return site_id

    def _recovery_frame(self, stamp, value):
        return make_hj212(qn=stamp+'001', cp=f'DataTime={stamp};w01001-Rtd={value};w01001-Flag=N')

    async def test_normalization_order_comparison_preserves_formal_sources(self):
        from station_monitoring import latest_values, result_trend_page, trend_slots, business_slot_diagnostics
        await self.server.close()
        site_id = self._recovery_endpoint()
        with closing(sqlite3.connect(self.database)) as db:
            endpoint_id = db.execute("SELECT id FROM trusted_endpoints WHERE station_code='TESTHJ01'").fetchone()[0]
            db.execute("""INSERT INTO monitoring_business_schedules(endpoint_id,protocol_code,
                timezone,interval_seconds,anchor_local_time,tolerance_seconds,result_delay_seconds,effective_from)
                VALUES (?,'HJ212:w01001','Asia/Shanghai',14400,'00:00:00',600,1800,'2020-01-01T00:00:00+00:00')""", (endpoint_id,))
            db.commit()
        cases = {
            'cross_hour': [('20261123083100', 7, '2026-11-23T00:32:00+00:00'), ('20261123093100', 8, '2026-11-23T01:32:00+00:00')],
            'same_hour_different_value': [('20261123083100', 7, '2026-11-23T00:32:00+00:00'), ('20261123084200', 8, '2026-11-23T00:43:00+00:00')],
            'same_slot_same_value': [('20261123083100', 7, '2026-11-23T00:32:00+00:00'), ('20261123084200', 7, '2026-11-23T00:43:00+00:00')],
            'same_slot_same_value_distinct_payload': [('20261123083100', 7, '2026-11-23T00:32:00+00:00'), ('20261123093100', 7, '2026-11-23T06:32:00+00:00')],
            'formal_same_slot_same_value': [('20261123080000', 7, '2026-11-23T00:32:00+00:00'), ('20261123080000', 7, '2026-11-23T06:32:00+00:00')],
            'formal_three_receipts': [('20261123080000', 7, f'2026-11-23T0{hour}:32:00+00:00') for hour in (0, 2, 6)],
            'formal_receipt_tie': [('20261123080000', 7, '2026-11-23T00:32:00+00:00') for _ in range(3)],
            'formal_receipt_not_id_order': [('20261123080000', 7, f'2026-11-23T0{hour}:32:00+00:00') for hour in (6, 0, 2)],
            'formal_different_value': [('20261123080000', value, f'2026-11-23T0{index}:32:00+00:00') for index, value in enumerate((7, 8, 7))],
            'same_measurement_later_receipt': [('20261123083100', 7, '2026-11-23T00:32:00+00:00'), ('20261123083100', 8, '2026-11-23T02:32:00+00:00')],
            'historical_retransmission': [('20261113083100', 7, '2026-11-23T00:32:00+00:00'), ('20261123083100', 8, '2026-11-23T00:33:00+00:00')],
            'continuous_new_values': [('2026112308'+str(31+i)+'00', 7+i/10, '2026-11-23T00:'+str(32+i)+':00+00:00') for i in range(4)],
            'saved_pending_before_restart': [('20261123083100', 7, '2026-11-23T00:32:00+00:00'), ('20261123084200', 8, '2026-11-23T00:43:00+00:00')],
        }
        results = {}
        for name, samples in cases.items():
            seed = self.root / (name+'-seed.db')
            with closing(sqlite3.connect(self.database)) as source, closing(sqlite3.connect(seed)) as target:
                source.backup(target)
            storage = IngestionStorage(seed, self.pepper)
            raw_ids = []
            for index, (stamp, value, received) in enumerate(samples):
                formal_source = name.startswith('formal_')
                kind = 'Avg' if formal_source else 'Rtd'
                companion = f';w01009-{kind}={index+1};w01009-Flag=N' if formal_source or name == 'same_slot_same_value_distinct_payload' else ''
                frame = parse_hj212_frame(make_hj212(command='2061' if formal_source else '2011', qn=stamp+f'{index:03}', cp=f'DataTime={stamp};w01001-{kind}={value};w01001-Flag=N'+companion))
                raw_id, folded = storage.persist_parsed(frame, storage.authenticate(frame), received)
                if not folded:
                    raw_ids.append(raw_id)
            snapshots = {}
            for order, ids in [('ascending', raw_ids), ('descending', raw_ids[::-1]),
                               ('interleaved', raw_ids[1::2]+raw_ids[::2])]:
                database = self.root / (name+'-'+order+'.db')
                with closing(sqlite3.connect(seed)) as source, closing(sqlite3.connect(database)) as target:
                    source.backup(target)
                for raw_id in ids:
                    ingest_server.normalize_raw_frame(database, raw_id, retire_source=True)
                with closing(sqlite3.connect(database)) as db:
                    db.row_factory = sqlite3.Row
                    self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
                    self.assertEqual(db.execute('''SELECT COUNT(*) FROM monitoring_business_observations business
                        LEFT JOIN observation_values value ON value.id=business.source_observation_value_id
                        LEFT JOIN observation_batches batch ON batch.id=value.observation_batch_id
                        LEFT JOIN ingest_raw_frames raw ON raw.id=batch.raw_frame_id
                        WHERE value.id IS NULL OR batch.id IS NULL OR raw.id IS NULL''').fetchone()[0], 0)
                    hourly = [tuple(row) for row in db.execute("""SELECT sample_hour,observed_at,received_at,
                        standard_value,quality,source_raw_frame_id FROM monitoring_hourly_last_values
                        WHERE business_metric='ph' ORDER BY sample_hour""")]
                    formal = [tuple(row) for row in db.execute("""SELECT business.scheduled_at,business.observed_at,
                        business.received_at,business.standard_value,business.quality,business.timeliness,
                        business.slot_state,batch.raw_frame_id FROM monitoring_business_observations business
                        JOIN observation_batches batch ON batch.id=business.observation_batch_id
                        WHERE business.is_current=1 AND business.business_metric='ph'
                        ORDER BY business.observed_at,business.received_at""")]
                    latest = [{key:row.get(key) for key in ('standard_value','observed_at','received_at','quality')}
                              for row in latest_values(db, site_id, at_time='2026-11-23T08:00:00+00:00') if row['business_metric']=='ph']
                    trend = [{key:row.get(key) for key in ('value','observed_at','quality','status')}
                             for row in result_trend_page(db, site_id, 'ph', '2026-11-13T00:00:00+00:00', '2026-11-23T03:00:00+00:00')[0]]
                    slots = trend_slots(db, site_id, 'ph', '2026-11-23T00:00:00+00:00', '2026-11-23T08:00:00+00:00', at_time='2026-11-23T08:00:00+00:00')
                    diagnostics = business_slot_diagnostics(db, site_id, 'ph', '2026-11-23T00:00:00+00:00', '2026-11-23T08:00:00+00:00')
                    snapshots[order] = {'hourly':hourly, 'formal':formal, 'latest':latest, 'trend':trend,
                                        'slots':slots, 'diagnostics':diagnostics}
            results[name] = {'persisted_raw_count':len(raw_ids), 'equal':all(snapshot==snapshots['ascending'] for snapshot in snapshots.values()),
                             'snapshots':snapshots}
        for name, result in results.items():
            self.assertTrue(result['equal'], name)
        for name in ('formal_same_slot_same_value', 'formal_three_receipts',
                     'formal_receipt_tie', 'formal_receipt_not_id_order'):
            formal = results[name]['snapshots']['ascending']
            self.assertEqual(len(formal['formal']), 1)
            self.assertEqual(formal['formal'][0][2:7],
                             ('2026-11-23T00:32:00+00:00', 7, 'valid', 'on_time', 'selected'))
            self.assertEqual(formal['diagnostics']['late_points'], 0)
        self.assertEqual([row[6] for row in results['formal_different_value']['snapshots']['ascending']['formal']],
                         ['conflict', 'conflict'])
        print('NORMALIZATION_ORDER_COMPARISON '+json.dumps(results, ensure_ascii=False, default=str))

    async def test_formal_source_order_preserves_protocol_priority_and_factor_independence(self):
        from station_monitoring import (trend_slots, current_factor_configurations,
            _slot_rows, _cycle_factor, _cycle_factor_from_rows)
        await self.server.close()
        site_id = self._recovery_endpoint()
        with closing(sqlite3.connect(self.database)) as db:
            endpoint = db.execute("SELECT id FROM trusted_endpoints WHERE station_code='TESTHJ01'").fetchone()[0]
            for code in ('HJ212:w01009', 'HJ212:005'):
                db.execute('''INSERT INTO monitoring_factor_mappings(endpoint_id,protocol_code,
                    expected_interval_seconds,tolerance_seconds,effective_from)
                    VALUES (?,?,60,0,'2020-01-01T00:00:00+00:00')''', (endpoint, code))
            db.execute('''INSERT INTO monitoring_business_schedules(endpoint_id,protocol_code,
                timezone,interval_seconds,anchor_local_time,tolerance_seconds,result_delay_seconds,effective_from)
                VALUES (?,NULL,'Asia/Shanghai',14400,'00:00:00',600,1800,'2020-01-01T00:00:00+00:00')''', (endpoint,))
            db.commit()
        raw_ids = []
        for index, (ph, do_code, received) in enumerate((
                (7, '005', '2026-11-23T00:31:00+00:00'),
                (7, 'w01009', '2026-11-23T00:32:00+00:00'),
                (8, 'w01009', '2026-11-23T06:32:00+00:00'))):
            frame = parse_hj212_frame(make_hj212(command='2061', qn=f'20261123080000{index:03}',
                cp=f'DataTime=20261123080000;w01001-Avg={ph};w01001-Flag=N;{do_code}-Avg=5;{do_code}-Flag=N'))
            raw_id, folded = self.storage.persist_parsed(frame, self.storage.authenticate(frame), received)
            self.assertFalse(folded)
            raw_ids.append(raw_id)
        snapshots = []
        for index, order in enumerate((raw_ids, raw_ids[::-1], raw_ids[1:]+raw_ids[:1])):
            database = self.root / f'factor-independent-{index}.db'
            with closing(sqlite3.connect(self.database)) as source, closing(sqlite3.connect(database)) as target:
                source.backup(target)
            for raw_id in order:
                ingest_server.normalize_raw_frame(database, raw_id, retire_source=True)
            with closing(sqlite3.connect(database)) as db:
                db.row_factory = sqlite3.Row
                rows = db.execute('''SELECT business.business_metric,business.protocol_code,
                    business.standard_value,business.timeliness,business.slot_state,batch.raw_frame_id
                    FROM monitoring_business_observations business JOIN observation_batches batch ON batch.id=business.observation_batch_id
                    WHERE business.is_current=1 ORDER BY business.business_metric,business.protocol_code,business.standard_value''').fetchall()
                rows = [tuple(row) for row in rows]
                ph = [row for row in rows if row[0]=='ph']
                self.assertEqual([(row[2],row[4],row[5]) for row in ph], [(7, 'conflict',raw_ids[0]), (8,'conflict',raw_ids[2])])
                selected = [row for row in rows if row[4]=='selected']
                self.assertEqual([(row[1],row[5]) for row in selected], [('HJ212:w01009', raw_ids[1])])
                slots = trend_slots(db, site_id, selected[0][0], '2026-11-23T00:00:00+00:00',
                                    '2026-11-23T04:00:00+00:00', at_time='2026-11-23T01:00:00+00:00')
                self.assertEqual(slots[0]['received_at'], '2026-11-23T00:32:00+00:00')
                configuration = next(item for item in current_factor_configurations(
                    db, site_id, at_time='2026-11-23T01:00:00+00:00') if item['business_metric']==selected[0][0])
                source_rows = _slot_rows(db, site_id, configuration, '2026-11-23T00:00:00+00:00')
                cycle = _cycle_factor(db, site_id, configuration, '2026-11-23T00:00:00+00:00',
                                      datetime(2026,11,23,1,tzinfo=timezone.utc))
                batched_cycle = _cycle_factor_from_rows(source_rows[::-1], configuration,
                    '2026-11-23T00:00:00+00:00', datetime(2026,11,23,1,tzinfo=timezone.utc))
                self.assertEqual(cycle, batched_cycle)
                self.assertEqual((cycle['protocol_code'], cycle['received_at']),
                                 ('HJ212:w01009', '2026-11-23T00:32:00+00:00'))
                snapshots.append((rows, slots, cycle))
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(snapshots[0], snapshots[1])
        self.assertEqual(snapshots[0], snapshots[2])

    def _save_recovery_sample(self, stamp, value, received):
        frame = parse_hj212_frame(self._recovery_frame(stamp, value))
        raw_id, folded = self.storage.persist_parsed(frame, self.storage.authenticate(frame), received)
        self.assertFalse(folded)
        return raw_id

    async def test_restart_recent_result_is_queryable_before_backlog_drains(self):
        from station_monitoring import latest_values, result_trend_page
        await self.server.close()
        site_id = self._recovery_endpoint()
        old = [self._save_recovery_sample(f'2026111308{minute:02}00', 6+minute/100,
               f'2026-11-13T00:{minute:02}:01+00:00') for minute in range(32)]
        current = self._save_recovery_sample('20261123083700', 7.8, '2026-11-23T00:37:01+00:00')
        # Highest ID/most recent receipt is actually a historical retransmission.
        self._save_recovery_sample('20261113090000', 6.8, '2026-11-23T00:38:00+00:00')
        original = ingest_server.normalize_raw_frame
        evidence = []

        def normalize(*args, **kwargs):
            time.sleep(0.01)
            result = original(*args, **kwargs)
            if args[1] == current:
                with closing(sqlite3.connect(self.database)) as db:
                    db.row_factory = sqlite3.Row
                    remaining = db.execute("SELECT COUNT(*) FROM ingest_raw_frames WHERE id<=? AND persistence_state='pending_parse'", (max(old),)).fetchone()[0]
                    latest = latest_values(db, site_id, at_time='2026-11-23T01:00:00+00:00')
                    trend = result_trend_page(db, site_id, 'ph', '2026-11-23T00:00:00+00:00', '2026-11-23T01:00:00+00:00')[0]
                    evidence.append((remaining, latest[0]['standard_value'], trend[-1]['value']))
            return result

        with mock.patch.object(ingest_server, 'normalize_raw_frame', side_effect=normalize):
            await self.server.start()
            for _ in range(150):
                if evidence:
                    break
                await asyncio.sleep(0.02)
            await self.server.close()
        self.assertTrue(evidence)
        self.assertGreater(evidence[0][0], 0)
        self.assertEqual(evidence[0][1:], (7.8, 7.8))
        print('RECENT_BEFORE_BACKLOG '+json.dumps({'old_total':len(old), 'old_remaining_when_current_queryable':evidence[0][0], 'latest':evidence[0][1], 'trend':evidence[0][2]}))

    async def test_live_tcp_result_overtakes_bounded_old_prefetch(self):
        from station_monitoring import latest_values, result_trend_page
        await self.server.close()
        site_id = self._recovery_endpoint()
        old = [self._save_recovery_sample(f'2026111308{minute:02}00', 6+minute/100,
               f'2026-11-13T00:{minute:02}:01+00:00') for minute in range(32)]
        entered = threading.Event()
        release = threading.Event()
        original = ingest_server.normalize_raw_frame
        evidence = []
        processed = []

        def normalize(*args, **kwargs):
            if not processed:
                entered.set()
                release.wait(5)
            result = original(*args, **kwargs)
            processed.append(args[1])
            if args[1] not in old:
                with closing(sqlite3.connect(self.database)) as db:
                    db.row_factory = sqlite3.Row
                    pending = db.execute("SELECT COUNT(*) FROM ingest_raw_frames WHERE id<=? AND persistence_state='pending_parse'", (max(old),)).fetchone()[0]
                    latest = latest_values(db, site_id, at_time='2026-11-23T01:00:00+00:00')
                    trend = result_trend_page(db, site_id, 'ph', '2026-11-23T00:00:00+00:00', '2026-11-23T01:00:00+00:00')[0]
                    evidence.append((pending, latest[0]['standard_value'], trend[-1]['value']))
            return result

        with mock.patch.object(ingest_server, 'normalize_raw_frame', side_effect=normalize):
            await self.server.start()
            try:
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(entered.is_set())
                reader, writer = await self.connect()
                writer.write(self._recovery_frame('20261123083700', 7.9))
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                for _ in range(100):
                    with closing(sqlite3.connect(self.database)) as db:
                        saved = db.execute('SELECT COUNT(*) FROM ingest_raw_frames WHERE id>?', (max(old),)).fetchone()[0]
                    if saved:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(saved, 1)
                release.set()
                for _ in range(150):
                    if evidence:
                        break
                    await asyncio.sleep(0.02)
            finally:
                release.set()
                await self.server.close()
        self.assertTrue(evidence)
        self.assertGreater(evidence[0][0], 0)
        self.assertEqual(evidence[0][1:], (7.9, 7.9))
        self.assertEqual(len(processed), len(set(processed)))
        print('LIVE_TCP_BEFORE_BACKLOG '+json.dumps({'old_remaining_when_current_queryable':evidence[0][0], 'latest':evidence[0][1], 'trend':evidence[0][2]}))

    async def test_recent_and_backlog_pages_share_due_retry_filter(self):
        await self.server.close()
        self._recovery_endpoint()
        ids = [self._save_recovery_sample(f'2026112308{minute:02}00', 7+minute/100,
               f'2026-11-23T00:{59-minute:02}:01+00:00') for minute in range(6)]
        with closing(sqlite3.connect(self.database)) as db:
            for raw_id, state, due in ((ids[0], 'retrying', '2099-01-01T00:00:00+00:00'),
                                       (ids[1], 'exhausted', None),
                                       (ids[2], 'retrying', '2020-01-01T00:00:00+00:00')):
                db.execute('''INSERT INTO monitoring_normalization_retries(raw_frame_id,normalization_version,
                    attempt_count,next_attempt_at,last_error,state) VALUES (?,'station-monitoring-normalizer-v1',1,?,'isolated',?)''', (raw_id,due,state))
            db.commit()
        self.assertEqual(self.storage.recent_pending_normalization_page(2), ids[2:4])
        self.assertEqual(self.storage.pending_normalization_page(0, 2), ids[2:4])
        self.assertEqual(self.storage.recent_pending_normalization_page(10), ids[2:])
        self.assertEqual(self.storage.pending_normalization_page(ids[3], 2), ids[4:])

    async def test_recent_search_bounds_database_work_with_sparse_or_no_pending_history(self):
        await self.server.close()
        window = ingest_server.DEFAULT_NORMALIZATION_RECENT_CANDIDATES
        original_connect = self.storage._connect

        def measure(method, *args):
            statements = []
            instructions = 0

            def connect():
                connection = original_connect()

                def progress():
                    nonlocal instructions
                    instructions += 1
                    return 0

                connection.set_trace_callback(statements.append)
                connection.set_progress_handler(progress, 1)
                return connection

            with mock.patch.object(self.storage, '_connect', side_effect=connect):
                ids = method(*args)
            self.assertEqual(len(statements), 1)
            with closing(original_connect()) as db:
                plan = [tuple(row) for row in db.execute('EXPLAIN QUERY PLAN '+statements[0])]
            return {'ids': ids, 'vm_instructions': instructions, 'plan': plan,
                    'exact_runtime_sql': statements[0]}

        evidence = []
        for history in (window*2, window*8):
            with closing(original_connect()) as db:
                previous = db.execute('SELECT COUNT(*) FROM ingest_raw_frames').fetchone()[0]
                db.executemany('''INSERT INTO ingest_raw_frames(received_at,frame_sha256,raw_frame,
                    crc_status,authentication_status,disposition,persistence_state)
                    VALUES (?,? ,X'00','not_checked','not_checked','quarantined','persisted')''',
                    [((datetime(2026,11,13,tzinfo=timezone.utc)+timedelta(seconds=i)).isoformat(),
                      f'isolated-bounded-history-{i}') for i in range(previous+1, history+1)])
                db.commit()
            for case in ('empty', 'oldest_only', 'recent_only', 'all_pending'):
                with closing(original_connect()) as db:
                    db.execute("UPDATE ingest_raw_frames SET persistence_state='persisted'")
                    if case == 'oldest_only':
                        db.execute("UPDATE ingest_raw_frames SET persistence_state='pending_parse' WHERE id=1")
                    elif case == 'recent_only':
                        db.execute("UPDATE ingest_raw_frames SET persistence_state='pending_reparse' WHERE id=?", (history,))
                    elif case == 'all_pending':
                        db.execute("UPDATE ingest_raw_frames SET persistence_state='pending_parse'")
                    db.commit()
                recent = measure(self.storage.recent_pending_normalization_page, 4)
                old = measure(self.storage.pending_normalization_page, 0, 4)
                expected = {'empty': [], 'oldest_only': [], 'recent_only': [history],
                            'all_pending': list(range(history, history-4, -1))}[case]
                self.assertEqual(recent['ids'], expected)
                if case == 'oldest_only':
                    self.assertEqual(old['ids'], [1])
                plan = ' '.join(row[3] for row in recent['plan'])
                self.assertIn('MATERIALIZE recent', plan)
                self.assertIn('COVERING INDEX idx_ingest_raw_received', plan)
                self.assertIn('SEARCH raw USING INTEGER PRIMARY KEY', plan)
                self.assertNotIn('SCAN raw', plan)
                evidence.append({'history_rows': history, 'case': case,
                                 'candidate_limit': window, 'recent': recent, 'old': old})
        for case in ('empty', 'oldest_only', 'recent_only', 'all_pending'):
            counts = [row['recent']['vm_instructions'] for row in evidence if row['case']==case]
            self.assertLessEqual(abs(counts[1]-counts[0]), 16, (case, counts))
        print('BOUNDED_RECENT_SQL '+json.dumps(evidence))

    async def test_recent_window_keeps_outside_backlog_and_due_retry_reachable(self):
        await self.server.close()
        self._recovery_endpoint()
        old = [self._save_recovery_sample(f'2026111308{minute:02}00', 6+minute/100,
               f'2026-11-13T00:{minute:02}:01+00:00') for minute in range(3)]
        with closing(sqlite3.connect(self.database)) as db:
            db.executemany('''INSERT INTO ingest_raw_frames(received_at,frame_sha256,raw_frame,
                crc_status,authentication_status,disposition,persistence_state)
                VALUES (?,? ,X'00','not_checked','not_checked','quarantined','persisted')''',
                [((datetime(2026,11,22,tzinfo=timezone.utc)+timedelta(seconds=i)).isoformat(),
                  f'isolated-window-history-{i}')
                 for i in range(ingest_server.DEFAULT_NORMALIZATION_RECENT_CANDIDATES)])
            for raw_id, due in ((old[0], '2099-01-01T00:00:00+00:00'),
                                (old[1], '2020-01-01T00:00:00+00:00')):
                db.execute('''INSERT INTO monitoring_normalization_retries(raw_frame_id,normalization_version,
                    attempt_count,next_attempt_at,last_error,state)
                    VALUES (?,'station-monitoring-normalizer-v1',1,?,'isolated','retrying')''', (raw_id, due))
            db.execute("UPDATE ingest_raw_frames SET persistence_state='pending_reparse' WHERE id=?", (old[1],))
            db.commit()
        current = self._save_recovery_sample('20261123083700', 7.8, '2026-11-23T00:37:01+00:00')
        self.assertEqual(self.storage.recent_pending_normalization_page(4), [current])
        self.assertEqual(self.storage.pending_normalization_page(0, 2), old[1:])
        original = ingest_server.normalize_raw_frame
        processed = []

        def normalize(*args, **kwargs):
            result = original(*args, **kwargs)
            processed.append(args[1])
            return result

        with mock.patch.object(ingest_server, 'normalize_raw_frame', side_effect=normalize):
            try:
                await self.server.start()
                for _ in range(150):
                    if current in processed and all(raw_id in processed for raw_id in old[1:]):
                        break
                    await asyncio.sleep(0.02)
                self.assertEqual(set(processed), {current, *old[1:]})
                self.assertEqual(processed[0], current)
                with closing(sqlite3.connect(self.database)) as db:
                    self.assertEqual(db.execute('SELECT persistence_state FROM ingest_raw_frames WHERE id=?',
                                               (old[0],)).fetchone()[0], 'pending_parse')
                    db.execute("UPDATE monitoring_normalization_retries SET next_attempt_at='2020-01-01T00:00:00+00:00' WHERE raw_frame_id=?", (old[0],))
                    db.commit()
                # No explicit wakeup/new TCP frame: the old cursor must wrap and
                # discover a retry that became due outside the recent window.
                for _ in range(150):
                    if old[0] in processed:
                        break
                    await asyncio.sleep(0.02)
                self.assertIn(old[0], processed)
            finally:
                await self.server.close()
        self.assertEqual(len(processed), len(set(processed)))
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ingest_raw_frames WHERE persistence_state IN ('pending_parse','pending_reparse')").fetchone()[0], 0)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        print('OUTSIDE_RECENT_WINDOW '+json.dumps({'candidate_limit':ingest_server.DEFAULT_NORMALIZATION_RECENT_CANDIDATES,
              'old_ids':old, 'recent_id':current, 'processed_once':processed, 'due_retry_reached':True}))

    async def test_continuous_recent_pending_does_not_starve_old_backlog(self):
        await self.server.close()
        self._recovery_endpoint()
        old = {self._save_recovery_sample(f'2026111308{minute:02}00', 6+minute/100,
               f'2026-11-13T00:{minute:02}:01+00:00') for minute in range(32)}
        self._save_recovery_sample('20261123080000', 7, '2026-11-23T00:00:01+00:00')
        original = ingest_server.normalize_raw_frame
        processed = []
        old_done = set()
        minimum_pending = []
        adding = True

        def normalize(*args, **kwargs):
            result = original(*args, **kwargs)
            processed.append(args[1])
            if args[1] in old:
                old_done.add(args[1])
            if adding:
                # More fresh work arrives than one normalizer can consume.
                for offset in (0, 1):
                    seconds = len(processed)*2+offset
                    stamp = (datetime(2026,11,23,8)+timedelta(seconds=seconds)).strftime('%Y%m%d%H%M%S')
                    received = (datetime(2026,11,23,tzinfo=timezone.utc)+timedelta(seconds=seconds+1)).isoformat()
                    self._save_recovery_sample(stamp, 7+seconds/1000, received)
            with closing(sqlite3.connect(self.database)) as db:
                minimum_pending.append(db.execute("SELECT COUNT(*) FROM ingest_raw_frames WHERE persistence_state='pending_parse'").fetchone()[0])
            return result

        with mock.patch.object(ingest_server, 'normalize_raw_frame', side_effect=normalize):
            await self.server.start()
            for _ in range(200):
                if len(old_done)>=6 or len(processed)>28:
                    break
                await asyncio.sleep(0.02)
            adding = False
            await self.server.close()
        self.assertGreaterEqual(len(old_done), 6)
        self.assertLessEqual(len(processed), 28)
        self.assertEqual(len(processed), len(set(processed)))
        self.assertGreater(min(minimum_pending), 0)
        self.assertLessEqual(self.server._normalization_queue.qsize(), 2)
        print('FAIR_BACKLOG_PROGRESS '+json.dumps({'processed':len(processed),'old_completed':len(old_done),'minimum_pending':min(minimum_pending)}))

    async def test_ack_does_not_wait_for_normalization_and_prefetch_stays_bounded(self):
        await self.server.close()
        self._recovery_endpoint()
        self.add_endpoint('unbound')
        for minute in range(12):
            self._save_recovery_sample(f'2026112308{minute:02}00', 7+minute/100,
                                       f'2026-11-23T00:{minute:02}:01+00:00')
        entered = threading.Event()
        release = threading.Event()
        original = ingest_server.normalize_raw_frame

        def normalize(*args, **kwargs):
            entered.set()
            release.wait(5)
            return original(*args, **kwargs)

        with mock.patch.object(ingest_server, 'normalize_raw_frame', side_effect=normalize):
            await self.server.start()
            try:
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(entered.is_set())
                await asyncio.wait_for(self.send_one(make_uplink(serial=992)), timeout=2)
                self.assertFalse(release.is_set())
                self.assertLessEqual(self.server._normalization_queue.qsize(), 2)
                self.assertLessEqual(len(self.server._scheduled_normalizations), 3)
                with closing(sqlite3.connect(self.database)) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM ingest_raw_frames WHERE persistence_state='pending_parse'").fetchone()[0], 12)
            finally:
                release.set()
                await self.server.close()

    async def test_scan_failure_recovers_backlog_and_current_tcp_result(self):
        from station_monitoring import latest_values, result_trend_page
        site_id = self._recovery_endpoint()
        for minute in (49, 50, 51):
            frame = parse_hj212_frame(self._recovery_frame(f'2026111314{minute}00', 7+minute/1000))
            self.storage.persist_parsed(frame, self.storage.authenticate(frame), '2026-11-13T07:00:00+00:00')
        original = self.storage.pending_normalization_page
        calls = []

        def fail_once(cursor, limit):
            calls.append(time.monotonic())
            if len(calls) == 1:
                raise sqlite3.OperationalError('isolated transient scan failure')
            return original(cursor, min(limit, 2))

        try:
            with mock.patch.object(self.storage, 'pending_normalization_page', side_effect=fail_once):
                self.server._normalization_wakeup.set()
                reader, writer = await self.connect()
                writer.write(self._recovery_frame('20261123083700', '7.01312'))
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                for _ in range(100):
                    with closing(sqlite3.connect(self.database)) as db:
                        hours = db.execute('SELECT observed_at,standard_value FROM monitoring_hourly_last_values ORDER BY observed_at').fetchall()
                    if hours == [('2026-11-13T06:51:00+00:00', 7.051), ('2026-11-23T00:37:00+00:00', 7.01312)]:
                        break
                    await asyncio.sleep(0.05)
                self.assertEqual(hours, [('2026-11-13T06:51:00+00:00', 7.051), ('2026-11-23T00:37:00+00:00', 7.01312)])
                self.assertFalse(self.server._normalization_scanner.done())
                self.assertGreaterEqual(calls[1]-calls[0], 0.09)
                with closing(sqlite3.connect(self.database)) as db:
                    db.row_factory = sqlite3.Row
                    latest = latest_values(db, site_id, at_time='2026-11-23T01:00:00+00:00')
                    trend = result_trend_page(db, site_id, 'ph', '2026-11-23T00:00:00+00:00', '2026-11-23T01:00:00+00:00')[0]
                    self.assertEqual(latest[0]['standard_value'], 7.01312)
                    self.assertEqual(trend[-1]['value'], 7.01312)
                    self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        finally:
            # Permit pre-fix reproduction to tear down even when the scanner crashed.
            if self.server._normalization_scanner.done():
                self.server._normalization_scanner.exception()
                self.server._normalization_scanner = None

    async def test_continuous_receipts_do_not_starve_normalization(self):
        self._recovery_endpoint()
        self.add_endpoint('unbound')
        frame = parse_hj212_frame(self._recovery_frame('20261123083700', '7.01312'))
        original_persist = self.server._process_raw_result
        original_normalize = ingest_server.normalize_raw_frame
        remaining_at_result = []
        target_id = None

        def slow_persist(*args):
            time.sleep(0.04)
            return original_persist(*args)

        def normalize(*args, **kwargs):
            result = original_normalize(*args, **kwargs)
            if args[1] == target_id:
                remaining_at_result.append(self.server.queued_frames)
            return result

        with mock.patch.object(self.server, '_process_raw_result', side_effect=slow_persist), mock.patch.object(ingest_server, 'normalize_raw_frame', side_effect=normalize):
            senders = [asyncio.create_task(self.send_one(make_uplink(serial=800+i))) for i in range(20)]
            for _ in range(200):
                if self.server.queued_frames >= 10:
                    break
                await asyncio.sleep(0.005)
            self.assertGreaterEqual(self.server.queued_frames, 10)
            target_id, _ = self.storage.persist_parsed(frame, self.storage.authenticate(frame), '2026-11-23T00:37:21+00:00')
            self.server._normalization_wakeup.set()
            await asyncio.gather(*senders)
            for _ in range(60):
                if remaining_at_result:
                    break
                await asyncio.sleep(0.05)
            await asyncio.wait_for(self.server._normalization_queue.join(), timeout=3)
        self.assertTrue(any(count > 0 for count in remaining_at_result), remaining_at_result)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT standard_value FROM monitoring_hourly_last_values').fetchone()[0], 7.01312)

    async def test_close_during_scan_failure_preserves_pending_and_restart_recovers(self):
        self._recovery_endpoint()
        raw = self._recovery_frame('20261123083700', '7.01312')
        with mock.patch.object(self.storage, 'pending_normalization_page', side_effect=sqlite3.OperationalError('isolated offline scanner')) as scan, mock.patch.object(self.storage, 'recent_pending_normalization_page', side_effect=sqlite3.OperationalError('isolated offline scanner')) as recent_scan:
            self.server._normalization_wakeup.set()
            reader, writer = await self.connect()
            writer.write(raw)
            await writer.drain()
            for _ in range(60):
                with closing(sqlite3.connect(self.database)) as db:
                    pending = db.execute("SELECT raw_frame,persistence_state FROM ingest_raw_frames").fetchall()
                if pending and (scan.call_count or recent_scan.call_count):
                    break
                await asyncio.sleep(0.02)
            self.assertEqual(pending, [(raw, 'pending_parse')])
            previous_handler = signal.getsignal(signal.SIGTERM)
            stop_task = asyncio.create_task(ingest_server._serve_until_stopped(self.server))
            await asyncio.sleep(0)
            signal.raise_signal(signal.SIGTERM)
            await asyncio.wait_for(stop_task, timeout=3)
            self.assertEqual(signal.getsignal(signal.SIGTERM), previous_handler)
            self.assertEqual(await asyncio.wait_for(reader.read(), timeout=1), b'')
            writer.close()
            await writer.wait_closed()
            self.assertLessEqual(scan.call_count + recent_scan.call_count, 2)
        self.assertEqual(self.server.queued_frames, 0)
        self.assertFalse(self.server._connections)
        self.assertIsNone(self.server._normalizer)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT raw_frame,persistence_state FROM ingest_raw_frames').fetchall(), [(raw, 'pending_parse')])
        await self.server.start()
        for _ in range(60):
            with closing(sqlite3.connect(self.database)) as db:
                hourly = db.execute('SELECT standard_value FROM monitoring_hourly_last_values').fetchall()
            if hourly:
                break
            await asyncio.sleep(0.05)
        self.assertEqual(hourly, [(7.01312,)])

    async def test_close_with_failed_inflight_persistence_never_acknowledges(self):
        self.add_endpoint('unbound')
        entered = threading.Event()
        release = threading.Event()

        def fail_persist(*args, **kwargs):
            entered.set()
            release.wait(2)
            raise sqlite3.OperationalError('isolated persistence failure')

        reader, writer = await self.connect()
        with mock.patch.object(self.storage, 'persist_parsed', side_effect=fail_persist):
            writer.write(make_uplink(serial=921))
            await writer.drain()
            try:
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(entered.is_set())
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(reader.readexactly(ACK_BYTES), timeout=0.05)
                closing_task = asyncio.create_task(self.server.close())
                await asyncio.sleep(0.02)
            finally:
                release.set()
            await asyncio.wait_for(closing_task, timeout=3)
            self.assertEqual(await asyncio.wait_for(reader.read(), timeout=1), b'')
        writer.close()
        await writer.wait_closed()
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ingest_raw_frames').fetchone()[0], 0)
        self.assertEqual(self.server.queued_frames, 0)
        await self.server.start()
        await self.send_one(make_uplink(serial=921))

    async def test_normalization_retry_remains_pending_across_close_and_restart(self):
        self._recovery_endpoint()
        raw = self._recovery_frame('20261123083700', '7.01312')
        with mock.patch.object(ingest_server, 'normalize_raw_frame', side_effect=sqlite3.OperationalError('isolated normalization failure')):
            reader, writer = await self.connect()
            writer.write(raw)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            for _ in range(100):
                with closing(sqlite3.connect(self.database)) as db:
                    retry = db.execute('SELECT attempt_count,state FROM monitoring_normalization_retries').fetchall()
                if retry:
                    break
                await asyncio.sleep(0.01)
            await asyncio.wait_for(self.server.close(), timeout=3)
        self.assertEqual(retry, [(1, 'retrying')])
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT raw_frame,persistence_state FROM ingest_raw_frames').fetchall(), [(raw, 'pending_parse')])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM monitoring_hourly_last_values').fetchone()[0], 0)
        await self.server.start()
        for _ in range(100):
            with closing(sqlite3.connect(self.database)) as db:
                hourly = db.execute('SELECT standard_value FROM monitoring_hourly_last_values').fetchall()
            if hourly:
                break
            await asyncio.sleep(0.05)
        self.assertEqual(hourly, [(7.01312,)])

    async def test_small_normalization_queue_continuously_drains_durable_backlog_without_restart(self):
        await self.server.close()
        self.server = StationIngestServer(
            self.storage, host="127.0.0.1", port=0, max_connections=32, max_connections_per_source=32,
            source_connection_burst=64, queue_max_frames=2, queue_max_bytes=1024 * 1024, error_queue_max=8,
        )
        self.add_endpoint("bound")
        with closing(sqlite3.connect(self.database)) as connection:
            site_id = connection.execute("INSERT INTO sites(code,name,type) VALUES ('NORM-Q','queue test','water')").lastrowid
            endpoint_id = connection.execute("SELECT id FROM trusted_endpoints WHERE station_code='0012345678'").fetchone()[0]
            connection.execute(
                """INSERT INTO monitoring_endpoint_profiles(endpoint_id,business_site_id,expected_granularity,timezone,effective_from)
                   VALUES (?,?,'realtime','Asia/Shanghai','2019-01-01T00:00:00+00:00')""", (endpoint_id, site_id),
            )
            connection.execute(
                """INSERT INTO monitoring_factor_mappings(endpoint_id,protocol_code,business_metric,instrument_asset_code,
                   expected_interval_seconds,tolerance_seconds,effective_from)
                   VALUES (?, '0311', 'water_temp', 'Q-INST', 60, 15, '2019-01-01T00:00:00+00:00')""", (endpoint_id,),
            )
            connection.commit()
        original = ingest_server.normalize_raw_frame

        def slow_normalize(database, raw_id, **kwargs):
            time.sleep(0.04)
            return original(database, raw_id, **kwargs)

        payload = b'\xf1\xf1' + encode_station_code('0012345678') + b'\x51\xf0\xf0' + encode_bcd_observation_time(datetime(2020, 6, 12, 2, 0)) + bytes.fromhex('03110304')
        with mock.patch.object(ingest_server, 'normalize_raw_frame', side_effect=slow_normalize):
            await self.server.start()
            for serial in range(1, 8):
                await self.send_one(make_uplink(serial=700 + serial, payload=payload))
            for _ in range(100):
                with closing(sqlite3.connect(self.database)) as connection:
                    pending = connection.execute(
                        "SELECT COUNT(*) FROM ingest_raw_frames WHERE persistence_state IN ('pending_parse','pending_reparse')"
                    ).fetchone()[0]
                    batches = connection.execute("SELECT COUNT(*) FROM observation_batches").fetchone()[0]
                    hourly = connection.execute("SELECT COUNT(*) FROM monitoring_hourly_last_values").fetchone()[0]
                    raw_count = connection.execute("SELECT COUNT(*) FROM ingest_raw_frames").fetchone()[0]
                if pending == 0 and batches == 0 and hourly == 1 and raw_count == 0:
                    break
                await asyncio.sleep(0.05)
        self.assertEqual(pending, 0)
        self.assertEqual((batches, hourly, raw_count), (0, 1, 0))
        self.assertFalse(self.server._normalization_scanner.done())

    async def test_hj212_tcp_auto_normalization_reaches_v3_public_api(self):
        station = "TESTHJ01"
        password = "long-ascii-password"
        with closing(sqlite3.connect(self.database)) as connection:
            site_id = connection.execute(
                "INSERT INTO sites(code,name,type) VALUES ('HJ212-E2E','HJ212 end to end','water')"
            ).lastrowid
            endpoint_id = connection.execute(
                """INSERT INTO trusted_endpoints(
                       station_code,credential_hmac,business_site_id,endpoint_state)
                   VALUES (?,?,?,'bound')""",
                (station, credential_hmac(password.encode("ascii"), self.pepper), site_id),
            ).lastrowid
            connection.execute(
                """INSERT INTO monitoring_endpoint_profiles(
                       endpoint_id,business_site_id,timezone,enabled,expected_granularity,
                       expected_interval_seconds,effective_from)
                   VALUES (?,?,'Asia/Shanghai',1,'realtime',60,'2020-01-01T00:00:00+00:00')""",
                (endpoint_id, site_id),
            )
            connection.execute(
                """INSERT INTO monitoring_factor_mappings(
                       endpoint_id,protocol_code,expected_interval_seconds,tolerance_seconds,
                       effective_from,enabled)
                   VALUES (?,'HJ212:w01001',60,0,'2020-01-01T00:00:00+00:00',1)""",
                (endpoint_id,),
            )
            connection.execute(
                """INSERT INTO monitoring_business_schedules(
                       endpoint_id,protocol_code,timezone,interval_seconds,anchor_local_time,
                       tolerance_seconds,result_delay_seconds,effective_from)
                   VALUES (?,NULL,'Asia/Shanghai',14400,'00:00:00',900,7200,
                           '2020-01-01T00:00:00+00:00')""",
                (endpoint_id,),
            )
            connection.commit()

        frames = (
            make_hj212(
                station=station, password=password, command="2061", qn="20260911140200001",
                cp="DataTime=20260911120000;w01001-Avg=7.1;w01001-Rtd=7.2;w01001-Flag=N",
            ),
            make_hj212(
                station=station, password=password, command="2061", qn="20260911180200001",
                cp="DataTime=20260911160000;w01001-Rtd=8.8;w01001-Flag=N",
            ),
            make_hj212(
                station=station, password=password, command="2011", qn="20260911180300001",
                cp="DataTime=20260911160100;w01001-Rtd=9.1;w01001-Flag=N",
            ),
            make_hj212(
                station=station, password=password, command="2091", qn="20260911180400001",
                cp="DataTime=20260911160200;w01001-Rtd=9.2;w01001-Flag=N",
            ),
        )
        reader, writer = await self.connect()
        writer.write(b"".join(frames))
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(), timeout=4), b"")
        writer.close()
        await writer.wait_closed()

        for _ in range(100):
            with closing(sqlite3.connect(self.database)) as connection:
                raw_count = connection.execute("SELECT COUNT(*) FROM ingest_raw_frames").fetchone()[0]
                pending_count = connection.execute(
                    """SELECT COUNT(*) FROM ingest_raw_frames
                       WHERE persistence_state IN ('pending_parse','pending_reparse')"""
                ).fetchone()[0]
                batch_count = connection.execute("SELECT COUNT(*) FROM observation_batches").fetchone()[0]
            if raw_count == 2 and pending_count == 0 and batch_count == 2:
                break
            await asyncio.sleep(0.05)
        self.assertEqual((raw_count, pending_count, batch_count), (2, 0, 2))

        with closing(sqlite3.connect(self.database)) as connection:
            batches = connection.execute(
                "SELECT function_code FROM observation_batches ORDER BY raw_frame_id"
            ).fetchall()
            business_rows = connection.execute(
                """SELECT batch.function_code,business.standard_value,business.projection_version
                   FROM monitoring_business_observations business
                   JOIN observation_batches batch ON batch.id=business.observation_batch_id
                   ORDER BY batch.raw_frame_id"""
            ).fetchall()
            non_data_errors = connection.execute(
                "SELECT COUNT(*) FROM ingest_errors WHERE error_type='hj212_non_data_command'"
            ).fetchone()[0]
            non_data_summary = connection.execute(
                """SELECT SUM(occurrence_count) FROM station_ingest_error_hourly
                    WHERE error_type='hj212_non_data_command'"""
            ).fetchone()[0]
        self.assertEqual(batches, [(2061,), (2011,)])
        self.assertEqual(business_rows, [
            (2061, 7.1, "station-monitoring-business-v3"),
            (2011, 9.1, "station-monitoring-business-v3"),
        ])
        self.assertEqual(non_data_errors, 0)
        self.assertIsNone(non_data_summary)
        self.assertFalse(self.server._normalizer.done())

        _, token = self._create_l1_web_credentials()
        headers = {"Authorization": f"Bearer {token}"}
        client = web_app.app.test_client()
        with mock.patch.object(web_app, "STATION_MONITORING_ACCESS_MODE", "public"):
            latest = client.get(f"/api/station-monitoring/sites/{site_id}/latest", headers=headers)
            self.assertEqual(latest.status_code, 200, latest.get_json())
            self.assertEqual(len(latest.get_json()["items"]), 1)
            latest_item = latest.get_json()["items"][0]
            self.assertEqual(
                (latest_item["business_metric"], latest_item["standard_value"],
                 latest_item["function_code"], latest_item["projection_version"]),
                ("ph", 9.1, 2011, "station-monitoring-normalizer-v4"),
            )
            trend = client.get(
                f"/api/station-monitoring/sites/{site_id}/trend"
                "?metric=ph&start=2026-09-11T04:00:00%2B00:00&end=2026-09-11T09:00:00%2B00:00",
                headers=headers,
            )
            self.assertEqual(trend.status_code, 200, trend.get_json())
            self.assertEqual(
                [point["value"] for point in trend.get_json()["points"] if point["status"] == "value"],
                [7.1, 9.1],
            )
            self.assertEqual(
                trend.get_json()["points"][-1]["observed_at"],
                "2026-09-11T08:01:00+00:00",
            )

    def _create_l1_web_credentials(self) -> tuple[int, str]:
        with closing(sqlite3.connect(self.database)) as connection:
            admin_id = connection.execute(
                "INSERT INTO users(username, password_hash, role, real_name) VALUES (?, ?, 'admin', ?)",
                ("l1-admin", web_app._hash_pw("l1-admin-password"), "L1 Admin"),
            ).lastrowid
            operator_id = connection.execute(
                "INSERT INTO users(username, password_hash, role, real_name) VALUES (?, ?, 'operator', ?)",
                ("l1-operator", web_app._hash_pw("l1-operator-password"), "L1 Operator"),
            ).lastrowid
            connection.execute("INSERT INTO user_roles(user_id, role) VALUES (?, 'admin')", (admin_id,))
            connection.execute("INSERT INTO user_roles(user_id, role) VALUES (?, 'operator')", (operator_id,))
            connection.commit()
        client = web_app.app.test_client()
        login = client.post("/api/auth/login", json={"username": "l1-admin", "password": "l1-admin-password"})
        self.assertEqual(login.status_code, 200, login.get_data(as_text=True))
        token = login.get_json()["token"]
        return int(operator_id), str(token)

    async def test_l1_tcp_equivalent_day_and_tenfold_burst_with_actual_web_writes(self):
        """Exercise TCP ingestion beside the real authenticated Flask site-write route."""
        # Let every isolated sample finish so the existing p95/p99 <= 4s assertion,
        # rather than an earlier client cancellation, is the latency authority.
        self.server.read_timeout_seconds = 8
        self.add_endpoint("bound")
        operator_id, token = self._create_l1_web_credentials()
        stop_web_writer = threading.Event()
        web_statuses: list[int] = []
        web_failures: list[Exception] = []

        def web_writer():
            try:
                # Flask's test client invokes before_request, token validation, role checks,
                # get_db(), the actual route transaction and JSON response without opening a port.
                client = web_app.app.test_client()
                for index in range(160):
                    if stop_web_writer.is_set():
                        break
                    response = client.post(
                        "/api/sites",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "code": f"L1-TCP-{index:04d}",
                            "name": f"L1 TCP site {index}",
                            "manager_id": operator_id,
                            "gps_lat": 28.6,
                            "gps_lng": 115.9,
                        },
                    )
                    web_statuses.append(response.status_code)
                    time.sleep(0.01)
            except Exception as exc:  # surfaced; SQLite failures are never swallowed
                web_failures.append(exc)

        writer_thread = threading.Thread(target=web_writer, daemon=True)
        writer_thread.start()
        started = datetime(2020, 6, 12, 0, 0, 0)
        try:
            # 2,880 samples are one 30-second reporting day. Twenty concurrent TCP
            # clients each wait for their own ACK before sending their next sample.
            day_frames = [
                make_uplink(serial=index, sent_at=started + timedelta(seconds=index * 30))
                for index in range(2880)
            ]
            batches = [day_frames[offset::20] for offset in range(20)]

            async def send_stream(items):
                values = []
                for raw in items:
                    values.append(await self.send_one(raw))
                return values

            durations = [duration for group in await asyncio.gather(*(send_stream(group) for group in batches)) for duration in group]
            # Immediate recovery burst: 100 independent TCP clients begin together.
            burst = [
                make_uplink(serial=50000 + index, sent_at=started + timedelta(days=1, seconds=index))
                for index in range(100)
            ]
            durations.extend(await asyncio.gather(*(self.send_one(raw) for raw in burst)))
        finally:
            stop_web_writer.set()
            writer_thread.join(timeout=5)
        self.assertFalse(writer_thread.is_alive())
        self.assertEqual(web_failures, [])
        self.assertGreater(len(web_statuses), 0)
        self.assertEqual(web_statuses, [201] * len(web_statuses), f"actual Web route returned {web_statuses}")
        ordered = sorted(durations)
        p95 = ordered[int(len(ordered) * 0.95) - 1]
        p99 = ordered[int(len(ordered) * 0.99) - 1]
        # Temporary isolated-test budget; actual RTU timeout remains NOT RUN.
        self.assertLessEqual(p95, 4.0)
        self.assertLessEqual(p99, 4.0)
        self.assertGreaterEqual(self.server.queue_peak_frames, 1)
        self.assertLessEqual(self.server.queue_peak_frames, 512)
        await asyncio.wait_for(self.server._queue.join(), timeout=4)
        self.assertEqual(self.server.queued_frames, 0)
        self.assertEqual(self.server.queued_bytes, 0)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ingest_raw_frames").fetchone()[0], 2980)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ingest_raw_frames WHERE duplicate_of_raw_frame_id IS NOT NULL").fetchone()[0], 0)
            web_write_count = connection.execute("SELECT COUNT(*) FROM sites WHERE code LIKE 'L1-TCP-%'").fetchone()[0]
        self.assertEqual(web_write_count, len(web_statuses))
        print(
            f"L1 TCP evidence samples=2980 queue_peak={self.server.queue_peak_frames} "
            f"p95={p95:.3f}s p99={p99:.3f}s actual_web_201={web_write_count}"
        )

    async def test_sqlite_lock_and_noise_errors_do_not_block_event_loop(self):
        self.add_endpoint("bound")
        lock_ready = threading.Event()
        release_lock = threading.Event()

        def hold_web_lock():
            connection = sqlite3.connect(self.database, timeout=1)
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("CREATE TABLE IF NOT EXISTS lock_probe (id INTEGER PRIMARY KEY)")
                lock_ready.set()
                release_lock.wait(timeout=2)
                connection.commit()
            finally:
                connection.close()

        holder = threading.Thread(target=hold_web_lock, daemon=True)
        holder.start()
        self.assertTrue(lock_ready.wait(timeout=2))
        noisy_reader, noisy_writer = await self.connect()
        noisy_writer.write(b"invalid-noise")
        await noisy_writer.drain()
        normal_task = asyncio.create_task(self.send_one(make_uplink(serial=333)))
        await asyncio.sleep(0.05)
        self.assertFalse(normal_task.done(), "SQLite lock may delay the worker but must not block the event loop")
        release_lock.set()
        self.assertLess(await normal_task, 2.5)
        noisy_writer.close()
        await noisy_writer.wait_closed()
        del noisy_reader
        holder.join(timeout=3)
        self.assertFalse(holder.is_alive())
        await asyncio.sleep(0.05)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertGreaterEqual(connection.execute("SELECT COUNT(*) FROM ingest_errors WHERE error_type='noise'").fetchone()[0], 1)

    async def test_single_source_slow_connection_and_storm_are_bounded(self):
        self.server.max_connections_per_source = 2
        self.server.source_connection_burst = 3
        first_reader, first_writer = await self.connect()
        second_reader, second_writer = await self.connect()
        rejected_reader, rejected_writer = await self.connect()
        self.assertEqual(await asyncio.wait_for(rejected_reader.read(), timeout=2), b"")
        self.assertLessEqual(self.server._active_connections, 2)
        self.assertLessEqual(self.server._sources["127.0.0.1"].active_connections, 2)
        rejected_writer.close()
        await rejected_writer.wait_closed()
        first_writer.close()
        second_writer.close()
        await first_writer.wait_closed()
        await second_writer.wait_closed()
        del first_reader, second_reader
        await asyncio.sleep(0.05)
        # A bounded error queue prevents rejected-connection storms from consuming raw-frame capacity.
        storm = await asyncio.gather(*(self.connect() for _ in range(40)), return_exceptions=True)
        for result in storm:
            if isinstance(result, tuple):
                reader, writer = result
                writer.close()
                await writer.wait_closed()
                del reader
        self.assertLessEqual(self.server._active_connections, self.server.max_connections)
        self.assertLessEqual(self.server._queued_errors, self.server.error_queue_max)

    async def test_lightweight_database_and_listener_health_probes(self):
        self.storage.healthcheck()
        await _listener_healthcheck("127.0.0.1", self.server.bound_port)
        self.assertNotIn("integrity_check", inspect.getsource(IngestionStorage.healthcheck))

    async def test_governance_capacity_cli_starts_healthchecks_and_persists_results_on_10gib(self):
        await self.server.close()
        raw_root = self.root / "raw-archive"
        raw_root.mkdir()
        gib = 1024 ** 3
        cli_arguments = ingest_server._parser().parse_args([
            "--database", str(self.database),
            "--raw-storage-dir", str(raw_root),
            "--storage-mode", "governance",
            "--archive-minimum-total-bytes", str(8 * gib),
            "--archive-minimum-free-bytes", str(1 * gib),
            "--hot-minimum-total-bytes", str(10 * gib),
            "--hot-minimum-free-bytes", str(2 * gib),
        ])
        policy = ingest_server._storage_policy_from_arguments(cli_arguments)
        self.assertEqual(policy.mode, "governance")
        with self.assertRaisesRegex(StorageError, "requires explicit archive and hot"):
            ingest_server._storage_policy_from_arguments(
                ingest_server._parser().parse_args([
                    "--database", str(self.database),
                    "--raw-storage-dir", str(raw_root),
                    "--storage-mode", "governance",
                ])
            )
        with self.assertRaisesRegex(StorageError, "require --storage-mode governance"):
            ingest_server._storage_policy_from_arguments(
                ingest_server._parser().parse_args([
                    "--database", str(self.database),
                    "--raw-storage-dir", str(raw_root),
                    "--storage-mode", "long_term",
                    "--archive-minimum-total-bytes", str(8 * gib),
                ])
            )

        environment = {
            "SL651_RAW_STORAGE_DIR": str(raw_root),
            "SL651_STORAGE_MODE": "governance",
            "SL651_ARCHIVE_MINIMUM_TOTAL_BYTES": str(8 * gib),
            "SL651_ARCHIVE_MINIMUM_FREE_BYTES": str(1 * gib),
            "SL651_HOT_MINIMUM_TOTAL_BYTES": str(10 * gib),
            "SL651_HOT_MINIMUM_FREE_BYTES": str(2 * gib),
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            health_arguments = ingest_server._parser().parse_args([
                "--database", str(self.database), "--healthcheck",
            ])
        self.assertEqual(ingest_server._storage_policy_from_arguments(health_arguments), policy)

        station = "GOVERNANCE01"
        password = "long-ascii-password"
        with closing(sqlite3.connect(self.database)) as connection:
            site_id = connection.execute(
                "INSERT INTO sites(code,name,type) VALUES ('GOV-01','Governance','water')"
            ).lastrowid
            endpoint_id = connection.execute(
                """INSERT INTO trusted_endpoints(
                       station_code,credential_hmac,business_site_id,endpoint_state)
                   VALUES (?,?,?,'bound')""",
                (station, credential_hmac(password.encode("ascii"), self.pepper), site_id),
            ).lastrowid
            connection.execute(
                """INSERT INTO monitoring_endpoint_profiles(
                       endpoint_id,business_site_id,timezone,enabled,expected_granularity,
                       expected_interval_seconds,effective_from)
                   VALUES (?,?,'Asia/Shanghai',1,'realtime',14400,'2020-01-01T00:00:00+00:00')""",
                (endpoint_id, site_id),
            )
            connection.execute(
                """INSERT INTO monitoring_factor_mappings(
                       endpoint_id,protocol_code,expected_interval_seconds,tolerance_seconds,
                       effective_from,enabled)
                   VALUES (?,'HJ212:w01001',14400,900,'2020-01-01T00:00:00+00:00',1)""",
                (endpoint_id,),
            )
            connection.execute(
                """INSERT INTO monitoring_business_schedules(
                       endpoint_id,protocol_code,timezone,interval_seconds,anchor_local_time,
                       tolerance_seconds,result_delay_seconds,effective_from)
                   VALUES (?,NULL,'Asia/Shanghai',14400,'00:00:00',900,7200,
                           '2020-01-01T00:00:00+00:00')""",
                (endpoint_id,),
            )
            connection.commit()

        class Usage:
            def __init__(self, total: int, free: int):
                self.total = total
                self.free = free
                self.used = total - free

        def available_usage(path):
            resolved = Path(path).resolve()
            if resolved == raw_root.resolve():
                return Usage(10 * gib, 3 * gib)
            if resolved == self.database.parent.resolve():
                return Usage(10 * gib, 4 * gib)
            raise AssertionError(f"unexpected capacity path: {resolved}")

        self.storage = IngestionStorage(self.database, self.pepper, storage_policy=policy)
        self.server = StationIngestServer(
            self.storage, host="127.0.0.1", port=0, max_connections=8,
        )
        with mock.patch.object(retention.shutil, "disk_usage", side_effect=available_usage):
            self.storage.healthcheck()
            await self.server.start()
            reader, writer = await self.connect()
            writer.write(make_hj212(
                station=station, password=password, qn="20261028120000001",
                cp="DataTime=20261028120000;w01001-Rtd=7.25;w01001-Flag=N",
            ))
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            del reader
            for _ in range(100):
                with closing(sqlite3.connect(self.database)) as connection:
                    row = connection.execute(
                        """SELECT business.standard_value
                             FROM monitoring_business_observations business
                             JOIN observation_batches batch
                               ON batch.id=business.observation_batch_id
                            WHERE batch.endpoint_id=?""",
                        (endpoint_id,),
                    ).fetchone()
                if row is not None:
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(row, (7.25,))
            self.storage.healthcheck()
            await self.server.close()

        def low_archive_usage(path):
            if Path(path).resolve() == raw_root.resolve():
                return Usage(10 * gib, gib - 1)
            return Usage(10 * gib, 4 * gib)

        with mock.patch.object(retention.shutil, "disk_usage", side_effect=low_archive_usage):
            with self.assertRaisesRegex(StorageError, "raw archive storage is unavailable"):
                self.storage.healthcheck()

        def low_hot_usage(path):
            if Path(path).resolve() == raw_root.resolve():
                return Usage(10 * gib, 3 * gib)
            return Usage(10 * gib, 2 * gib - 1)

        with mock.patch.object(retention.shutil, "disk_usage", side_effect=low_hot_usage):
            with self.assertRaisesRegex(StorageError, "raw archive storage is unavailable"):
                self.storage.healthcheck()

        long_term = ingest_server._storage_policy_from_arguments(
            ingest_server._parser().parse_args([
                "--database", str(self.database), "--raw-storage-dir", str(raw_root),
                "--storage-mode", "long_term",
            ])
        )
        validation = ingest_server._storage_policy_from_arguments(
            ingest_server._parser().parse_args([
                "--database", str(self.database), "--raw-storage-dir", str(raw_root),
                "--storage-mode", "validation",
            ])
        )
        self.assertEqual((long_term.minimum_total_bytes, long_term.minimum_free_bytes), (100 * gib, 10 * gib))
        self.assertEqual((validation.minimum_total_bytes, validation.minimum_free_bytes), (20 * gib, 5 * gib))

    async def test_full_integrity_check_allows_unrelated_legacy_foreign_key_violation(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE legacy_parents (id INTEGER PRIMARY KEY)")
            connection.execute(
                "CREATE TABLE legacy_children (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES legacy_parents(id))"
            )
            connection.execute("INSERT INTO legacy_children(parent_id) VALUES (17)")
            connection.commit()
            before = frozenset(tuple(row) for row in connection.execute("PRAGMA foreign_key_check"))
        self.assertEqual(len(before), 1)

        self.storage.full_integrity_check()

        with closing(sqlite3.connect(self.database)) as connection:
            after = frozenset(tuple(row) for row in connection.execute("PRAGMA foreign_key_check"))
            self.assertEqual(after, before)
            self.assertEqual(connection.execute("SELECT parent_id FROM legacy_children").fetchone()[0], 17)

    async def test_full_integrity_check_rejects_station_owned_foreign_key_violation(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO monitoring_normalization_retries(raw_frame_id, normalization_version) VALUES (999, 'test')"
            )
            connection.commit()
        with self.assertRaisesRegex(StorageError, "station ingestion foreign key"):
            self.storage.full_integrity_check()

    async def test_first_stage_only_database_refuses_healthcheck_and_start_before_listener_binding(self):
        await self.server.close()
        first_stage_database = self.root / "first-stage-only.db"
        with closing(sqlite3.connect(first_stage_database)) as connection:
            connection.execute("CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            migration._execute_migration_sql(connection, migration.MIGRATIONS[0][1].read_text(encoding="utf-8"))
            connection.commit()
            connection.execute("PRAGMA journal_mode=WAL")
        first_stage_storage = IngestionStorage(first_stage_database, self.pepper)
        first_stage_server = StationIngestServer(first_stage_storage, host="127.0.0.1", port=0)
        with self.assertRaisesRegex(StorageError, "missing required tables: ingest_frame_protocols"):
            first_stage_storage.healthcheck()
        with self.assertRaisesRegex(StorageError, "missing required tables: ingest_frame_protocols"):
            await first_stage_server.start()
        self.assertIsNone(first_stage_server._server)
        self.assertIsNone(first_stage_server._worker)
        self.assertIsNone(first_stage_server._normalizer)
        self.assertIsNone(first_stage_server._normalization_scanner)
        self.server = first_stage_server

    def test_station_ingest_container_has_shared_group_and_executable_permission_probe(self):
        project = Path(__file__).resolve().parents[1]
        dockerfile = (project / "Dockerfile").read_text(encoding="utf-8")
        dockerignore = (project / ".dockerignore").read_text(encoding="utf-8").splitlines()
        compose = yaml.safe_load((project / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
        ignore_patterns = tuple(line for line in dockerignore if line and not line.startswith("#"))

        def excluded_from_context(path: str) -> bool:
            return any(
                path == pattern.rstrip("/") or path.startswith(pattern)
                if pattern.endswith("/")
                else Path(path).match(pattern)
                for pattern in ignore_patterns
            )

        for runtime_output in (
            "backend/data/water.db",
            "backend/data/water.db-wal",
            "backend/data/water.db-shm",
            "backend/backups/water.sqlite",
            "deploy/backups/water.backup",
            "deploy/water-monitor-archive.tar.gz",
        ):
            self.assertTrue(excluded_from_context(runtime_output), runtime_output)
        for application_source in (
            "backend/migrations/001_station_ingestion.py",
            "backend/sl651_parser.py",
            "backend/sl651_server.py",
            "backend/app.py",
            "react-vite/src/pages/archive/ArchivePage.jsx",
        ):
            self.assertFalse(excluded_from_context(application_source), application_source)
        self.assertNotIn("archive/", ignore_patterns)
        self.assertNotIn("**/archive/", ignore_patterns)
        self.assertIn("groupadd --gid 10001 stationingest", dockerfile)
        self.assertIn("useradd --uid 10001 --gid 10001", dockerfile)
        self.assertIn("FROM python:3.12-slim AS sqlite-group-mode-builder", dockerfile)
        self.assertIn("shared_database_mode", dockerfile)
        self.assertIn("return (mode & ~0007) | 0060", dockerfile)
        self.assertIn('"/app/backend/data/"', dockerfile)
        self.assertIn("int open64", dockerfile)
        self.assertIn("libsqlite-group-mode.so", dockerfile)

        preparer = compose["station-ingest-permissions"]
        self.assertEqual(preparer["user"], "0:0")
        self.assertEqual(preparer["restart"], "no")
        self.assertIn("/app/backend/data /var/lib/station-ingest/raw", preparer["command"][2])
        self.assertIn('chown -R 0:10001 "$$directory"', preparer["command"][2])
        self.assertIn('chmod -R u+rwX,g+rwX,o-rwx "$$directory"', preparer["command"][2])
        self.assertIn("chmod g+s", preparer["command"][2])

        # Raw YAML parsing cannot detect Compose's host-side interpolation. Render
        # with non-production placeholders and assert the command seen by a
        # container still contains a live shell variable and no empty path.
        environment = os.environ.copy()
        environment.update({
            "SL651_RAW_STORAGE_HOST_PATH": "/tmp/codex-station-raw-placeholder",
            "SL651_BIND_PORT": "15505",
            "SL651_STORAGE_MODE": "governance",
            "SL651_ARCHIVE_MINIMUM_TOTAL_BYTES": "8589934592",
            "SL651_ARCHIVE_MINIMUM_FREE_BYTES": "1073741824",
            "SL651_HOT_MINIMUM_TOTAL_BYTES": "10737418240",
            "SL651_HOT_MINIMUM_FREE_BYTES": "2147483648",
            "SL651_CREDENTIAL_PEPPER": "test-only-placeholder",
            "MINIPROGRAM_STATE": "formal",
            "WX_APPSECRET": "test-only-placeholder",
            "COMPOSE_PROJECT_NAME": "station-ingest-contract-test",
        })
        rendered = subprocess.run(
            ["docker", "compose", "--profile", "station-ingest", "config", "--format", "json"],
            cwd=project, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        self.assertNotIn("directory is not set", rendered.stderr.lower())
        rendered_services = json.loads(rendered.stdout)["services"]
        rendered_receiver_environment = rendered_services["station-ingest"]["environment"]
        self.assertEqual(rendered_receiver_environment["SL651_STORAGE_MODE"], "governance")
        self.assertEqual(
            rendered_receiver_environment["SL651_ARCHIVE_MINIMUM_TOTAL_BYTES"],
            "8589934592",
        )
        self.assertEqual(
            rendered_receiver_environment["SL651_HOT_MINIMUM_FREE_BYTES"],
            "2147483648",
        )
        rendered_command = rendered_services["station-ingest-permissions"]["command"][2]
        self.assertEqual(rendered_command.count('"$$directory"'), 4)
        # Compose's config output preserves the escape; the command delivered to
        # the container is the corresponding single-dollar shell variable.
        self.assertEqual(rendered_command.replace("$$directory", "$directory").count('"$directory"'), 4)
        self.assertNotIn('""', rendered_command)
        self.assertIn("/app/backend/data /var/lib/station-ingest/raw", rendered_command)

        web = compose["water-monitor"]
        self.assertIn("umask 0007", web["command"][2])
        self.assertEqual(web["environment"]["LD_PRELOAD"], "/opt/libsqlite-group-mode.so")
        self.assertEqual(
            web["depends_on"]["station-ingest-cutover"]["condition"],
            "service_completed_successfully",
        )

        cutover = compose["station-ingest-cutover"]
        self.assertIn("station_ingest_cutover.py prepare", cutover["command"][2])
        self.assertEqual(
            cutover["depends_on"]["station-ingest-permissions"]["condition"],
            "service_completed_successfully",
        )

        receiver = compose["station-ingest"]
        self.assertEqual(receiver["user"], "10001:10001")
        self.assertTrue(receiver["read_only"])
        self.assertIn("umask 0007", receiver["command"][2])
        self.assertIn("--raw-storage-dir /var/lib/station-ingest/raw", receiver["command"][2])
        self.assertIn("--storage-mode ${SL651_STORAGE_MODE:-long_term}", receiver["command"][2])
        self.assertEqual(receiver["environment"]["SL651_RAW_STORAGE_DIR"], "/var/lib/station-ingest/raw")
        self.assertEqual(receiver["environment"]["SL651_STORAGE_MODE"], "${SL651_STORAGE_MODE:-long_term}")
        for name in (
            "SL651_ARCHIVE_MINIMUM_TOTAL_BYTES", "SL651_ARCHIVE_MINIMUM_FREE_BYTES",
            "SL651_HOT_MINIMUM_TOTAL_BYTES", "SL651_HOT_MINIMUM_FREE_BYTES",
        ):
            self.assertEqual(receiver["environment"][name], "${" + name + ":-}")
        self.assertIn("${SL651_RAW_STORAGE_HOST_PATH:?set an independent raw storage mount}:/var/lib/station-ingest/raw", receiver["volumes"])
        self.assertIn("umask 0007", receiver["healthcheck"]["test"][1])
        self.assertEqual(
            receiver["depends_on"]["station-ingest-cutover"]["condition"],
            "service_completed_successfully",
        )
        self.assertEqual(receiver["ports"], ["127.0.0.1:${SL651_BIND_PORT:-5505}:5005"])

        maintenance = compose["station-ingest-retention"]
        self.assertEqual(maintenance["user"], "10001:10001")
        self.assertTrue(maintenance["read_only"])
        self.assertIn("maintain-once", maintenance["command"][2])
        self.assertIn("--lock-file", maintenance["command"][2])
        self.assertNotIn("compact-copy", maintenance["command"][2])

        probe = compose["station-ingest-permission-check"]
        self.assertEqual(probe["user"], "0:0")
        self.assertTrue(probe["read_only"])
        self.assertEqual(probe["environment"]["LD_PRELOAD"], "/opt/libsqlite-group-mode.so")
        probe_source = probe["command"][2]
        self.assertIn("PRAGMA journal_mode=WAL", probe_source)
        self.assertIn("setpriv", probe_source)
        self.assertIn("web-first", probe_source)
        self.assertIn("ingest-after-web", probe_source)
        self.assertIn("ingest-first", probe_source)
        self.assertIn("web-after-ingest", probe_source)
        self.assertIn("web-restarted", probe_source)
        self.assertIn("ingest-restarted", probe_source)
        self.assertIn("/app/backend", probe_source)
        self.assertIn("/app/frontend", probe_source)

        parsed = ast.parse(probe_source)
        helper_chmod = [
            node
            for node in ast.walk(parsed)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "chmod"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
        ]
        self.assertEqual([node.args[0].value for node in helper_chmod], [0o444])
        self.assertLess(
            probe_source.index("helper.chmod(0o444)"),
            probe_source.index("setpriv', '--reuid=10001"),
        )


class ProcessingFailureDiagnosticsTest(unittest.TestCase):
    def test_repeated_failure_logs_are_rate_limited_per_fixed_stage(self):
        server = StationIngestServer(mock.Mock())
        failure = RuntimeError('PRIVATE_ERROR_CONTENT')
        with self.assertLogs('station_ingest', level='WARNING') as logs:
            with mock.patch.object(ingest_server.time, 'monotonic', return_value=100):
                for _ in range(100):
                    server._log_failure('error_evidence', failure)
                server._log_failure('raw_persistence', failure)
            with mock.patch.object(ingest_server.time, 'monotonic', return_value=105):
                server._log_failure('error_evidence', failure)
        self.assertEqual(len(logs.output), 3)
        self.assertNotIn('PRIVATE_ERROR_CONTENT', '\n'.join(logs.output))
        self.assertEqual(len(server._failure_log_next), 2)

    def test_sqlite_diagnostics_redact_schema_paths_parameters_and_long_messages(self):
        secret = 'PRIVATE_PASSWORD_AND_RAW_FRAME'
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE private_values (value TEXT UNIQUE)')
            db.execute('INSERT INTO private_values VALUES (?)', (secret,))
            for sql, parameters, expected in [
                ('SELECT * FROM '+secret, (), ('SQLITE_ERROR', 'missing_table')),
                ('SELECT '+secret+' FROM private_values', (), ('SQLITE_ERROR', 'missing_column')),
                ('INSERT INTO private_values VALUES (?)', (secret,), ('SQLITE_CONSTRAINT_UNIQUE', 'constraint_failed')),
            ]:
                with self.subTest(expected=expected):
                    try:
                        db.execute(sql, parameters)
                    except sqlite3.Error as exc:
                        with self.assertLogs('station_ingest', level='WARNING') as logs:
                            ingest_server._log_processing_failure('raw_persistence', exc)
                    else:
                        self.fail('SQLite fault was not raised')
                    output = '\n'.join(logs.output)
                    self.assertIn('sqlite_name='+expected[0], output)
                    self.assertIn('reason='+expected[1], output)
                    self.assertNotIn(secret, output)
                    self.assertNotIn('private_values', output)
                    self.assertNotIn(sql, output)
                    self.assertLess(len(output), 400)
        for exc in (sqlite3.OperationalError(secret*1000), RuntimeError(secret*1000)):
            with self.assertLogs('station_ingest', level='WARNING') as logs:
                ingest_server._log_processing_failure('raw_persistence', exc)
            self.assertIn('sqlite_code=unknown sqlite_name=unknown reason=details_suppressed', logs.output[0])
            self.assertNotIn(secret, logs.output[0])
            self.assertLess(len(logs.output[0]), 400)

    def test_readonly_error_and_wrapped_sqlite_cause_remain_diagnostic(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('PRAGMA query_only=ON')
            try:
                db.execute('CREATE TABLE private_table (private_column TEXT)')
            except sqlite3.Error as exc:
                try:
                    raise StorageError('private path and parameters') from exc
                except StorageError as wrapped:
                    with self.assertLogs('station_ingest', level='WARNING') as logs:
                        ingest_server._log_processing_failure('error_evidence', wrapped)
            else:
                self.fail('Readonly SQLite fault was not raised')
            self.assertIn('sqlite_code=8 sqlite_name=SQLITE_READONLY reason=database_readonly', logs.output[0])
            self.assertNotIn('private', logs.output[0])


if __name__ == "__main__":
    unittest.main()
