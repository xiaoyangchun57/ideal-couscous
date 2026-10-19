import hashlib
from contextlib import closing
from datetime import datetime, timezone
import json
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

    def _trace_sql(self, action):
        original_connect = sqlite3.connect
        statements = []

        def traced_connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection

        with mock.patch.object(sqlite3, "connect", side_effect=traced_connect):
            result = action()
        return result, statements

    @staticmethod
    def _pragma_count(statements, name):
        prefix = f"PRAGMA {name}".upper()
        return sum(statement.strip().upper().startswith(prefix) for statement in statements)

    def test_prepare_migrates_backfills_verifies_and_is_idempotent(self):
        result, first_statements = self._trace_sql(
            lambda: cutover.prepare_cutover(
                self.database, self.root / "backups",
                now=datetime(2026, 10, 16, 12, tzinfo=timezone.utc),
            )
        )

        self.assertEqual(result["migration"], "applied")
        self.assertEqual(result["verification"]["raw_frame_count"], 1)
        self.assertEqual(result["verification"]["current_state_count"], 1)
        self.assertEqual(result["verification"]["authenticated_source_count"], 1)
        self.assertEqual(result["verification"]["recent_receipt_count"], 1)
        self.assertEqual(result["verification"]["receipt_hour_count"], 1)
        self.assertEqual(result["verification"]["latest_authenticated_mismatches"], 0)
        self.assertEqual(result["verification"]["consumer_window_hours"], 8)
        self.assertEqual(self._pragma_count(first_statements, "integrity_check"), 1)
        self.assertEqual(self._pragma_count(first_statements, "quick_check"), 0)
        self.assertEqual(cutover.require_cutover_ready(self.database)["status"], "completed")
        with mock.patch.object(
            cutover, "bootstrap_retention_state", side_effect=AssertionError("must not rescan")
        ):
            repeated, repeated_statements = self._trace_sql(
                lambda: cutover.prepare_cutover(self.database, self.root / "backups"))
        self.assertEqual(repeated["backfill"], "already-completed")
        self.assertEqual(repeated["backup"], result["backup"])
        self.assertEqual(self._pragma_count(repeated_statements, "integrity_check"), 0)
        self.assertEqual(self._pragma_count(repeated_statements, "quick_check"), 0)

    def test_prepare_accepts_explicit_verified_recovery_reference_without_local_backup(self):
        result, first_statements = self._trace_sql(
            lambda: cutover.prepare_cutover(
                self.database, None,
                now=datetime(2026, 10, 16, 12, tzinfo=timezone.utc),
                recovery_reference="isolated-release-recovery-set",
            )
        )

        self.assertEqual(result["migration"], "applied")
        self.assertIsNone(result["backup"])
        self.assertEqual(
            result["recovery_reference"], "isolated-release-recovery-set")
        self.assertEqual(self._pragma_count(first_statements, "integrity_check"), 0)
        self.assertEqual(self._pragma_count(first_statements, "quick_check"), 0)
        self.assertEqual(cutover.require_cutover_ready(self.database)["status"], "completed")
        repeated, repeated_statements = self._trace_sql(
            lambda: cutover.prepare_cutover(
                self.database, None,
                recovery_reference="isolated-release-recovery-set",
            ))
        self.assertEqual(repeated["backfill"], "already-completed")
        self.assertEqual(self._pragma_count(repeated_statements, "integrity_check"), 0)
        self.assertEqual(self._pragma_count(repeated_statements, "quick_check"), 0)

    def test_prepare_creates_pre_cleanup_backup_when_migration_is_already_applied(self):
        migration.apply_migration(
            self.database, None, external_recovery_confirmed=True)
        backup_dir = self.root / "cutover-backups"

        result, statements = self._trace_sql(
            lambda: cutover.prepare_cutover(
                self.database, backup_dir,
                now=datetime(2026, 10, 16, 12, tzinfo=timezone.utc),
            )
        )

        backup = Path(result["backup"])
        self.assertEqual(result["migration"], "already-applied")
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.parent, backup_dir)
        with closing(sqlite3.connect(self.database)) as connection:
            detail = json.loads(connection.execute(
                "SELECT detail FROM station_ingest_cutover_state WHERE cutover_name=?",
                (cutover.CUTOVER_NAME,),
            ).fetchone()[0])
        self.assertEqual(detail["recovery"], {
            "kind": "local_backup", "reference": str(backup),
        })
        self.assertEqual(self._pragma_count(statements, "integrity_check"), 1)
        self.assertEqual(self._pragma_count(statements, "quick_check"), 0)

    def test_prepare_still_rejects_missing_required_structure_without_deep_check(self):
        migration.apply_migration(
            self.database, None, external_recovery_confirmed=True)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DROP INDEX idx_station_ingest_current_endpoint")
            connection.commit()

        with self.assertRaisesRegex(migration.MigrationError, "missing required indexes"):
            cutover.prepare_cutover(
                self.database, None,
                recovery_reference="isolated-release-recovery-set",
            )

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

    def test_duplicate_reduction_resumes_from_the_last_committed_cursor(self):
        first_batch = {
            "cursor_raw_id": 10, "scanned": 10, "eligible": 8,
            "duplicate_candidates": 6, "deleted": 5, "retained_protected": 1,
            "retained_unclassified": 2, "canonical_kept": 2, "complete": False,
            "cutoff": "2026-10-09T12:00:00+00:00",
        }
        with mock.patch.object(
            cutover, "reduce_confirmed_minute_duplicates",
            side_effect=[first_batch, RetentionError("injected batch failure")],
        ), self.assertRaisesRegex(RetentionError, "injected batch failure"):
            cutover.prepare_cutover(
                self.database, self.root / "backups",
                now=datetime(2026, 10, 16, 12, tzinfo=timezone.utc),
            )
        original_backups = list((self.root / "backups").glob("*.db"))
        self.assertEqual(len(original_backups), 1)

        def finish(_database, *, after_id, batch_limit, now):
            self.assertEqual(after_id, 10)
            return {
                "cursor_raw_id": 12, "scanned": 2, "eligible": 2,
                "duplicate_candidates": 1, "deleted": 1, "retained_protected": 0,
                "retained_unclassified": 0, "canonical_kept": 1, "complete": True,
                "cutoff": "2026-10-09T12:00:00+00:00",
            }

        with mock.patch.object(
            cutover, "reduce_confirmed_minute_duplicates", side_effect=finish,
        ):
            result = cutover.prepare_cutover(
                self.database, self.root / "backups",
                now=datetime(2026, 10, 16, 12, tzinfo=timezone.utc),
            )
        self.assertEqual(result["duplicate_reduction"]["scanned"], 12)
        self.assertEqual(result["duplicate_reduction"]["deleted"], 6)
        self.assertEqual(list((self.root / "backups").glob("*.db")), original_backups)
        self.assertEqual(Path(result["backup"]), original_backups[0])
        self.assertEqual(cutover.require_cutover_ready(self.database)["status"], "completed")


if __name__ == "__main__":
    unittest.main()
