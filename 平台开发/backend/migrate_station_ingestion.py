"""Versioned additive migration for the station-ingestion evidence base.

This command is explicit about its database and backup locations. By default it refuses
to create a database or migrate an unrecognised SQLite file: ordinary execution targets
an existing business database that already contains the site identity schema. Empty
SQLite databases are permitted only with the explicit isolated-test switch.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

MIGRATION_VERSION = "20260908_001_station_ingestion"
MIGRATIONS = (
    (MIGRATION_VERSION, Path(__file__).with_name("migrations") / "20260908_001_station_ingestion.sql"),
    ("20260909_002_station_monitoring_normalization", Path(__file__).with_name("migrations") / "20260909_002_station_monitoring_normalization.sql"),
    ("20260911_003_hj212_protocol", Path(__file__).with_name("migrations") / "20260911_003_hj212_protocol.sql"),
    ("20260912_004_monitoring_retention", Path(__file__).with_name("migrations") / "20260912_004_monitoring_retention.sql"),
    ("20260912_005_retention_recovery", Path(__file__).with_name("migrations") / "20260912_005_retention_recovery.sql"),
    ("20260918_006_hj212_legacy_005_dissolved_oxygen", Path(__file__).with_name("migrations") / "20260918_006_hj212_legacy_005_dissolved_oxygen.sql"),
)
MONITORING_MIGRATION_VERSION = "20260909_002_station_monitoring_normalization"
HJ212_MIGRATION_VERSION = "20260911_003_hj212_protocol"
RETENTION_MIGRATION_VERSION = "20260912_004_monitoring_retention"
RETENTION_RECOVERY_MIGRATION_VERSION = "20260912_005_retention_recovery"
HJ212_LEGACY_005_MIGRATION_VERSION = "20260918_006_hj212_legacy_005_dissolved_oxygen"
REQUIRED_BUSINESS_IDENTITY_TABLES = frozenset({"sites"})
STATION_INGESTION_TABLES = frozenset({
    "schema_migrations",
    "trusted_endpoints",
    "ingest_raw_frames",
    "ingest_parse_attempts",
    "ingest_errors",
    "ingest_frame_protocols",
    "monitoring_endpoint_profiles",
    "monitoring_factor_definitions",
    "monitoring_factor_mappings",
    "observation_batches",
    "observation_values",
    "monitoring_status_events",
    "monitoring_quality_issues",
    "monitoring_normalization_retries",
    "monitoring_raw_archives",
    "monitoring_raw_archive_frames",
    "monitoring_hourly_values",
    "monitoring_raw_archive_parts",
    "monitoring_raw_archive_part_frames",
    "monitoring_hourly_value_series",
    "monitoring_storage_health",
    "monitoring_business_schedules",
    "monitoring_business_observations",
})


class MigrationError(RuntimeError):
    pass


def migration_checksum(version: str = MIGRATION_VERSION) -> str:
    for candidate, path in MIGRATIONS:
        if candidate == version:
            return hashlib.sha256(path.read_bytes()).hexdigest()
    raise MigrationError(f"unknown migration version: {version}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def backup_database(database: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f"{database.stem}-{MIGRATION_VERSION}-{stamp}.db"
    source = sqlite3.connect(str(database))
    destination = sqlite3.connect(str(target))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    with closing(sqlite3.connect(str(target))) as verified:
        result = verified.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        target.unlink(missing_ok=True)
        raise MigrationError("backup integrity check failed")
    return target


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _foreign_key_violations(
    connection: sqlite3.Connection,
    tables: frozenset[str] | None = None,
) -> frozenset[tuple[object, ...]]:
    if tables is None:
        return frozenset(tuple(row) for row in connection.execute("PRAGMA foreign_key_check"))
    existing = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    violations: set[tuple[object, ...]] = set()
    for table in sorted(tables & existing):
        quoted_table = '"' + table.replace('"', '""') + '"'
        violations.update(
            tuple(row)
            for row in connection.execute(f"PRAGMA foreign_key_check({quoted_table})")
        )
    return frozenset(violations)


def station_ingestion_foreign_key_violations(
    connection: sqlite3.Connection,
) -> frozenset[tuple[object, ...]]:
    return _foreign_key_violations(connection, STATION_INGESTION_TABLES)


def _schema_snapshot(connection: sqlite3.Connection) -> dict[str, str]:
    return {
        row[0]: row[1]
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%'"
        )
    }


def _require_existing_business_database(database: Path, *, isolated_test: bool) -> None:
    if not database.exists() or not database.is_file():
        raise MigrationError("target database must already exist; use --isolated-test only for a temporary test database")
    if isolated_test:
        return
    with closing(sqlite3.connect(str(database))) as connection:
        found = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = REQUIRED_BUSINESS_IDENTITY_TABLES - found
    if missing:
        raise MigrationError(
            "target database is not a recognised business database; missing identity tables: "
            + ", ".join(sorted(missing))
        )


def verify_station_ingestion_contract(connection: sqlite3.Connection) -> None:
    """Verify the lightweight first-stage objects required before ingestion can start."""
    required_tables = {
        "schema_migrations", "trusted_endpoints", "ingest_raw_frames", "ingest_parse_attempts", "ingest_errors",
        "ingest_frame_protocols",
    }
    existing = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    missing = required_tables - existing
    if missing:
        raise MigrationError(f"missing required tables: {', '.join(sorted(missing))}")
    required_indexes = {
        "idx_ingest_raw_received",
        "idx_ingest_raw_endpoint_received",
        "idx_ingest_raw_frame_hash",
        "idx_ingest_raw_disposition_received",
        "idx_ingest_raw_logical_key",
        "idx_ingest_parse_pending",
        "idx_ingest_errors_open",
        "idx_ingest_protocol_family_raw",
    }
    indexes = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    missing_indexes = required_indexes - indexes
    if missing_indexes:
        raise MigrationError(f"missing required indexes: {', '.join(sorted(missing_indexes))}")


def verify_station_ingestion_schema(connection: sqlite3.Connection) -> None:
    verify_station_ingestion_contract(connection)
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise MigrationError("database integrity check failed")
    foreign_rows = station_ingestion_foreign_key_violations(connection)
    if foreign_rows:
        raise MigrationError("station ingestion foreign key check failed")


def _require_unique_columns(connection: sqlite3.Connection, table: str, columns: tuple[str, ...]) -> None:
    for index in connection.execute(f"PRAGMA index_list({table})"):
        if not index[2]:
            continue
        index_columns = tuple(row[2] for row in connection.execute(f"PRAGMA index_info({index[1]})"))
        if index_columns == columns:
            return
    raise MigrationError(f"missing required unique constraint: {table}({', '.join(columns)})")


def _verify_monitoring_contract(connection: sqlite3.Connection) -> None:
    required = {
        "monitoring_endpoint_profiles", "monitoring_factor_definitions", "monitoring_factor_mappings",
        "observation_batches", "observation_values", "monitoring_status_events", "monitoring_quality_issues",
        "monitoring_normalization_retries",
        "monitoring_business_schedules", "monitoring_business_observations",
    }
    existing = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = required - existing
    if missing:
        raise MigrationError(f"missing monitoring tables: {', '.join(sorted(missing))}")
    applied = connection.execute(
        "SELECT checksum FROM schema_migrations WHERE version=?", (MONITORING_MIGRATION_VERSION,)
    ).fetchone()
    if not applied or applied[0] != migration_checksum(MONITORING_MIGRATION_VERSION):
        raise MigrationError("station monitoring migration is missing or incompatible")
    hj212 = connection.execute(
        "SELECT checksum FROM schema_migrations WHERE version=?", (HJ212_MIGRATION_VERSION,)
    ).fetchone()
    if not hj212 or hj212[0] != migration_checksum(HJ212_MIGRATION_VERSION):
        raise MigrationError("HJ212 protocol migration is missing or incompatible")
    retention = connection.execute(
        "SELECT checksum FROM schema_migrations WHERE version=?", (RETENTION_MIGRATION_VERSION,)
    ).fetchone()
    if not retention or retention[0] != migration_checksum(RETENTION_MIGRATION_VERSION):
        raise MigrationError("monitoring retention migration is missing or incompatible")
    recovery = connection.execute(
        "SELECT checksum FROM schema_migrations WHERE version=?", (RETENTION_RECOVERY_MIGRATION_VERSION,)
    ).fetchone()
    if not recovery or recovery[0] != migration_checksum(RETENTION_RECOVERY_MIGRATION_VERSION):
        raise MigrationError("monitoring retention recovery migration is missing or incompatible")
    legacy_005 = connection.execute(
        "SELECT checksum FROM schema_migrations WHERE version=?", (HJ212_LEGACY_005_MIGRATION_VERSION,)
    ).fetchone()
    if not legacy_005 or legacy_005[0] != migration_checksum(HJ212_LEGACY_005_MIGRATION_VERSION):
        raise MigrationError("HJ212 legacy dissolved oxygen migration is missing or incompatible")
    _require_unique_columns(connection, "observation_batches", ("raw_frame_id", "normalization_version"))
    _require_unique_columns(connection, "observation_batches", ("endpoint_id", "idempotency_key", "normalization_version"))
    _require_unique_columns(connection, "monitoring_normalization_retries", ("raw_frame_id", "normalization_version"))
    _require_unique_columns(connection, "monitoring_business_observations", ("source_observation_value_id",))
    current_index = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_observation_current_raw'"
    ).fetchone()
    normalized_sql = "".join(str(current_index[0]).upper().split()) if current_index else ""
    if "CREATEUNIQUEINDEX" not in normalized_sql or "ONOBSERVATION_BATCHES(RAW_FRAME_ID)" not in normalized_sql or "WHEREIS_CURRENT=1" not in normalized_sql:
        raise MigrationError("missing required unique constraint: observation_batches current raw frame")
    required_triggers = {
        "reject_overlapping_monitoring_factor_mapping_insert": "BEFOREINSERTONMONITORING_FACTOR_MAPPINGS",
        "reject_overlapping_monitoring_factor_mapping_update": "BEFOREUPDATEOFENDPOINT_ID,PROTOCOL_CODE,EFFECTIVE_FROM,EFFECTIVE_TO,ENABLEDONMONITORING_FACTOR_MAPPINGS",
        "reject_overlapping_business_schedule_insert": "BEFOREINSERTONMONITORING_BUSINESS_SCHEDULES",
        "reject_overlapping_business_schedule_update": "BEFOREUPDATEOFENDPOINT_ID,PROTOCOL_CODE,EFFECTIVE_FROM,EFFECTIVE_TO,ENABLEDONMONITORING_BUSINESS_SCHEDULES",
    }
    triggers = {
        row[0]: "".join(str(row[1]).upper().split())
        for row in connection.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger'")
    }
    missing_triggers = set(required_triggers) - set(triggers)
    if missing_triggers:
        raise MigrationError(f"missing required monitoring triggers: {', '.join(sorted(missing_triggers))}")
    for trigger, operation in required_triggers.items():
        expected_error = ("RAISE(ABORT,'OVERLAPPINGBUSINESSSCHEDULE')"
                          if "business_schedule" in trigger else "RAISE(ABORT,'OVERLAPPINGFACTORMAPPING')")
        if operation not in triggers[trigger] or expected_error not in triggers[trigger]:
            raise MigrationError(f"invalid required monitoring trigger: {trigger}")


def verify_station_monitoring_contract(connection: sqlite3.Connection) -> None:
    """Verify all startup-critical ingestion and normalization objects without a table scan."""
    verify_station_ingestion_contract(connection)
    _verify_monitoring_contract(connection)


def verify_station_monitoring_schema(connection: sqlite3.Connection) -> None:
    verify_station_ingestion_schema(connection)
    _verify_monitoring_contract(connection)


def _execute_migration_sql(connection: sqlite3.Connection, script: str) -> None:
    """Run the controlled SQL file inside the caller's explicit transaction."""
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            if statement.strip():
                connection.execute(statement)
            statement = ""
    if statement.strip():
        raise MigrationError("migration SQL ended with an incomplete statement")


