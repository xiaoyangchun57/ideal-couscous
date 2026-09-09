"""Offline-only provisioning for one station-monitoring endpoint configuration."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from .migrate_station_ingestion import MigrationError, verify_station_monitoring_schema
    from .sl651_server import credential_hmac
except ImportError:  # pragma: no cover - direct command execution
    from migrate_station_ingestion import MigrationError, verify_station_monitoring_schema
    from sl651_server import credential_hmac


class ProvisionError(RuntimeError):
    pass


def _utc_time(value: object, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise ProvisionError(f"{field} must be an ISO timestamp with timezone")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ProvisionError(f"{field} must be an ISO timestamp with timezone") from exc
    if parsed.tzinfo is None:
        raise ProvisionError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ProvisionError(f"{field} is invalid")
    return value.strip()


def _positive_integer(value: object, field: str, *, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProvisionError(f"{field} must be a positive integer")
    return value


def _nonnegative_integer(value: object, field: str, *, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProvisionError(f"{field} must be a non-negative integer")
    return value


def _reject_secret_fields(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if any(token in str(key).lower() for token in ("password", "credential", "pepper", "hmac", "secret")):
                raise ProvisionError("private configuration must not contain credentials or secrets")
            _reject_secret_fields(child)
    elif isinstance(value, list):
        for child in value:
            _reject_secret_fields(child)


def _require_keys(value: dict[str, Any], required: set[str], allowed: set[str], label: str) -> None:
    if set(value) - allowed or required - set(value):
        raise ProvisionError(f"{label} fields are invalid")


def load_configuration(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvisionError("private configuration cannot be read as JSON") from exc
    if not isinstance(document, dict):
        raise ProvisionError("private configuration must be an object")
    _reject_secret_fields(document)
    _require_keys(
        document,
        {"station_code", "business_site_id", "timezone", "effective_from", "expected_interval_seconds", "mappings"},
        {
            "station_code", "business_site_id", "rtu_asset_code", "instrument_asset_code", "timezone",
            "effective_from", "effective_to", "expected_granularity", "expected_interval_seconds", "mappings",
        },
        "configuration",
    )
    station_code = _optional_text(document["station_code"], "station_code")
    if station_code is None or not station_code.isascii() or not station_code.isalnum() or not 4 <= len(station_code) <= 20:
        raise ProvisionError("station_code is invalid")
    site_id = _positive_integer(document["business_site_id"], "business_site_id")
    timezone_name = _optional_text(document["timezone"], "timezone")
    try:
        ZoneInfo(timezone_name or "")
    except ZoneInfoNotFoundError as exc:
        raise ProvisionError("timezone is unavailable") from exc
    effective_from = _utc_time(document["effective_from"], "effective_from")
    effective_to = _utc_time(document.get("effective_to"), "effective_to", nullable=True)
    if effective_to is not None and effective_to <= effective_from:
        raise ProvisionError("profile effective period is invalid")
    granularity = _optional_text(document.get("expected_granularity", "realtime"), "expected_granularity")
    interval = _positive_integer(document["expected_interval_seconds"], "expected_interval_seconds")
    mappings = document["mappings"]
    if not isinstance(mappings, list) or not mappings:
        raise ProvisionError("at least one factor mapping is required")
    normalized_mappings = []
    for item in mappings:
        if not isinstance(item, dict):
            raise ProvisionError("factor mapping is invalid")
        _require_keys(
            item,
            {"protocol_code"},
            {
                "protocol_code", "business_metric", "instrument_asset_code", "expected_interval_seconds",
                "tolerance_seconds", "effective_from", "effective_to",
            },
            "factor mapping",
        )
        protocol_code = _optional_text(item["protocol_code"], "protocol_code")
        if protocol_code is None or not protocol_code.isascii() or len(protocol_code) > 16:
            raise ProvisionError("protocol_code is invalid")
        mapping_from = _utc_time(item.get("effective_from", effective_from), "mapping effective_from")
        mapping_to = _utc_time(item.get("effective_to", effective_to), "mapping effective_to", nullable=True)
        if mapping_to is not None and mapping_to <= mapping_from:
            raise ProvisionError("mapping effective period is invalid")
        if mapping_from < effective_from or (effective_to is not None and (mapping_to is None or mapping_to > effective_to)):
            raise ProvisionError("mapping period must be contained by the endpoint profile")
        normalized_mappings.append({
            "protocol_code": protocol_code.upper(),
            "business_metric": _optional_text(item.get("business_metric"), "business_metric"),
            "instrument_asset_code": _optional_text(item.get("instrument_asset_code", document.get("instrument_asset_code")), "instrument_asset_code"),
            "expected_interval_seconds": _positive_integer(item.get("expected_interval_seconds", interval), "mapping expected_interval_seconds"),
            "tolerance_seconds": _nonnegative_integer(item.get("tolerance_seconds", 0), "tolerance_seconds"),
            "effective_from": mapping_from,
            "effective_to": mapping_to,
        })
    normalized_mappings.sort(key=lambda item: (item["protocol_code"], item["effective_from"], item["effective_to"] or ""))
    return {
        "station_code": station_code,
        "business_site_id": site_id,
        "rtu_asset_code": _optional_text(document.get("rtu_asset_code"), "rtu_asset_code"),
        "instrument_asset_code": _optional_text(document.get("instrument_asset_code"), "instrument_asset_code"),
        "timezone": timezone_name,
        "effective_from": effective_from,
        "effective_to": effective_to,
        "expected_granularity": granularity,
        "expected_interval_seconds": interval,
        "mappings": normalized_mappings,
    }


def configuration_fingerprint(configuration: dict[str, Any]) -> str:
    encoded = json.dumps(configuration, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()[:16]


def _periods_overlap(left_from: str, left_to: str | None, right_from: str, right_to: str | None) -> bool:
    end = "9999-12-31T23:59:59+00:00"
    return left_from < (right_to or end) and right_from < (left_to or end)


def _connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(database), timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _require_existing_database(database: Path) -> Path:
    database = Path(database)
    if not database.exists() or not database.is_file():
        raise ProvisionError("database path must name an existing database file")
    return database


def _verify_assets_when_available(connection: sqlite3.Connection, configuration: dict[str, Any]) -> None:
    asset_codes = {code for code in [configuration["rtu_asset_code"], configuration["instrument_asset_code"]] if code}
    asset_codes.update(mapping["instrument_asset_code"] for mapping in configuration["mappings"] if mapping["instrument_asset_code"])
    if not asset_codes:
        return
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for table in tables:
        if "asset" not in table.lower() and "equipment" not in table.lower():
            continue
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        code_column = next((name for name in ("asset_code", "instrument_asset_code", "rtu_asset_code") if name in columns), None)
        site_column = next((name for name in ("business_site_id", "site_id") if name in columns), None)
        if not code_column or not site_column:
            continue
        for asset_code in asset_codes:
            row = connection.execute(
                f"SELECT {site_column} FROM {table} WHERE {code_column}=? LIMIT 1", (asset_code,)
            ).fetchone()
            if row is not None and row[0] != configuration["business_site_id"]:
                raise ProvisionError("authoritative asset record belongs to another business site")


def _validate_configuration_for_database(connection: sqlite3.Connection, configuration: dict[str, Any]) -> None:
    verify_station_monitoring_schema(connection)
    if not connection.execute("SELECT 1 FROM sites WHERE id=?", (configuration["business_site_id"],)).fetchone():
        raise ProvisionError("business site does not exist")
    defined = {
        row["protocol_code"]: row
        for row in connection.execute("SELECT protocol_code,business_metric,is_published FROM monitoring_factor_definitions")
    }
    for mapping in configuration["mappings"]:
        definition = defined.get(mapping["protocol_code"])
        if definition is None:
            raise ProvisionError("factor mapping references an undefined protocol factor")
        if not definition["is_published"] or not definition["business_metric"]:
            raise ProvisionError("factor mapping references an unpublished protocol factor")
        if mapping["business_metric"] is None:
            mapping["business_metric"] = definition["business_metric"]
        elif mapping["business_metric"] != definition["business_metric"]:
            raise ProvisionError("factor mapping business metric conflicts with the published protocol definition")
    for index, mapping in enumerate(configuration["mappings"]):
        for other in configuration["mappings"][index + 1:]:
            if mapping["protocol_code"] == other["protocol_code"] and _periods_overlap(
                mapping["effective_from"], mapping["effective_to"], other["effective_from"], other["effective_to"],
            ):
                raise ProvisionError("configuration contains overlapping factor mappings")
    _verify_assets_when_available(connection, configuration)


def _row_matches(row: sqlite3.Row, expected: dict[str, Any]) -> bool:
    return all(row[key] == value for key, value in expected.items())


def _profile_expected(configuration: dict[str, Any]) -> dict[str, Any]:
    return {
        "business_site_id": configuration["business_site_id"], "rtu_asset_code": configuration["rtu_asset_code"],
        "instrument_asset_code": configuration["instrument_asset_code"], "timezone": configuration["timezone"],
        "enabled": 1, "expected_granularity": configuration["expected_granularity"],
        "expected_interval_seconds": configuration["expected_interval_seconds"], "effective_from": configuration["effective_from"],
        "effective_to": configuration["effective_to"],
    }


def _mapping_expected(mapping: dict[str, Any]) -> dict[str, Any]:
    return dict(mapping, enabled=1)


def _check_existing_configuration(connection: sqlite3.Connection, configuration: dict[str, Any], credential: bytes | None = None) -> sqlite3.Row | None:
    endpoint = connection.execute("SELECT * FROM trusted_endpoints WHERE station_code=?", (configuration["station_code"],)).fetchone()
    if endpoint is None:
        return None
    if endpoint["business_site_id"] != configuration["business_site_id"]:
        raise ProvisionError("station code is already bound to another business site")
    if credential is not None and endpoint["credential_hmac"] != credential_hmac(credential, _credential_pepper()):
        raise ProvisionError("station code is already bound with different credentials")
    profile = connection.execute(
        "SELECT * FROM monitoring_endpoint_profiles WHERE endpoint_id=? AND effective_from=?",
        (endpoint["id"], configuration["effective_from"]),
    ).fetchone()
    if profile is not None and not _row_matches(profile, _profile_expected(configuration)):
        raise ProvisionError("endpoint profile conflicts with the requested effective period")
    for existing in connection.execute("SELECT effective_from,effective_to FROM monitoring_endpoint_profiles WHERE endpoint_id=?", (endpoint["id"],)):
        if existing["effective_from"] != configuration["effective_from"] and _periods_overlap(
            existing["effective_from"], existing["effective_to"], configuration["effective_from"], configuration["effective_to"],
        ):
            raise ProvisionError("endpoint profile overlaps an existing effective period")
    for mapping in configuration["mappings"]:
        existing = connection.execute(
            "SELECT * FROM monitoring_factor_mappings WHERE endpoint_id=? AND protocol_code=? AND effective_from=?",
            (endpoint["id"], mapping["protocol_code"], mapping["effective_from"]),
        ).fetchone()
        if existing is not None and not _row_matches(existing, _mapping_expected(mapping)):
            raise ProvisionError("factor mapping conflicts with the requested effective period")
        for existing_period in connection.execute(
            "SELECT effective_from,effective_to FROM monitoring_factor_mappings WHERE endpoint_id=? AND protocol_code=?",
            (endpoint["id"], mapping["protocol_code"]),
        ):
            if existing_period["effective_from"] != mapping["effective_from"] and _periods_overlap(
                existing_period["effective_from"], existing_period["effective_to"],
                mapping["effective_from"], mapping["effective_to"],
            ):
                raise ProvisionError("factor mapping overlaps an existing effective period")
    return endpoint


def _credential_pepper() -> str:
    pepper = os.environ.get("SL651_CREDENTIAL_PEPPER")
    if not pepper:
        raise ProvisionError("SL651_CREDENTIAL_PEPPER is required in the private runtime environment")
    return pepper


def _read_credential_from_stdin() -> bytes:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    value = stream.readline()
    if isinstance(value, str):
        value = value.encode("utf-8")
    encoded = bytes(value).rstrip(b"\r\n")
    if len(encoded) != 4 or any(character not in b"0123456789abcdefABCDEF" for character in encoded):
        raise ProvisionError("credential stdin must be exactly four hexadecimal characters")
    return bytes.fromhex(encoded.decode("ascii"))


def _apply(connection: sqlite3.Connection, configuration: dict[str, Any], credential: bytes) -> str:
    endpoint = _check_existing_configuration(connection, configuration, credential)
    if endpoint is not None and (not endpoint["enabled"] or endpoint["endpoint_state"] == "disabled"):
        raise ProvisionError("disabled endpoint must not be silently re-enabled")
    if endpoint is None:
        cursor = connection.execute(
            """INSERT INTO trusted_endpoints(station_code,credential_hmac,business_site_id,endpoint_state)
               VALUES (?,?,?,'bound')""",
            (configuration["station_code"], credential_hmac(credential, _credential_pepper()), configuration["business_site_id"]),
        )
        endpoint_id = int(cursor.lastrowid)
    else:
        endpoint_id = int(endpoint["id"])
    profile = connection.execute(
        "SELECT 1 FROM monitoring_endpoint_profiles WHERE endpoint_id=? AND effective_from=?",
        (endpoint_id, configuration["effective_from"]),
    ).fetchone()
    if profile is None:
        fields = _profile_expected(configuration)
        connection.execute(
            """INSERT INTO monitoring_endpoint_profiles(endpoint_id,business_site_id,rtu_asset_code,instrument_asset_code,
               timezone,enabled,expected_granularity,expected_interval_seconds,effective_from,effective_to)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (endpoint_id, *(fields[key] for key in (
                "business_site_id", "rtu_asset_code", "instrument_asset_code", "timezone", "enabled",
                "expected_granularity", "expected_interval_seconds", "effective_from", "effective_to",
            ))),
        )
    for mapping in configuration["mappings"]:
        existing = connection.execute(
            "SELECT 1 FROM monitoring_factor_mappings WHERE endpoint_id=? AND protocol_code=? AND effective_from=?",
            (endpoint_id, mapping["protocol_code"], mapping["effective_from"]),
        ).fetchone()
        if existing is None:
            connection.execute(
                """INSERT INTO monitoring_factor_mappings(endpoint_id,protocol_code,business_metric,instrument_asset_code,
                   expected_interval_seconds,tolerance_seconds,effective_from,effective_to,enabled)
                   VALUES (?,?,?,?,?,?,?,?,1)""",
                (endpoint_id, mapping["protocol_code"], mapping["business_metric"], mapping["instrument_asset_code"],
                 mapping["expected_interval_seconds"], mapping["tolerance_seconds"], mapping["effective_from"], mapping["effective_to"]),
            )
    return "applied"


