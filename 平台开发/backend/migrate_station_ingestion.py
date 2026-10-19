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
    ("20260921_007_monitoring_result_window", Path(__file__).with_name("migrations") / "20260921_007_monitoring_result_window.sql"),
    ("20260922_008_station_master_refresh", Path(__file__).with_name("migrations") / "20260922_008_station_master_refresh.sql"),
    ("20261011_009_station_monitoring_read_indexes", Path(__file__).with_name("migrations") / "20261011_009_station_monitoring_read_indexes.sql"),
    ("20261012_010_pending_normalization_index", Path(__file__).with_name("migrations") / "20261012_010_pending_normalization_index.sql"),
    ("20261013_011_station_expected_factors", Path(__file__).with_name("migrations") / "20261013_011_station_expected_factors.sql"),
    ("20261013_012_hj212_numeric_factor_definitions", Path(__file__).with_name("migrations") / "20261013_012_hj212_numeric_factor_definitions.sql"),
    ("20261013_013_station_service_policy", Path(__file__).with_name("migrations") / "20261013_013_station_service_policy.sql"),
    ("20261016_014_station_ingest_retention_windows", Path(__file__).with_name("migrations") / "20261016_014_station_ingest_retention_windows.sql"),
)
MONITORING_MIGRATION_VERSION = "20260909_002_station_monitoring_normalization"
HJ212_MIGRATION_VERSION = "20260911_003_hj212_protocol"
RETENTION_MIGRATION_VERSION = "20260912_004_monitoring_retention"
RETENTION_RECOVERY_MIGRATION_VERSION = "20260912_005_retention_recovery"
HJ212_LEGACY_005_MIGRATION_VERSION = "20260918_006_hj212_legacy_005_dissolved_oxygen"
MONITORING_RESULT_WINDOW_MIGRATION_VERSION = "20260921_007_monitoring_result_window"
STATION_MASTER_REFRESH_MIGRATION_VERSION = "20260922_008_station_master_refresh"
STATION_MONITORING_READ_INDEX_MIGRATION_VERSION = "20261011_009_station_monitoring_read_indexes"
PENDING_NORMALIZATION_INDEX_MIGRATION_VERSION = "20261012_010_pending_normalization_index"
STATION_EXPECTED_FACTORS_MIGRATION_VERSION = "20261013_011_station_expected_factors"
HJ212_NUMERIC_FACTOR_DEFINITIONS_MIGRATION_VERSION = "20261013_012_hj212_numeric_factor_definitions"
STATION_SERVICE_POLICY_MIGRATION_VERSION = "20261013_013_station_service_policy"
STATION_INGEST_RETENTION_WINDOWS_MIGRATION_VERSION = "20261016_014_station_ingest_retention_windows"
CONTROLLED_EXISTING_SCHEMA_CHANGES = {
    MONITORING_RESULT_WINDOW_MIGRATION_VERSION: {
        "monitoring_business_schedules": """ALTER TABLE monitoring_business_schedules
            ADD COLUMN result_delay_seconds INTEGER NOT NULL DEFAULT 0
            CHECK (result_delay_seconds BETWEEN 0 AND interval_seconds)""",
    },
    STATION_MASTER_REFRESH_MIGRATION_VERSION: {
        "sites": """ALTER TABLE sites
            ADD COLUMN master_status TEXT NOT NULL DEFAULT 'active'
            CHECK (master_status IN ('active', 'retired'))""",
    },
}
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
    "site_name_aliases",
    "station_master_refresh_audits",
    "station_expected_factor_configs",
    "station_expected_factors",
    "station_service_policy_events",
    "station_ingest_current_state",
    "station_ingest_recent_receipts",
    "station_ingest_receipt_hourly",
    "station_ingest_slot_summaries",
    "monitoring_raw_archive_part_metadata",
    "monitoring_archive_evidence_refs",
    "station_ingest_dedup_keys",
    "station_ingest_duplicate_links",
    "station_ingest_error_hourly",
    "station_ingest_detail_archive_parts",
    "station_ingest_retention_runs",
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
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
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


