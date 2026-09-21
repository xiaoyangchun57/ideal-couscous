import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import migrate_station_ingestion as migration


class StationIngestionMigrationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database = self.root / "water.db"
        self.backups = self.root / "backups"
        self.create_business_database(self.database)

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def create_business_database(path: Path) -> None:
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            connection.execute("INSERT INTO sites(name) VALUES ('isolated test site')")
            connection.commit()

    def test_existing_business_database_upgrade_is_additive_and_repeatable(self):
        applied, backup = migration.apply_migration(self.database, self.backups)
        self.assertTrue(applied)
        self.assertTrue(backup.is_file())
        with closing(sqlite3.connect(self.database)) as connection:
            migration.verify_station_ingestion_schema(connection)
            migration.verify_station_monitoring_schema(connection)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ingest_raw_frames").fetchone()[0], 0)
            legacy = connection.execute(
                """SELECT business_metric,standard_unit,is_published
                   FROM monitoring_factor_definitions WHERE protocol_code='HJ212:005'"""
            ).fetchone()
            self.assertEqual(legacy, ('dissolved_oxygen', 'mg/L', 1))
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE version=?",
                    (migration.HJ212_LEGACY_005_MIGRATION_VERSION,),
                ).fetchone()[0],
                1,
            )
        self.assertEqual(self._run_check(self.database), 0)
        second_applied, second_backup = migration.apply_migration(self.database, self.backups)
        self.assertFalse(second_applied)
        self.assertIsNone(second_backup)

    def _run_check(self, database: Path) -> int:
        with mock.patch.object(sys, "argv", [
            "migrate_station_ingestion.py", "--database", str(database), "--backup-dir", str(self.backups), "--check",
        ]):
            return migration.main()

    @staticmethod
    def _apply_through_006(database: Path) -> None:
        with closing(sqlite3.connect(database)) as connection:
            for version, path in migration.MIGRATIONS:
                if version == migration.MONITORING_RESULT_WINDOW_MIGRATION_VERSION:
                    break
                migration._execute_migration_sql(connection, path.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO schema_migrations(version,checksum,applied_at,app_version) VALUES (?,?,?,?)",
                    (version, migration.migration_checksum(version), "2026-09-21T00:00:00+00:00", "isolated-006"),
                )
            connection.commit()

    def test_existing_001_through_006_database_upgrades_to_007_and_repeats_without_writes(self):
        self._apply_through_006(self.database)
        with closing(sqlite3.connect(self.database)) as connection:
            endpoint_id = connection.execute(
                """INSERT INTO trusted_endpoints(
                       station_code,credential_hmac,business_site_id,endpoint_state)
                   VALUES ('INCREMENTAL-007','isolated',1,'bound')"""
            ).lastrowid
            connection.execute(
                """INSERT INTO monitoring_business_schedules(
                       endpoint_id,protocol_code,timezone,interval_seconds,anchor_local_time,
                       tolerance_seconds,effective_from)
                   VALUES (?,'HJ212:w01001','Asia/Shanghai',14400,'00:00:00',0,
                           '2026-09-21T00:00:00+00:00')""",
                (endpoint_id,),
            )
            before_schedule_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='monitoring_business_schedules'"
            ).fetchone()[0]
            connection.commit()

        applied, backup = migration.apply_migration(self.database, self.backups)

        self.assertTrue(applied)
        self.assertTrue(backup.is_file())
        with closing(sqlite3.connect(self.database)) as connection:
            columns = {row[1]: row for row in connection.execute(
                "PRAGMA table_info(monitoring_business_schedules)"
            )}
            self.assertNotEqual(connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='monitoring_business_schedules'"
            ).fetchone()[0], before_schedule_sql)
            self.assertEqual((columns["result_delay_seconds"][2], columns["result_delay_seconds"][3],
                              str(columns["result_delay_seconds"][4])), ("INTEGER", 1, "0"))
            self.assertEqual(connection.execute(
                "SELECT result_delay_seconds FROM monitoring_business_schedules"
            ).fetchone()[0], 7200)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE monitoring_business_schedules SET result_delay_seconds=interval_seconds+1"
                )
            migration.verify_station_monitoring_schema(connection)
        second_applied, second_backup = migration.apply_migration(self.database, self.backups)
        self.assertFalse(second_applied)
        self.assertIsNone(second_backup)

    def test_failed_007_upgrade_restores_the_001_through_006_database(self):
        self._apply_through_006(self.database)
        with closing(sqlite3.connect(self.database)) as connection:
            before_schema = migration._schema_snapshot(connection)
            before_versions = connection.execute(
                "SELECT version,checksum FROM schema_migrations ORDER BY version"
            ).fetchall()

        with mock.patch.object(
            migration, "verify_station_monitoring_schema",
            side_effect=migration.MigrationError("forced 007 verification failure"),
        ):
            with self.assertRaisesRegex(migration.MigrationError, "forced 007 verification failure"):
                migration.apply_migration(self.database, self.backups)

        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(migration._schema_snapshot(connection), before_schema)
            self.assertEqual(connection.execute(
                "SELECT version,checksum FROM schema_migrations ORDER BY version"
            ).fetchall(), before_versions)
            self.assertNotIn("result_delay_seconds", {
                row[1] for row in connection.execute("PRAGMA table_info(monitoring_business_schedules)")
            })
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_007_upgrade_rejects_and_restores_any_undeclared_schema_change(self):
        self._apply_through_006(self.database)
        with closing(sqlite3.connect(self.database)) as connection:
            before_schema = migration._schema_snapshot(connection)
        original_execute = migration._execute_migration_sql

        def execute_with_undeclared_change(connection, script):
            original_execute(connection, script)
            connection.execute("ALTER TABLE sites ADD COLUMN undeclared_007_value TEXT")

        with mock.patch.object(
            migration, "_execute_migration_sql", side_effect=execute_with_undeclared_change,
        ):
            with self.assertRaisesRegex(
                migration.MigrationError, "modified pre-existing schema objects",
            ):
                migration.apply_migration(self.database, self.backups)

        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(migration._schema_snapshot(connection), before_schema)
            self.assertNotIn("undeclared_007_value", {
                row[1] for row in connection.execute("PRAGMA table_info(sites)")
            })
            self.assertNotIn("result_delay_seconds", {
                row[1] for row in connection.execute("PRAGMA table_info(monitoring_business_schedules)")
            })

    def test_check_rejects_database_with_only_first_stage_migration(self):
        with closing(sqlite3.connect(self.database)) as connection:
            migration._execute_migration_sql(connection, migration.MIGRATIONS[0][1].read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO schema_migrations(version,checksum,applied_at,app_version) VALUES (?,?,?,?)",
                (migration.MIGRATION_VERSION, migration.migration_checksum(), "2026-09-09T00:00:00+00:00", "isolated"),
            )
            connection.commit()
        with self.assertRaisesRegex(migration.MigrationError, "missing required tables: ingest_frame_protocols"):
            self._run_check(self.database)

    def test_monitoring_schema_rejects_missing_overlap_trigger(self):
        for trigger in (
            "reject_overlapping_monitoring_factor_mapping_insert",
            "reject_overlapping_monitoring_factor_mapping_update",
            "reject_overlapping_business_schedule_insert",
            "reject_overlapping_business_schedule_update",
        ):
            with self.subTest(trigger=trigger):
                database = self.root / f"{trigger}.db"
                self.create_business_database(database)
                migration.apply_migration(database, self.root / f"{trigger}-backups")
                with closing(sqlite3.connect(database)) as connection:
                    connection.execute(f"DROP TRIGGER {trigger}")
                    connection.commit()
                    with self.assertRaisesRegex(migration.MigrationError, trigger):
                        migration.verify_station_monitoring_schema(connection)

    def test_monitoring_schema_rejects_missing_current_batch_unique_constraint(self):
        migration.apply_migration(self.database, self.backups)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DROP INDEX uq_observation_current_raw")
            connection.commit()
            with self.assertRaisesRegex(migration.MigrationError, "current raw frame"):
                migration.verify_station_monitoring_schema(connection)

    def test_business_schedule_migration_only_seeds_confirmed_factor_periods(self):
        migration.apply_migration(self.database, self.backups)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute('DROP TABLE monitoring_business_observations')
            connection.execute('DROP TABLE monitoring_business_schedules')
            connection.execute(
                'DELETE FROM schema_migrations WHERE version=?',
                (migration.HJ212_LEGACY_005_MIGRATION_VERSION,),
            )
            connection.execute(
                'DELETE FROM schema_migrations WHERE version=?',
                (migration.MONITORING_RESULT_WINDOW_MIGRATION_VERSION,),
            )
            endpoint = connection.execute(
                """INSERT INTO trusted_endpoints(
                       station_code,credential_hmac,business_site_id,endpoint_state)
                   VALUES ('SCHEDULE-TEST','isolated',1,'bound')""")
            connection.execute(
                """INSERT INTO monitoring_endpoint_profiles(
                       endpoint_id,business_site_id,timezone,effective_from)
                   VALUES (?,1,'Asia/Shanghai','2020-01-01T00:00:00+00:00')""",
                (endpoint.lastrowid,),
            )
            connection.executemany(
                """INSERT INTO monitoring_factor_mappings(
                       endpoint_id,protocol_code,expected_interval_seconds,effective_from)
                   VALUES (?,?,?,'2020-01-01T00:00:00+00:00')""",
                ((endpoint.lastrowid, 'HJ212:w01001', 60),
                 (endpoint.lastrowid, 'HJ212:w01010', 14400)),
            )
            connection.commit()
        applied, _ = migration.apply_migration(self.database, self.root / 'confirmed-backups')
        self.assertTrue(applied)
        with closing(sqlite3.connect(self.database)) as connection:
            schedules = connection.execute(
                """SELECT protocol_code,interval_seconds,result_delay_seconds FROM monitoring_business_schedules
                   ORDER BY protocol_code""").fetchall()
        self.assertEqual(schedules, [('HJ212:w01010', 14400, 7200)])

    def test_missing_target_path_is_rejected_without_creating_sqlite_file(self):
        missing = self.root / "not-created" / "water.db"
        with self.assertRaisesRegex(migration.MigrationError, "must already exist"):
            migration.apply_migration(missing, self.backups)
        self.assertFalse(missing.exists())
        self.assertFalse(self.backups.exists())

    def test_non_business_empty_database_is_rejected_except_explicit_isolated_test_mode(self):
        empty = self.root / "empty.db"
        sqlite3.connect(empty).close()
        with self.assertRaisesRegex(migration.MigrationError, "not a recognised business database"):
            migration.apply_migration(empty, self.backups)
        applied, backup = migration.apply_migration(empty, self.backups, isolated_test=True)
        self.assertTrue(applied)
        self.assertTrue(backup.is_file())
        with closing(sqlite3.connect(empty)) as connection:
            migration.verify_station_ingestion_schema(connection)

    def test_old_business_tables_are_not_changed(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE sensor_data (id INTEGER PRIMARY KEY, value REAL)")
            connection.execute("INSERT INTO sensor_data(value) VALUES (7.2)")
            before = connection.execute("SELECT sql FROM sqlite_master WHERE name='sensor_data'").fetchone()[0]
            connection.commit()
        migration.apply_migration(self.database, self.backups)
        with closing(sqlite3.connect(self.database)) as connection:
            after = connection.execute("SELECT sql FROM sqlite_master WHERE name='sensor_data'").fetchone()[0]
            self.assertEqual(before, after)
            self.assertEqual(connection.execute("SELECT value FROM sensor_data").fetchone()[0], 7.2)

    def test_unrelated_legacy_foreign_key_violation_is_preserved_and_does_not_block_migration(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE legacy_parents (id INTEGER PRIMARY KEY)")
            connection.execute(
                "CREATE TABLE legacy_children (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES legacy_parents(id))"
            )
            connection.execute("INSERT INTO legacy_children(parent_id) VALUES (17)")
            connection.commit()
            before = frozenset(tuple(row) for row in connection.execute("PRAGMA foreign_key_check"))
        self.assertEqual(len(before), 1)

        applied, _ = migration.apply_migration(self.database, self.backups)

        self.assertTrue(applied)
        with closing(sqlite3.connect(self.database)) as connection:
            after = frozenset(tuple(row) for row in connection.execute("PRAGMA foreign_key_check"))
            self.assertEqual(after, before)
            self.assertEqual(connection.execute("SELECT parent_id FROM legacy_children").fetchone()[0], 17)
            migration.verify_station_ingestion_schema(connection)
            migration.verify_station_monitoring_schema(connection)
        second_applied, second_backup = migration.apply_migration(self.database, self.backups)
        self.assertFalse(second_applied)
        self.assertIsNone(second_backup)

    def test_station_owned_foreign_key_violation_is_rejected_by_schema_verification(self):
        migration.apply_migration(self.database, self.backups)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO monitoring_normalization_retries(raw_frame_id, normalization_version) VALUES (999, 'test')"
            )
            connection.commit()
            with self.assertRaisesRegex(migration.MigrationError, "station ingestion foreign key"):
                migration.verify_station_ingestion_schema(connection)

    def test_migration_added_foreign_key_violation_restores_original_database(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE legacy_parents (id INTEGER PRIMARY KEY)")
            connection.execute(
                "CREATE TABLE legacy_children (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES legacy_parents(id))"
            )
            connection.commit()
        original_execute = migration._execute_migration_sql
        injected = False

        def execute_with_orphan(connection, script):
            nonlocal injected
            original_execute(connection, script)
            if not injected:
                connection.execute("PRAGMA defer_foreign_keys=ON")
                connection.execute("INSERT INTO legacy_children(parent_id) VALUES (999)")
                injected = True

        with mock.patch.object(migration, "_execute_migration_sql", side_effect=execute_with_orphan):
            with self.assertRaisesRegex(migration.MigrationError, "migration changed foreign key violations"):
                migration.apply_migration(self.database, self.backups)

        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM legacy_children").fetchone()[0], 0)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertFalse(migration._table_exists(connection, "ingest_raw_frames"))

    def test_checksum_conflict_is_rejected(self):
        migration.apply_migration(self.database, self.backups)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE schema_migrations SET checksum='conflict' WHERE version=?", (migration.MIGRATION_VERSION,))
            connection.commit()
        with self.assertRaisesRegex(migration.MigrationError, "checksum conflict"):
            migration.apply_migration(self.database, self.backups)

    def test_backup_can_restore_pre_migration_database(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE legacy (id INTEGER PRIMARY KEY, note TEXT)")
            connection.execute("INSERT INTO legacy(note) VALUES ('before')")
            connection.commit()
        _, backup = migration.apply_migration(self.database, self.backups)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertTrue(migration._table_exists(connection, "ingest_raw_frames"))
        migration.restore_backup(self.database, backup)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertFalse(migration._table_exists(connection, "ingest_raw_frames"))
            self.assertEqual(connection.execute("SELECT note FROM legacy").fetchone()[0], "before")

    def test_mid_migration_failure_automatically_restores_original_database(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE legacy (id INTEGER PRIMARY KEY, note TEXT)")
            connection.execute("INSERT INTO legacy(note) VALUES ('before')")
            before_schema = migration._schema_snapshot(connection)
            connection.commit()
        with mock.patch.object(migration, "verify_station_ingestion_schema", side_effect=migration.MigrationError("forced")):
            with self.assertRaisesRegex(migration.MigrationError, "forced"):
                migration.apply_migration(self.database, self.backups)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(migration._schema_snapshot(connection), before_schema)
            self.assertFalse(migration._table_exists(connection, "ingest_raw_frames"))
            self.assertEqual(connection.execute("SELECT note FROM legacy").fetchone()[0], "before")
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(len(list(self.backups.glob("*.db"))), 1)


if __name__ == "__main__":
    unittest.main()