def execute(action: str, database: Path, configuration: dict[str, Any], *, credential: bytes | None = None,
            offline_confirmed: bool = False, disable_confirmed: bool = False, reason: str | None = None) -> dict[str, Any]:
    if action not in {"plan", "apply", "verify", "disable"}:
        raise ProvisionError("action is invalid")
    if action in {"apply", "disable"} and not offline_confirmed:
        raise ProvisionError("offline confirmation is required before writing configuration")
    if action == "apply" and credential is None:
        raise ProvisionError("credential input is required for apply")
    if action == "disable" and (not disable_confirmed or not isinstance(reason, str) or not reason.strip()):
        raise ProvisionError("disable requires explicit confirmation and a reason")
    with closing(_connect(_require_existing_database(Path(database)))) as connection:
        if action == "disable":
            verify_station_monitoring_schema(connection)
            endpoint = connection.execute("SELECT * FROM trusted_endpoints WHERE station_code=?", (configuration["station_code"],)).fetchone()
            if endpoint is None or endpoint["business_site_id"] != configuration["business_site_id"]:
                raise ProvisionError("configured endpoint does not exist for this business site")
            if endpoint["enabled"] and endpoint["endpoint_state"] != "disabled":
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        "UPDATE trusted_endpoints SET enabled=0,endpoint_state='disabled',updated_at=? WHERE id=?",
                        (datetime.now(timezone.utc).replace(microsecond=0).isoformat(), endpoint["id"]),
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            result = "disabled"
        elif action == "apply":
            try:
                connection.execute("BEGIN IMMEDIATE")
                _validate_configuration_for_database(connection, configuration)
                result = _apply(connection, configuration, credential or b"")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        else:
            _validate_configuration_for_database(connection, configuration)
            endpoint = _check_existing_configuration(connection, configuration)
            unavailable = endpoint is not None and (not endpoint["enabled"] or endpoint["endpoint_state"] == "disabled")
            if action == "verify":
                if endpoint is None:
                    raise ProvisionError("configured endpoint does not exist")
                if unavailable:
                    raise ProvisionError("configured endpoint is disabled and unavailable")
                profile = connection.execute(
                    "SELECT * FROM monitoring_endpoint_profiles WHERE endpoint_id=? AND effective_from=?",
                    (endpoint["id"], configuration["effective_from"]),
                ).fetchone()
                if profile is None:
                    raise ProvisionError("configured endpoint profile does not exist")
                for mapping in configuration["mappings"]:
                    row = connection.execute(
                        "SELECT * FROM monitoring_factor_mappings WHERE endpoint_id=? AND protocol_code=? AND effective_from=?",
                        (endpoint["id"], mapping["protocol_code"], mapping["effective_from"]),
                    ).fetchone()
                    if row is None or not _row_matches(row, _mapping_expected(mapping)):
                        raise ProvisionError("configured factor mapping does not exist")
                credential_summary = str(endpoint["credential_hmac"] or "")
                if len(credential_summary) != 64 or any(character not in "0123456789abcdef" for character in credential_summary.lower()):
                    raise ProvisionError("configured endpoint credential summary is invalid")
                result = "verified"
            else:
                result = "unavailable" if unavailable else "ready"
    return {
        "action": action, "business_site_id": configuration["business_site_id"],
        "mapping_count": len(configuration["mappings"]), "configuration_fingerprint": configuration_fingerprint(configuration),
        "result": result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline station-monitoring provisioning")
    parser.add_argument("action", choices=("plan", "apply", "verify", "disable"))
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--offline-confirmation", action="store_true")
    parser.add_argument("--credential-stdin", action="store_true")
    parser.add_argument("--confirm-disable", action="store_true")
    parser.add_argument("--reason")
    arguments = parser.parse_args(argv)
    try:
        configuration = load_configuration(arguments.config)
        credential = _read_credential_from_stdin() if arguments.action == "apply" and arguments.credential_stdin else None
        if arguments.action == "apply" and not arguments.credential_stdin:
            raise ProvisionError("apply requires --credential-stdin")
        outcome = execute(
            arguments.action, arguments.database, configuration, credential=credential,
            offline_confirmed=arguments.offline_confirmation, disable_confirmed=arguments.confirm_disable,
            reason=arguments.reason,
        )
    except (ProvisionError, MigrationError) as exc:
        print(json.dumps({"action": arguments.action, "result": "failed", "reason": str(exc)}), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error):
        print(json.dumps({"action": arguments.action, "result": "failed", "reason": "database operation could not be completed"}), file=sys.stderr)
        return 3
    print(json.dumps(outcome, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