def _expected_controlled_schema_sql(before_sql: str, object_name: str, ddl: str) -> str:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute(before_sql)
        connection.execute(ddl)
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (object_name,)
        ).fetchone()
    if row is None:
        raise MigrationError("controlled migration schema validation failed")
    return str(row[0])


def _verify_existing_schema_changes(
    before_schema: dict[str, str], after_schema: dict[str, str], pending_versions: tuple[str, ...],
) -> None:
    changed_existing = {
        name for name, sql in before_schema.items()
        if name in after_schema and after_schema[name] != sql
    }
    expected_changes: dict[str, str] = {}
    for version in pending_versions:
        for object_name, ddl in CONTROLLED_EXISTING_SCHEMA_CHANGES.get(version, {}).items():
            before_sql = before_schema.get(object_name)
            if before_sql is None:
                continue
            expected_changes[object_name] = _expected_controlled_schema_sql(
                before_sql, object_name, ddl,
            )
    if changed_existing != set(expected_changes):
        raise MigrationError("migration modified pre-existing schema objects")
    if any(
        "".join(after_schema[name].split()) != "".join(expected_sql.split())
        for name, expected_sql in expected_changes.items()
    ):
        raise MigrationError("migration modified pre-existing schema objects")


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
        "idx_ingest_errors_open",
        "idx_ingest_protocol_family_raw",
        "idx_station_ingest_current_endpoint",
        "idx_station_ingest_current_station",
        "idx_station_ingest_recent_received",
        "idx_station_ingest_recent_endpoint_auth",
        "idx_station_ingest_recent_station_auth",
        "idx_station_ingest_receipt_hour",
        "idx_station_ingest_slot_summary_retention",
        "idx_monitoring_evidence_archive_record",
        "idx_monitoring_evidence_received",
        "idx_station_ingest_dedup_expiry",
        "idx_station_ingest_error_hour",
        "idx_station_ingest_detail_range",
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
    result_window = connection.execute(
        "SELECT checksum FROM schema_migrations WHERE version=?", (MONITORING_RESULT_WINDOW_MIGRATION_VERSION,)
    ).fetchone()
    if not result_window or result_window[0] != migration_checksum(MONITORING_RESULT_WINDOW_MIGRATION_VERSION):
        raise MigrationError("monitoring result-window migration is missing or incompatible")
    station_master = connection.execute(
        "SELECT checksum FROM schema_migrations WHERE version=?", (STATION_MASTER_REFRESH_MIGRATION_VERSION,)
    ).fetchone()
    if not station_master or station_master[0] != migration_checksum(STATION_MASTER_REFRESH_MIGRATION_VERSION):
        raise MigrationError("station master refresh migration is missing or incompatible")
    read_indexes = connection.execute(
        "SELECT checksum FROM schema_migrations WHERE version=?",
        (STATION_MONITORING_READ_INDEX_MIGRATION_VERSION,),
    ).fetchone()
    if not read_indexes or read_indexes[0] != migration_checksum(
            STATION_MONITORING_READ_INDEX_MIGRATION_VERSION):
        raise MigrationError("station monitoring read-index migration is missing or incompatible")
    pending_index_migration = connection.execute(
        "SELECT checksum FROM schema_migrations WHERE version=?",
        (PENDING_NORMALIZATION_INDEX_MIGRATION_VERSION,),
    ).fetchone()
    if not pending_index_migration or pending_index_migration[0] != migration_checksum(
            PENDING_NORMALIZATION_INDEX_MIGRATION_VERSION):
        raise MigrationError("pending normalization index migration is missing or incompatible")
    expected_factors_migration = connection.execute(
        "SELECT checksum FROM schema_migrations WHERE version=?",
        (STATION_EXPECTED_FACTORS_MIGRATION_VERSION,),
    ).fetchone()
    if not expected_factors_migration or expected_factors_migration[0] != migration_checksum(
            STATION_EXPECTED_FACTORS_MIGRATION_VERSION):
        raise MigrationError("station expected-factor migration is missing or incompatible")
    numeric_factor_migration = connection.execute(
        "SELECT checksum FROM schema_migrations WHERE version=?",
        (HJ212_NUMERIC_FACTOR_DEFINITIONS_MIGRATION_VERSION,),
    ).fetchone()
    if not numeric_factor_migration or numeric_factor_migration[0] != migration_checksum(
            HJ212_NUMERIC_FACTOR_DEFINITIONS_MIGRATION_VERSION):
        raise MigrationError("HJ212 numeric-factor migration is missing or incompatible")
    service_policy_migration = connection.execute(
        "SELECT checksum FROM schema_migrations WHERE version=?",
        (STATION_SERVICE_POLICY_MIGRATION_VERSION,),
    ).fetchone()
    if not service_policy_migration or service_policy_migration[0] != migration_checksum(
            STATION_SERVICE_POLICY_MIGRATION_VERSION):
        raise MigrationError("station service-policy migration is missing or incompatible")
    retention_windows_migration = connection.execute(
        "SELECT checksum FROM schema_migrations WHERE version=?",
        (STATION_INGEST_RETENTION_WINDOWS_MIGRATION_VERSION,),
    ).fetchone()
    if (not retention_windows_migration
            or retention_windows_migration[0] != migration_checksum(
                STATION_INGEST_RETENTION_WINDOWS_MIGRATION_VERSION)):
        raise MigrationError("station ingest retention-window migration is missing or incompatible")
    required_read_indexes = {
        "idx_ingest_raw_logical_key",
        "idx_ingest_raw_authenticated_endpoint_received",
        "idx_station_ingest_current_endpoint",
        "idx_station_ingest_recent_received",
        "idx_station_ingest_recent_endpoint_auth",
        "idx_station_ingest_recent_station_auth",
        "idx_station_ingest_receipt_hour",
        "idx_station_ingest_slot_summary_retention",
        "idx_monitoring_business_current_site_scheduled",
        "idx_monitoring_business_valid_endpoint_factor",
    }
    existing_indexes = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    missing_read_indexes = required_read_indexes - existing_indexes
    if missing_read_indexes:
        raise MigrationError(
            "station monitoring read indexes are missing: "
            + ", ".join(sorted(missing_read_indexes)))
    pending_index_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
        ("idx_ingest_raw_pending_normalization",),
    ).fetchone()
    normalized_pending_index_sql = "".join(
        str(pending_index_sql[0] if pending_index_sql else "").lower().split())
    expected_pending_index_clause = (
        "oningest_raw_frames(id)wherepersistence_statein"
        "('pending_parse','pending_reparse')"
    )
    if expected_pending_index_clause not in normalized_pending_index_sql:
        raise MigrationError("pending normalization partial index is missing or incompatible")
    expected_factor_tables = {"station_expected_factor_configs", "station_expected_factors"}
    if expected_factor_tables - existing:
        raise MigrationError("station expected-factor tables are missing")
    _require_unique_columns(connection, "station_expected_factor_configs", ("site_id", "version"))
    _require_unique_columns(connection, "station_expected_factor_configs", ("site_id", "idempotency_key"))
    _require_unique_columns(connection, "station_expected_factors", ("config_id", "business_metric"))
    expected_factor_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(station_expected_factors)")
    }
    if {"site_id", "business_metric", "display_order", "effective_from"} - expected_factor_columns:
        raise MigrationError("station expected-factor item contract is missing or incompatible")
    current_expected_index = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_station_expected_factor_current'"
    ).fetchone()
    normalized_current_expected_sql = "".join(
        str(current_expected_index[0] if current_expected_index else "").upper().split())
    if ("CREATEUNIQUEINDEX" not in normalized_current_expected_sql or
            "ONSTATION_EXPECTED_FACTOR_CONFIGS(SITE_ID)" not in normalized_current_expected_sql or
            "WHEREEFFECTIVE_TOISNULL" not in normalized_current_expected_sql):
        raise MigrationError("station expected-factor current-version index is missing or incompatible")
    if "station_service_policy_events" not in existing:
        raise MigrationError("station service-policy table is missing")
    service_policy_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(station_service_policy_events)")
    }
    required_service_policy_columns = {
        "site_id", "version", "previous_service_status", "service_status",
        "previous_reporting_condition", "reporting_condition", "effective_from",
        "reason", "changed_by_user_id", "idempotency_key", "request_fingerprint",
        "created_at",
    }
    if required_service_policy_columns - service_policy_columns:
        raise MigrationError("station service-policy contract is missing or incompatible")
    _require_unique_columns(connection, "station_service_policy_events", ("site_id", "version"))
    _require_unique_columns(
        connection, "station_service_policy_events", ("site_id", "idempotency_key"))
    required_service_policy_indexes = {
        "idx_station_service_policy_site_version",
        "idx_station_service_policy_site_effective",
    }
    if required_service_policy_indexes - existing_indexes:
        raise MigrationError("station service-policy indexes are missing")
    required_master_tables = {"site_name_aliases", "station_master_refresh_audits"}
    if required_master_tables - existing:
        raise MigrationError("station master refresh tables are missing")
    site_columns = {row[1]: row for row in connection.execute("PRAGMA table_info(sites)")}
    master_status = site_columns.get("master_status")
    if master_status is None or not master_status[3] or str(master_status[4]).strip("'") != "active":
        raise MigrationError("station master status column is missing or incompatible")
    audit_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(station_master_refresh_audits)")
    }
    if {"source_fingerprint", "preview_fingerprint", "accepted_rows", "applied_at"} - audit_columns:
        raise MigrationError("station master refresh audit contract is missing or incompatible")
    _require_unique_columns(connection, "site_name_aliases", ("site_id", "normalized_alias"))
    _require_unique_columns(connection, "station_master_refresh_audits", ("source_fingerprint",))
    _require_unique_columns(connection, "station_master_refresh_audits", ("preview_fingerprint",))
    schedule_columns = {
        row[1]: row for row in connection.execute("PRAGMA table_info(monitoring_business_schedules)")
    }
    delay_column = schedule_columns.get("result_delay_seconds")
    if delay_column is None or not delay_column[3] or str(delay_column[4]) != "0":
        raise MigrationError("monitoring result delay column is missing or incompatible")
    schedule_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='monitoring_business_schedules'"
    ).fetchone()
    schedule_sql = "".join(str(schedule_sql_row[0]).upper().split()) if schedule_sql_row else ""
    if "CHECK(RESULT_DELAY_SECONDSBETWEEN0ANDINTERVAL_SECONDS)" not in schedule_sql:
        raise MigrationError("monitoring result delay constraint is missing or incompatible")
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
    backup_dir: Path | None,
    *,
    app_version: str = "station-ingestion-v1",
    isolated_test: bool = False,
    external_recovery_confirmed: bool = False,
) -> tuple[bool, Path | None]:
    database = Path(database)
    backup_dir = Path(backup_dir) if backup_dir is not None else None
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
            verify_station_monitoring_contract(connection)
            return False, None
        before_schema = _schema_snapshot(connection)

    if backup_dir is None and not external_recovery_confirmed:
        raise MigrationError(
            "migration requires either a local backup directory or a verified external recovery set")
    backup_path = backup_database(database, backup_dir) if backup_dir is not None else None
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
        verify_station_monitoring_contract(connection)
        after_schema = _schema_snapshot(connection)
        _verify_existing_schema_changes(
            before_schema, after_schema, tuple(version for version, _ in pending),
        )
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
        if backup_path is not None:
            restore_backup(database, backup_path)
            with closing(sqlite3.connect(str(database))) as restored:
                if restored.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise MigrationError("migration failed and automatic restore verification failed")
                if before_foreign_rows is not None and _foreign_key_violations(restored) != before_foreign_rows:
                    raise MigrationError("migration failed and automatic restore verification failed")
        else:
            with closing(sqlite3.connect(str(database))) as rolled_back:
                unchanged = _schema_snapshot(rolled_back) == before_schema
                foreign_unchanged = (before_foreign_rows is None
                                     or _foreign_key_violations(rolled_back) == before_foreign_rows)
            if not unchanged or not foreign_unchanged:
                raise MigrationError(
                    "migration failed after external recovery was selected; restore the verified recovery set")
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