def apply_migration(
    database: Path,
    backup_dir: Path,
    *,
    app_version: str = "station-ingestion-v1",
    isolated_test: bool = False,
) -> tuple[bool, Path | None]:
    database = Path(database)
    backup_dir = Path(backup_dir)
    _require_existing_business_database(database, isolated_test=isolated_test)
    with closing(sqlite3.connect(str(database))) as connection:
        existing_versions = {}
        if _table_exists(connection, "schema_migrations"):
            existing_versions = dict(connection.execute("SELECT version, checksum FROM schema_migrations").fetchall())
        for version, _ in MIGRATIONS:
            existing = existing_versions.get(version)
            if existing is not None and existing != migration_checksum(version):
                raise MigrationError("migration checksum conflict")
        pending = [(version, path) for version, path in MIGRATIONS if version not in existing_versions]
        if not pending:
            verify_station_monitoring_schema(connection)
            return False, None
        before_schema = _schema_snapshot(connection)

    backup_path = backup_database(database, backup_dir)
    connection: sqlite3.Connection | None = None
    before_foreign_rows: frozenset[tuple[object, ...]] | None = None
    try:
        connection = sqlite3.connect(str(database), isolation_level=None)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("BEGIN IMMEDIATE")
        before_foreign_rows = _foreign_key_violations(connection)
        for version, path in pending:
            _execute_migration_sql(connection, path.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO schema_migrations(version, checksum, applied_at, app_version) VALUES (?, ?, ?, ?)",
                (version, migration_checksum(version), _utc_now(), app_version),
            )
        verify_station_monitoring_schema(connection)
        after_schema = _schema_snapshot(connection)
        changed_existing = {
            name
            for name, sql in before_schema.items()
            if name in after_schema and after_schema[name] != sql
        }
        if changed_existing:
            raise MigrationError("migration modified pre-existing schema objects")
        after_foreign_rows = _foreign_key_violations(connection)
        if after_foreign_rows != before_foreign_rows:
            raise MigrationError("migration changed foreign key violations")
        connection.commit()
    except Exception:
        if connection is not None:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            connection.close()
            connection = None
        # SQLite PRAGMA changes and DDL can survive a failed run on some versions. Restore
        # the verified pre-migration backup so a failed migration has zero target side effect.
        restore_backup(database, backup_path)
        with closing(sqlite3.connect(str(database))) as restored:
            if restored.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise MigrationError("migration failed and automatic restore verification failed")
            if before_foreign_rows is not None and _foreign_key_violations(restored) != before_foreign_rows:
                raise MigrationError("migration failed and automatic restore verification failed")
        raise
    finally:
        if connection is not None:
            connection.close()
    return True, backup_path


