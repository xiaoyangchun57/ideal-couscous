import hashlib
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import migrate_station_ingestion as migration
import station_ingest_cutover as cutover
from station_ingest_retention import RetentionError


class StationIngestCutoverTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "water.db"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            connection.execute("INSERT INTO sites(id,name) VALUES (1,'cutover station')")
            connection.commit()
        self._apply_through_013()
        with closing(sqlite3.connect(self.database)) as connection:
            endpoint_id = connection.execute(
                """INSERT INTO trusted_endpoints(
                       station_code,credential_hmac,business_site_id,endpoint_state)
                   VALUES ('CUTOVER-MN','credential',1,'bound')"""
            ).lastrowid
            payload = b"historical-authenticated-frame"
            raw_id = connection.execute(
                """INSERT INTO ingest_raw_frames(
                       endpoint_id,station_code,received_at,frame_sha256,logical_key_sha256,
                       raw_frame,body_length,crc_status,authentication_status,disposition,
                       persistence_state)
                   VALUES (?,?,?,?,?,?,?,'valid','authenticated','accepted','persisted')""",
                (
                    endpoint_id, "CUTOVER-MN", "2026-10-16T11:00:00+00:00",
                    hashlib.sha256(payload).hexdigest(), "cutover-key", payload, len(payload),
                ),
            ).lastrowid
            connection.execute(
                """INSERT INTO ingest_frame_protocols(raw_frame_id,protocol_family,parser_version)
                   VALUES (?,'hj212','cutover-test')""", (raw_id,),
            )
            connection.commit()

    def tearDown(self):
        self.temp.cleanup()

    def _apply_through_013(self):
        with closing(sqlite3.connect(self.database)) as connection:
            for version, path in migration.MIGRATIONS:
                if version == migration.STATION_INGEST_RETENTION_WINDOWS_MIGRATION_VERSION:
                    break
                migration._execute_migration_sql(connection, path.read_text(encoding="utf-8"))
                connection.execute(
                    """INSERT INTO schema_migrations(version,checksum,applied_at,app_version)
                       VALUES (?,?,?,?)""",
                    (
                        version, migration.migration_checksum(version),
                        "2026-10-16T00:00:00+00:00", "isolated-013",
                    ),
                )
            connection.commit()

    def test_prepare_migrates_backfills_verifies_and_is_idempotent(self):
        result = cutover.prepare_cutover(
            self.database, self.root / "backups",
            now=datetime(2026, 10, 16, 12, tzinfo=timezone.utc),
        )

        self.assertEqual(result["migration"], "applied")
        self.assertEqual(result["verification"]["raw_frame_count"], 1)
        self.assertEqual(result["verification"]["current_state_count"], 1)
        self.assertEqual(result["verification"]["authenticated_source_count"], 1)
        self.assertEqual(result["verification"]["recent_receipt_count"], 1)
        self.assertEqual(result["verification"]["receipt_hour_count"], 1)
        self.assertEqual(result["verification"]["latest_authenticated_mismatches"], 0)
        self.assertEqual(cutover.require_cutover_ready(self.database)["status"], "completed")
        with mock.patch.object(
            cutover, "bootstrap_retention_state", side_effect=AssertionError("must not rescan")
        ):
            repeated = cutover.prepare_cutover(self.database, self.root / "backups")
        self.assertEqual(repeated["backfill"], "already-completed")

    def test_backfill_failure_leaves_startup_gate_closed_with_visible_reason(self):
        with mock.patch.object(
            cutover, "bootstrap_retention_state", side_effect=RetentionError("injected backfill failure")
        ), self.assertRaisesRegex(RetentionError, "injected backfill failure"):
            cutover.prepare_cutover(self.database, self.root / "backups")

        with closing(sqlite3.connect(self.database)) as connection:
            status, detail = connection.execute(
                "SELECT status,detail FROM station_ingest_cutover_state WHERE cutover_name=?",
                (cutover.CUTOVER_NAME,),
            ).fetchone()
        self.assertEqual(status, "failed")
        self.assertIn("injected backfill failure", detail)
        with self.assertRaisesRegex(cutover.CutoverError, "cutover is failed"):
            cutover.require_cutover_ready(self.database)


if __name__ == "__main__":
    unittest.main()
