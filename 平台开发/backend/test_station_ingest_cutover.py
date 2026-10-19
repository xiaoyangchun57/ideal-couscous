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

    @staticmethod
    def _global_foreign_key_checks(statements):
        return sum(
            statement.strip().upper() == "PRAGMA FOREIGN_KEY_CHECK"
            for statement in statements)

    def test_prepare_migrates_refreshes_bounded_state_and_records_phases(self):
        result, first_statements = self._trace_sql(
            lambda: cutover.prepare_cutover(
                self.database, self.root / "backups",
                now=datetime(2026, 10, 16, 12, tzinfo=timezone.utc),
            )
        )

        self.assertEqual(result["migration"], "applied")
        self.assertEqual(result["verification"]["raw_max_id"], 1)
        self.assertEqual(result["verification"]["current_state_count"], 1)
        self.assertEqual(result["verification"]["authenticated_source_count"], 1)
        self.assertEqual(result["verification"]["recent_receipt_count"], 1)
        self.assertEqual(result["verification"]["receipt_hour_count"], 1)
        self.assertEqual(result["verification"]["latest_authenticated_mismatches"], 0)
        self.assertEqual(result["verification"]["consumer_window_hours"], 8)
        self.assertEqual(set(result["phases"]), {
            "structure", "necessary-state", "recent-summary", "startup-verification",
        })
        for phase in result["phases"].values():
            self.assertIn("started_at", phase)
            self.assertIn("completed_at", phase)
            self.assertGreaterEqual(phase["elapsed_seconds"], 0)
        self.assertEqual(self._pragma_count(first_statements, "integrity_check"), 1)
        self.assertEqual(self._pragma_count(first_statements, "quick_check"), 0)
        self.assertEqual(self._global_foreign_key_checks(first_statements), 0)
        self.assertFalse(any(
            "COUNT(*) FROM INGEST_RAW_FRAMES)" in " ".join(statement.upper().split())
            for statement in first_statements
        ))
        self.assertEqual(cutover.require_cutover_ready(self.database)["status"], "completed")

        with closing(sqlite3.connect(self.database)) as connection:
            payload = b"r25-era-frame"
            raw_id = connection.execute(
                """INSERT INTO ingest_raw_frames(
                       endpoint_id,station_code,received_at,frame_sha256,logical_key_sha256,
                       raw_frame,body_length,crc_status,authentication_status,disposition,
                       persistence_state)
                   SELECT endpoint_id,station_code,'2026-10-16T11:30:00+00:00',?,?,?, ?,
                          crc_status,authentication_status,disposition,persistence_state
                   FROM ingest_raw_frames WHERE id=1""",
                (hashlib.sha256(payload).hexdigest(), "r25-era-key", payload, len(payload)),
            ).lastrowid
            connection.execute(
                """INSERT INTO ingest_frame_protocols(raw_frame_id,protocol_family,parser_version)
                   VALUES (?,'hj212','r25-era')""", (raw_id,),
            )
            connection.commit()
        repeated, repeated_statements = self._trace_sql(
            lambda: cutover.prepare_cutover(
                self.database, self.root / "backups",
                now=datetime(2026, 10, 16, 12, tzinfo=timezone.utc),
            ))
        self.assertEqual(repeated["migration"], "already-applied")
        self.assertEqual(repeated["startup"]["recent_receipts"], 2)
        self.assertEqual(repeated["verification"]["raw_max_id"], raw_id)
        self.assertIsNone(repeated["backup"])
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
                now=datetime(2026, 10, 16, 12, tzinfo=timezone.utc),
                recovery_reference="isolated-release-recovery-set",
            ))
        self.assertEqual(repeated["migration"], "already-applied")
        self.assertEqual(repeated["startup"]["recent_receipts"], 1)
        self.assertEqual(self._pragma_count(repeated_statements, "integrity_check"), 0)
        self.assertEqual(self._pragma_count(repeated_statements, "quick_check"), 0)

    def test_prepare_does_not_copy_database_when_014_is_already_applied(self):
        migration.apply_migration(
            self.database, None, external_recovery_confirmed=True)
        backup_dir = self.root / "cutover-backups"

        result, statements = self._trace_sql(
            lambda: cutover.prepare_cutover(
                self.database, backup_dir,
                now=datetime(2026, 10, 16, 12, tzinfo=timezone.utc),
            )
        )

        self.assertEqual(result["migration"], "already-applied")
        self.assertIsNone(result["backup"])
        self.assertEqual(list(backup_dir.glob("*.db")) if backup_dir.exists() else [], [])
        with closing(sqlite3.connect(self.database)) as connection:
            detail = json.loads(connection.execute(
                "SELECT detail FROM station_ingest_cutover_state WHERE cutover_name=?",
                (cutover.CUTOVER_NAME,),
            ).fetchone()[0])
        self.assertEqual(detail["recovery"], {
            "kind": "not-required", "reference": "migration-already-applied",
        })
        self.assertEqual(self._pragma_count(statements, "integrity_check"), 0)
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
            cutover, "prepare_startup_state", side_effect=RetentionError("injected backfill failure")
        ), self.assertRaisesRegex(RetentionError, "injected backfill failure"):
            cutover.prepare_cutover(self.database, self.root / "backups")

        with closing(sqlite3.connect(self.database)) as connection:
            status, detail = connection.execute(
                "SELECT status,detail FROM station_ingest_cutover_state WHERE cutover_name=?",
                (cutover.CUTOVER_NAME,),
            ).fetchone()
        self.assertEqual(status, "failed")
        self.assertIn("injected backfill failure", detail)
        self.assertIn('"phase": "necessary-state"', detail)
        with self.assertRaisesRegex(cutover.CutoverError, "startup is failed"):
            cutover.require_cutover_ready(self.database)

    def test_old_completed_state_does_not_open_new_startup_gate(self):
        migration.apply_migration(
            self.database, None, external_recovery_confirmed=True)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO station_ingest_cutover_state(
                       cutover_name,migration_version,status,detail,updated_at)
                   VALUES ('station-ingest-necessary-state-v2',?,'completed','{}',?)""",
                (migration.STATION_INGEST_RETENTION_WINDOWS_MIGRATION_VERSION,
                 "2026-10-16T12:00:00+00:00"),
            )
            connection.commit()
        with self.assertRaisesRegex(cutover.CutoverError, "startup has not completed"):
            cutover.require_cutover_ready(self.database)

    def test_running_and_failed_startup_states_keep_gate_closed(self):
        migration.apply_migration(
            self.database, None, external_recovery_confirmed=True)
        with closing(sqlite3.connect(self.database)) as connection:
            for status in ("running", "failed"):
                connection.execute(
                    """INSERT INTO station_ingest_cutover_state(
                           cutover_name,migration_version,status,detail,updated_at)
                       VALUES (?,?,?,'{}',?)
                       ON CONFLICT(cutover_name) DO UPDATE SET status=excluded.status""",
                    (cutover.CUTOVER_NAME,
                     migration.STATION_INGEST_RETENTION_WINDOWS_MIGRATION_VERSION,
                     status, "2026-10-16T12:00:00+00:00"),
                )
                connection.commit()
                with self.assertRaisesRegex(cutover.CutoverError, f"startup is {status}"):
                    cutover.require_cutover_ready(self.database)

    def test_startup_prepare_does_not_run_history_governance(self):
        before_size = self.database.stat().st_size
        result, statements = self._trace_sql(
            lambda: cutover.prepare_cutover(
                self.database, self.root / "backups",
                now=datetime(2026, 10, 16, 12, tzinfo=timezone.utc),
            ))
        normalized = [" ".join(statement.upper().split()) for statement in statements]
        self.assertEqual(result["migration"], "applied")
        self.assertFalse(any("IDX_INGEST_PARSE_RAW_CLEANUP" in statement for statement in normalized))
        self.assertFalse(any("DELETE FROM INGEST_RAW_FRAMES" in statement for statement in normalized))
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_dedup_keys").fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM ingest_raw_frames").fetchone()[0], 1)
        self.assertGreaterEqual(self.database.stat().st_size, before_size)


if __name__ == "__main__":
    unittest.main()
