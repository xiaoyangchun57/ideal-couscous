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
MIGRATION_PATH = Path(__file__).with_name("migrations") / "20260908_001_station_ingestion.sql"
REQUIRED_BUSINESS_IDENTITY_TABLES = frozenset({"sites"})


class MigrationError(RuntimeError):
    pass


def migration_checksum() -> str:
    return hashlib.sha256(MIGRATION_PATH.read_bytes()).hexdigest()


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


def verify_station_ingestion_schema(connection: sqlite3.Connection) -> None:
    required_tables = {"schema_migrations", "trusted_endpoints", "ingest_raw_frames", "ingest_parse_attempts", "ingest_errors"}
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
    }
    indexes = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    missing_indexes = required_indexes - indexes
    if missing_indexes:
        raise MigrationError(f"missing required indexes: {', '.join(sorted(missing_indexes))}")
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise MigrationError("database integrity check failed")
    foreign_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_rows:
        raise MigrationError("foreign key check failed")


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
    checksum = migration_checksum()

    with closing(sqlite3.connect(str(database))) as connection:
        if _table_exists(connection, "schema_migrations"):
            existing = connection.execute(
                "SELECT checksum FROM schema_migrations WHERE version=?", (MIGRATION_VERSION,)
            ).fetchone()
            if existing:
                if existing[0] != checksum:
                    raise MigrationError("migration checksum conflict")
                verify_station_ingestion_schema(connection)
                return False, None
        before_schema = _schema_snapshot(connection)

    backup_path = backup_database(database, backup_dir)
    script = MIGRATION_PATH.read_text(encoding="utf-8")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(database), isolation_level=None)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("BEGIN IMMEDIATE")
        _execute_migration_sql(connection, script)
        existing = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE version=?", (MIGRATION_VERSION,)
        ).fetchone()
        if existing and existing[0] != checksum:
            raise MigrationError("migration checksum conflict")
        if not existing:
            connection.execute(
                "INSERT INTO schema_migrations(version, checksum, applied_at, app_version) VALUES (?, ?, ?, ?)",
                (MIGRATION_VERSION, checksum, _utc_now(), app_version),
            )
        verify_station_ingestion_schema(connection)
        after_schema = _schema_snapshot(connection)
        changed_existing = {
            name
            for name, sql in before_schema.items()
            if name in after_schema and after_schema[name] != sql
        }
        if changed_existing:
            raise MigrationError("migration modified pre-existing schema objects")
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
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--isolated-test", action="store_true", help="allow only a temporary isolated empty SQLite database")
    arguments = parser.parse_args()
    if arguments.check:
        _require_existing_business_database(arguments.database, isolated_test=arguments.isolated_test)
        with closing(sqlite3.connect(str(arguments.database))) as connection:
            verify_station_ingestion_schema(connection)
        return 0
    applied, backup = apply_migration(
        arguments.database,
        arguments.backup_dir,
        isolated_test=arguments.isolated_test,
    )
    print("already-applied" if not applied else f"applied backup={backup.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