def restore_backup(database: Path, backup: Path) -> None:
    """Restore only for an isolated test or an explicitly authorised recovery run."""
    database = Path(database)
    backup = Path(backup)
    if not backup.is_file():
        raise MigrationError("backup file does not exist")
    temp_target = database.with_suffix(database.suffix + ".restore-check")
    source = sqlite3.connect(str(backup))
    target = sqlite3.connect(str(temp_target))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    with closing(sqlite3.connect(str(temp_target))) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            temp_target.unlink(missing_ok=True)
            raise MigrationError("backup is not restorable")
    # A restored main database must never be paired with WAL/SHM sidecars from the newer database.
    for suffix in ("-wal", "-shm"):
        Path(str(database) + suffix).unlink(missing_ok=True)
    temp_target.replace(database)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--backup-dir", required=True, type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true")
    action.add_argument("--reproject-business-history", action="store_true")
    parser.add_argument("--isolated-test", action="store_true", help="allow only a temporary isolated empty SQLite database")
    arguments = parser.parse_args()
    if arguments.check:
        _require_existing_business_database(arguments.database, isolated_test=arguments.isolated_test)
        with closing(sqlite3.connect(str(arguments.database))) as connection:
            verify_station_monitoring_schema(connection)
        return 0
    applied, backup = apply_migration(
        arguments.database,
        arguments.backup_dir,
        isolated_test=arguments.isolated_test,
    )
    if arguments.reproject_business_history:
        try:
            from .station_monitoring import reproject_historical_business_observations
        except ImportError:  # pragma: no cover - direct script execution
            from station_monitoring import reproject_historical_business_observations
        result = reproject_historical_business_observations(arguments.database)
        print(
            f"business-history eligible={result['eligible']} reprojected={result['reprojected']} "
            f"already={result['already_reprojected']} deferred={result['deferred']} "
            f"business_projected={result['business_projected']} "
            f"business_deferred={result['business_deferred']}"
        )
        return 2 if result['deferred'] or result['business_deferred'] else 0
    print("already-applied" if not applied else f"applied backup={backup.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
