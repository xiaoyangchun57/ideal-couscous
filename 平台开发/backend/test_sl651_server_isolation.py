import ast
import asyncio
import inspect
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
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
from hj212_parser import build_hj212_9011_response, parse_hj212_frame
import sl651_server as ingest_server
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


def make_hj212(station="TESTHJ01", password="long-ascii-password", command="2011", qn="20260911164900001"):
    cp = "DataTime=20260911164900;w01001-Rtd=7.0;w01001-Flag=N" if command == "2011" else ""
    body = f"QN={qn};ST=91;CN={command};PW={password};MN={station};CP=&&{cp}&&".encode("ascii")
    return b"##" + f"{len(body):04d}".encode("ascii") + body + f"{crc16_modbus(body):04X}".encode("ascii") + b"\r\n"


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
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ingest_raw_frames WHERE station_code='TESTHJ01'").fetchone()[0], 3)

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
            "record_connection_error",
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

        def slow_normalize(database, raw_id):
            time.sleep(0.04)
            return original(database, raw_id)

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
                if pending == 0 and batches == 7:
                    break
                await asyncio.sleep(0.05)
        self.assertEqual(pending, 0)
        self.assertEqual(batches, 7)
        self.assertFalse(self.server._normalization_scanner.done())

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
            "SL651_STORAGE_MODE": "validation",
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
            web["depends_on"]["station-ingest-permissions"]["condition"],
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
        self.assertIn("${SL651_RAW_STORAGE_HOST_PATH:?set an independent raw storage mount}:/var/lib/station-ingest/raw", receiver["volumes"])
        self.assertIn("umask 0007", receiver["healthcheck"]["test"][1])
        self.assertEqual(
            receiver["depends_on"]["station-ingest-permissions"]["condition"],
            "service_completed_successfully",
        )
        self.assertEqual(receiver["ports"], ["127.0.0.1:${SL651_BIND_PORT:-5505}:5005"])

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


if __name__ == "__main__":
    unittest.main()
