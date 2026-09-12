import sqlite3
from contextlib import closing
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from migrate_station_ingestion import apply_migration
from sl651_parser import UP_FLOW_CONTROL, crc16_modbus, encode_bcd_time, encode_station_code
from sl651_server import IngestionStorage, StationIngestServer, StorageError, credential_hmac
import station_ingest_retention as retention
from station_ingest_retention import StoragePolicy, storage_policy_for_mode


def make_uplink(station="0012345678", password=b"\x12\x34", serial=1, sent_at=None):
    sent_at = sent_at or datetime(2020, 6, 12, 2, 0, 0)
    content = serial.to_bytes(2, "big") + encode_bcd_time(sent_at)
    prefix = (
        b"\x7e\x7e\x10" + encode_station_code(station) + password + b"\x32"
        + len(content).to_bytes(2, "big") + b"\x02" + content + bytes((UP_FLOW_CONTROL,))
    )
    return prefix + crc16_modbus(prefix).to_bytes(2, "big")


class IngestionStorageTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database = self.root / "ingest.db"
        sqlite3.connect(self.database).close()
        apply_migration(self.database, self.root / "backups", isolated_test=True)
        self.pepper = "test-only-pepper"
        self.storage = IngestionStorage(self.database, self.pepper)

    def tearDown(self):
        self.temp_dir.cleanup()

    def add_endpoint(self, station="0012345678", password=b"\x12\x34", state="bound"):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO trusted_endpoints(station_code, credential_hmac, endpoint_state) VALUES (?, ?, ?)",
                (station, credential_hmac(password, self.pepper), state),
            )
            connection.commit()

    def rows(self, table):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()

    def test_unbound_authenticated_endpoint_is_persisted_and_acknowledged(self):
        self.add_endpoint(state="unbound")
        server = StationIngestServer(self.storage)
        ack = server._process_raw(make_uplink(), "2026-09-08T00:00:00+00:00")
        self.assertIsNotNone(ack)
        raw = self.rows("ingest_raw_frames")[0]
        self.assertEqual(raw[10], "quarantined")
        self.assertEqual(raw[9], "unbound_authenticated")
        self.assertEqual(len(self.rows("ingest_errors")), 1)

    def test_unknown_or_bad_credential_never_acknowledges(self):
        server = StationIngestServer(self.storage)
        self.assertIsNone(server._process_raw(make_uplink(), "2026-09-08T00:00:00+00:00"))
        self.add_endpoint()
        self.assertIsNone(server._process_raw(make_uplink(password=b"\xab\xcd"), "2026-09-08T00:00:01+00:00"))
        rows = self.rows("ingest_raw_frames")
        self.assertEqual([row[9] for row in rows], ["unknown_endpoint", "credential_failed"])

    def test_duplicates_keep_raw_receipts_without_duplicate_canonical_record(self):
        self.add_endpoint()
        server = StationIngestServer(self.storage)
        raw = make_uplink(serial=9)
        self.assertIsNotNone(server._process_raw(raw, "2026-09-08T00:00:00+00:00"))
        self.assertIsNotNone(server._process_raw(raw, "2026-09-08T00:00:01+00:00"))
        rows = self.rows("ingest_raw_frames")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][10], "pending_parse")
        self.assertEqual(rows[1][10], "duplicate")
        self.assertEqual(rows[1][11], rows[0][0])

    def test_raw_persistence_failure_never_acknowledges(self):
        self.add_endpoint()
        server = StationIngestServer(self.storage)
        with mock.patch.object(self.storage, "persist_parsed", side_effect=sqlite3.OperationalError("locked")):
            with self.assertRaises(sqlite3.OperationalError):
                server._process_raw(make_uplink(), "2026-09-08T00:00:00+00:00")

    def test_unavailable_archive_storage_blocks_persistence_before_ack(self):
        self.add_endpoint()
        storage = IngestionStorage(
            self.database, self.pepper,
            storage_policy=StoragePolicy(self.root, minimum_total_bytes=10**18, minimum_free_bytes=10 * 1024 ** 3),
        )
        server = StationIngestServer(storage)
        with self.assertRaisesRegex(StorageError, "raw archive storage is unavailable"):
            server._process_raw(make_uplink(), "2026-09-08T00:00:00+00:00")
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ingest_raw_frames").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT status FROM monitoring_storage_health WHERE storage_key='raw_archive'").fetchone()[0], "degraded")

    def test_unparseable_frame_is_also_blocked_when_archive_storage_is_unavailable(self):
        storage = IngestionStorage(
            self.database, self.pepper,
            storage_policy=StoragePolicy(self.root, minimum_total_bytes=10**18, minimum_free_bytes=10 * 1024 ** 3),
        )
        server = StationIngestServer(storage)
        broken = bytearray(make_uplink())
        broken[-1] ^= 1
        with self.assertRaisesRegex(StorageError, "raw archive storage is unavailable"):
            server._process_raw(bytes(broken), "2026-09-08T00:00:00+00:00")
        self.assertEqual(self.rows("ingest_raw_frames"), [])

    def test_framing_error_evidence_uses_the_same_storage_capacity_guard(self):
        storage = IngestionStorage(
            self.database, self.pepper,
            storage_policy=StoragePolicy(self.root, minimum_total_bytes=10**18, minimum_free_bytes=10 * 1024 ** 3),
        )
        with self.assertRaisesRegex(StorageError, "raw archive storage is unavailable"):
            storage.record_connection_error("hj212_invalid_length")
        self.assertEqual(self.rows("ingest_errors"), [])

    def test_validation_mode_rejects_low_space_on_sqlite_hot_storage_even_when_archive_is_healthy(self):
        archive_root = self.root / "archive"
        archive_root.mkdir()
        policy = storage_policy_for_mode(archive_root, "validation")

        class Usage:
            total = 29 * 1024 ** 3
            free = 6 * 1024 ** 3

        class LowHotUsage:
            total = 29 * 1024 ** 3
            free = 4 * 1024 ** 3

        with mock.patch.object(retention.shutil, "disk_usage", side_effect=lambda path: Usage() if Path(path) == archive_root else LowHotUsage()):
            with self.assertRaisesRegex(Exception, "SQLite hot storage is below"):
                retention.check_storage_policy(self.database, policy)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT status FROM monitoring_storage_health WHERE storage_key='raw_archive'").fetchone()[0], "degraded")

    def test_wal_lock_wait_and_restart_recovery_keep_raw_evidence(self):
        self.add_endpoint()
        ready = threading.Event()
        release = threading.Event()

        def hold_web_write_lock():
            connection = sqlite3.connect(self.database, timeout=1)
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("CREATE TABLE IF NOT EXISTS web_write_probe (id INTEGER PRIMARY KEY)")
                ready.set()
                release.wait(timeout=2)
                connection.commit()
            finally:
                connection.close()

        holder = threading.Thread(target=hold_web_write_lock)
        holder.start()
        try:
            self.assertTrue(ready.wait(timeout=2))
            threading.Timer(0.1, release.set).start()
            server = StationIngestServer(self.storage)
            self.assertIsNotNone(server._process_raw(make_uplink(serial=77), "2026-09-08T00:00:00+00:00"))
            restarted_storage = IngestionStorage(self.database, self.pepper)
            self.assertEqual(len(restarted_storage.pending_normalization_page(0, 10)), 1)
        finally:
            release.set()
            holder.join(timeout=2)
        self.assertFalse(holder.is_alive())

    def test_crc_failure_is_quarantined_without_ack(self):
        server = StationIngestServer(self.storage)
        broken = bytearray(make_uplink())
        broken[-1] ^= 1
        self.assertIsNone(server._process_raw(bytes(broken), "2026-09-08T00:00:00+00:00"))
        row = self.rows("ingest_raw_frames")[0]
        self.assertEqual(row[8], "invalid")
        self.assertEqual(row[10], "quarantined")


if __name__ == "__main__":
    unittest.main()
