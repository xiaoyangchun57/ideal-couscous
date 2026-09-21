"""Offline-only provisioning for one station-monitoring endpoint configuration."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from .migrate_station_ingestion import MigrationError, verify_station_monitoring_schema
    from .sl651_server import credential_hmac
except ImportError:  # pragma: no cover - direct command execution
    from migrate_station_ingestion import MigrationError, verify_station_monitoring_schema
    from sl651_server import credential_hmac


class ProvisionError(RuntimeError):
    pass


FORMAL_STATION_CODE_ROWS = 43
FORMAL_STATION_CODE_IGNORED_ROWS = 1
B2_INTERVAL_SECONDS = 14400
B2_TIMEZONE = "Asia/Shanghai"
B2_ANCHOR_LOCAL_TIME = "00:00:00"
B2_TOLERANCE_SECONDS = 0
B2_RESULT_DELAY_SECONDS = 7200


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
            "protocol_code": protocol_code,
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


def _read_hj212_credential_from_stdin() -> bytes:
    """Read the private text credential without imposing the binary RTU format."""
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    value = stream.readline()
    if isinstance(value, str):
        value = value.encode("utf-8")
    credential = bytes(value).rstrip(b"\r\n")
    if not credential or len(credential) > 128 or not credential.isascii():
        raise ProvisionError("HJ212 credential stdin must be non-empty ASCII text")
    return credential


def _xlsx_cell_text(cell: ElementTree.Element, shared_strings: list[str]) -> str | None:
    cell_type = cell.attrib.get("t")
    if cell_type == "s":
        value = cell.findtext("{*}v")
        if value is None or not value.isdigit() or int(value) >= len(shared_strings):
            raise ProvisionError("station-code workbook contains an invalid shared string")
        return shared_strings[int(value)]
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//{*}t"))
    if cell_type in {"str", None}:
        # Numeric cells cannot safely represent opaque MN values (for example, leading zeroes).
        value = cell.findtext("{*}v")
        if value is not None and cell_type is None:
            raise ProvisionError("station-code workbook MN cells must be text")
        return value
    raise ProvisionError("station-code workbook contains an unsupported cell type")


def load_station_code_workbook(path: Path) -> tuple[list[dict[str, object]], int]:
    """Read only Sheet1 columns B/C and retain MN as an opaque text identifier."""
    try:
        with ZipFile(path) as archive:
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                shared_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                shared_strings = ["".join(node.text or "" for node in item.findall(".//{*}t"))
                                  for item in shared_root.findall("{*}si")]
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            target = next((sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
                           for sheet in workbook.findall(".//{*}sheet") if sheet.attrib.get("name") == "Sheet1"), None)
            if target is None:
                raise ProvisionError("station-code workbook must contain Sheet1")
            relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            location = next((item.attrib.get("Target") for item in relationships.findall("{*}Relationship")
                             if item.attrib.get("Id") == target), None)
            if not location:
                raise ProvisionError("station-code workbook Sheet1 is unavailable")
            sheet_path = "xl/" + location.lstrip("/")
            sheet = ElementTree.fromstring(archive.read(sheet_path))
    except (OSError, KeyError, BadZipFile, ElementTree.ParseError) as exc:
        raise ProvisionError("station-code workbook cannot be read") from exc
    accepted: list[dict[str, object]] = []
    ignored = 0
    for row in sheet.findall(".//{*}row"):
        row_number = int(row.attrib.get("r", "0"))
        values: dict[str, str | None] = {"B": None, "C": None}
        for cell in row.findall("{*}c"):
            reference = cell.attrib.get("r", "")
            column = "".join(character for character in reference if character.isalpha())
            if column in values:
                values[column] = _xlsx_cell_text(cell, shared_strings)
        station = values["B"].strip() if isinstance(values["B"], str) else None
        mn = values["C"].strip() if isinstance(values["C"], str) else None
        if row_number == 1 and station and mn and ("站" in station or "station" in station.lower() or "site" in station.lower()):
            continue
        if not station or not mn:
            ignored += 1
            continue
        if len(station) > 128 or len(mn) > 128 or not mn.isascii():
            raise ProvisionError("station-code workbook contains an invalid station name or MN")
        accepted.append({"row": row_number, "station_name": station, "station_code": mn})
    return accepted, ignored


def _station_code_preview_from_connection(
    connection: sqlite3.Connection, records: list[dict[str, object]], ignored_rows: int, *, identity_only: bool = False,
) -> dict[str, object]:
    verify_station_monitoring_schema(connection)
    sites: dict[str, list[int]] = {}
    for row in connection.execute("SELECT id,name FROM sites"):
        sites.setdefault(str(row["name"]).strip(), []).append(int(row["id"]))
    endpoint_rows = list(connection.execute("SELECT id,station_code,business_site_id,enabled,endpoint_state FROM trusted_endpoints"))
    names = [str(record["station_name"]) for record in records]
    codes = [str(record["station_code"]) for record in records]
    errors: list[str] = []
    if len(set(names)) != len(names):
        errors.append("duplicate_station_name")
    if len(set(codes)) != len(codes):
        errors.append("duplicate_station_code")
    planned: list[dict[str, object]] = []
    for record in records:
        matched = sites.get(str(record["station_name"]), [])
        if len(matched) != 1:
            if matched:
                errors.append("ambiguous_station")
                continue
            if identity_only:
                planned.append(dict(record, business_site_id=None, endpoint_state="unbound"))
                continue
            errors.append("unmatched_station")
            continue
        planned.append(dict(record, business_site_id=matched[0], endpoint_state="bound"))
    planned_by_code = {str(item["station_code"]): item for item in planned}
    for endpoint in endpoint_rows:
        item = planned_by_code.get(str(endpoint["station_code"]))
        if item is None:
            continue
        if not endpoint["enabled"] or endpoint["endpoint_state"] == "disabled":
            errors.append("disabled_station_code")
        elif identity_only and item["business_site_id"] is None:
            if endpoint["business_site_id"] is not None or endpoint["endpoint_state"] != "unbound":
                errors.append("station_code_conflict")
        elif identity_only and endpoint["business_site_id"] is None:
            errors.append("unbound_endpoint_requires_binding")
        elif endpoint["business_site_id"] != item["business_site_id"] or endpoint["endpoint_state"] != "bound":
            errors.append("station_code_conflict")
    replacement_ids: dict[int, list[int]] = {}
    for endpoint in endpoint_rows:
        if not endpoint["enabled"] or (identity_only and endpoint["endpoint_state"] != "bound"):
            continue
        for item in planned:
            if (endpoint["business_site_id"] == item["business_site_id"]
                    and endpoint["station_code"] != item["station_code"]):
                replacement_ids.setdefault(int(item["business_site_id"]), []).append(int(endpoint["id"]))
                break
    disable_ids: list[int] = []
    for endpoint_ids in replacement_ids.values():
        if identity_only and len(endpoint_ids) > 1:
            errors.append("ambiguous_superseded_station_identity")
            continue
        disable_ids.extend(endpoint_ids)
    if not identity_only and (len(planned) != FORMAL_STATION_CODE_ROWS or ignored_rows != FORMAL_STATION_CODE_IGNORED_ROWS):
        errors.append("unexpected_record_count")
    return {"accepted_rows": len(planned), "ignored_rows": ignored_rows, "errors": sorted(set(errors)),
            "records": [dict(record) for record in records], "planned": planned,
            "disable_endpoint_ids": sorted(disable_ids)}


def preview_station_code_import(database: Path, workbook: Path) -> dict[str, object]:
    """Return a zero-write validation record. Callers must not print its planned rows."""
    database = _require_existing_database(database)
    records, ignored_rows = load_station_code_workbook(workbook)
    with closing(_connect(database)) as connection:
        return _station_code_preview_from_connection(connection, records, ignored_rows)


def _station_code_import_summary(preview: dict[str, object], template: dict[str, Any] | None = None) -> dict[str, object]:
    fingerprint_source = {
        "candidates": [
            {"station_name": str(item["station_name"]), "station_code": str(item["station_code"])}
            for item in sorted(preview.get("records", preview["planned"]), key=lambda item: (str(item["station_name"]), str(item["station_code"])))
        ],
        "template": template,
    }
    encoded = json.dumps(fingerprint_source, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "accepted_rows": int(preview["accepted_rows"]),
        "ignored_rows": int(preview["ignored_rows"]),
        "conflict_categories": list(preview["errors"]),
        "fingerprint": "sha256:" + hashlib.sha256(encoded).hexdigest()[:16],
    }


def _station_code_identity_summary(preview: dict[str, object]) -> dict[str, object]:
    """Lock B1 identity imports to workbook identities, never to a B2 template."""
    fingerprint_source = {
        "candidates": [
            {"station_name": str(item["station_name"]), "station_code": str(item["station_code"])}
            for item in sorted(preview["records"], key=lambda item: (str(item["station_name"]), str(item["station_code"])))
        ],
    }
    encoded = json.dumps(fingerprint_source, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    fingerprint = "sha256:" + hashlib.sha256(encoded).hexdigest()[:16]
    approved_fingerprint = os.environ.get("STATION_IDENTITY_APPROVED_SOURCE_FINGERPRINT", "")
    return {
        "accepted_rows": int(preview["accepted_rows"]),
        "ignored_rows": int(preview["ignored_rows"]),
        "bound_rows": sum(1 for item in preview["planned"] if item["business_site_id"] is not None),
        "unbound_rows": sum(1 for item in preview["planned"] if item["business_site_id"] is None),
        "retired_rows": len(preview["disable_endpoint_ids"]),
        "source_status": (
            "ready" if int(preview["accepted_rows"]) == FORMAL_STATION_CODE_ROWS
            and int(preview["ignored_rows"]) == FORMAL_STATION_CODE_IGNORED_ROWS
            and approved_fingerprint == fingerprint else "not_ready"
        ),
        "conflict_categories": list(preview["errors"]),
        "fingerprint": fingerprint,
    }


def preview_station_code_identity_import(database: Path, workbook: Path) -> dict[str, object]:
    """Preview the B1 station identity transaction without requiring a monitoring profile."""
    database = _require_existing_database(database)
    records, ignored_rows = load_station_code_workbook(workbook)
    with closing(_connect(database)) as connection:
        preview = _station_code_preview_from_connection(connection, records, ignored_rows, identity_only=True)
        disabled_codes = {
            str(row["station_code"])
            for row in connection.execute("SELECT station_code FROM trusted_endpoints WHERE enabled=0 OR endpoint_state='disabled'")
        }
    if any(str(item["station_code"]) in disabled_codes for item in preview["planned"]):
        preview = dict(preview, errors=sorted(set(preview["errors"]) | {"disabled_station_code"}))
    return preview


def _validate_station_code_identity_request(
    preview: dict[str, object], expected_fingerprint: str | None,
    expected_accepted_rows: int | None, expected_ignored_rows: int | None,
) -> dict[str, object]:
    if (not expected_fingerprint or expected_accepted_rows is None or expected_ignored_rows is None
            or expected_accepted_rows < FORMAL_STATION_CODE_ROWS
            or expected_ignored_rows != FORMAL_STATION_CODE_IGNORED_ROWS):
        raise ProvisionError("station identity import requires the approved 43+1 preview lock")
    if expected_accepted_rows > FORMAL_STATION_CODE_ROWS:
        raise ProvisionError("expanded station identity sources are not ready for application")
    if preview["errors"]:
        raise ProvisionError("station identity import validation failed")
    summary = _station_code_identity_summary(preview)
    if summary["source_status"] != "ready":
        raise ProvisionError("station identity approved source is unavailable or differs")
    if (summary["accepted_rows"] != expected_accepted_rows
            or summary["ignored_rows"] != expected_ignored_rows):
        raise ProvisionError("station identity approved source count changed")
    if summary["fingerprint"] != expected_fingerprint:
        raise ProvisionError("station identity workbook changed after preview")
    return summary


def _apply_station_code_identity(connection: sqlite3.Connection, item: dict[str, object], credential: bytes) -> None:
    endpoint = connection.execute("SELECT * FROM trusted_endpoints WHERE station_code=?", (item["station_code"],)).fetchone()
    expected_hmac = credential_hmac(credential, _credential_pepper())
    business_site_id = item["business_site_id"]
    endpoint_state = "bound" if business_site_id is not None else "unbound"
    if endpoint is None:
        connection.execute(
            """INSERT INTO trusted_endpoints(station_code,credential_hmac,business_site_id,endpoint_state)
               VALUES (?,?,?,?)""",
            (item["station_code"], expected_hmac, business_site_id, endpoint_state),
        )
        return
    if not endpoint["enabled"] or endpoint["endpoint_state"] == "disabled":
        raise ProvisionError("disabled station identity must not be silently re-enabled")
    if endpoint["credential_hmac"] != expected_hmac:
        raise ProvisionError("station identity is already bound with different credentials")
    if business_site_id is None:
        if endpoint["business_site_id"] is not None or endpoint["endpoint_state"] != "unbound":
            raise ProvisionError("station code is already bound to another business site")
        return
    if endpoint["business_site_id"] is None:
        raise ProvisionError("unbound station identity requires explicit binding")
    if endpoint["business_site_id"] != business_site_id or endpoint["endpoint_state"] != "bound":
        raise ProvisionError("station code is already bound to another business site")


def apply_station_code_identity_import(
    database: Path, workbook: Path, *, credential: bytes, offline_confirmed: bool,
    retire_confirmed: bool, expected_fingerprint: str | None,
    expected_accepted_rows: int | None, expected_ignored_rows: int | None,
) -> dict[str, int]:
    """Atomically apply only B1 authentication identities; profiles and mappings are untouched."""
    if not offline_confirmed:
        raise ProvisionError("offline confirmation is required before writing station identities")
    if not credential or len(credential) > 128 or not credential.isascii():
        raise ProvisionError("HJ212 credential must be non-empty ASCII text")
    database = _require_existing_database(database)
    records, ignored_rows = load_station_code_workbook(workbook)
    with closing(_connect(database)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            preview = _station_code_preview_from_connection(connection, records, ignored_rows, identity_only=True)
            disabled_codes = {
                str(row["station_code"])
                for row in connection.execute("SELECT station_code FROM trusted_endpoints WHERE enabled=0 OR endpoint_state='disabled'")
            }
            if any(str(item["station_code"]) in disabled_codes for item in preview["planned"]):
                preview = dict(preview, errors=sorted(set(preview["errors"]) | {"disabled_station_code"}))
            _validate_station_code_identity_request(
                preview, expected_fingerprint, expected_accepted_rows, expected_ignored_rows,
            )
            if preview["disable_endpoint_ids"] and not retire_confirmed:
                raise ProvisionError("explicit confirmation is required before retiring superseded station identities")
            expected_hmac = credential_hmac(credential, _credential_pepper())
            for endpoint_id in preview["disable_endpoint_ids"]:
                endpoint = connection.execute("SELECT credential_hmac FROM trusted_endpoints WHERE id=?", (endpoint_id,)).fetchone()
                if endpoint is None or endpoint["credential_hmac"] != expected_hmac:
                    raise ProvisionError("superseded station identity credential differs from the private input")
            for item in preview["planned"]:
                _apply_station_code_identity(connection, item, credential)
            for endpoint_id in preview["disable_endpoint_ids"]:
                connection.execute("UPDATE trusted_endpoints SET enabled=0,endpoint_state='disabled',updated_at=? WHERE id=?",
                                   (datetime.now(timezone.utc).replace(microsecond=0).isoformat(), endpoint_id))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {"accepted_rows": int(preview["accepted_rows"]), "ignored_rows": int(preview["ignored_rows"]),
            "bound_rows": sum(1 for item in preview["planned"] if item["business_site_id"] is not None),
            "unbound_rows": sum(1 for item in preview["planned"] if item["business_site_id"] is None)}


def verify_station_code_identity_import(
    database: Path, workbook: Path, *, credential: bytes, expected_fingerprint: str,
    expected_accepted_rows: int, expected_ignored_rows: int,
) -> dict[str, object]:
    if not credential or len(credential) > 128 or not credential.isascii():
        raise ProvisionError("HJ212 credential must be non-empty ASCII text")
    database = _require_existing_database(database)
    records, ignored_rows = load_station_code_workbook(workbook)
    with closing(_connect(database)) as connection:
        preview = _station_code_preview_from_connection(connection, records, ignored_rows, identity_only=True)
        disabled_codes = {
            str(row["station_code"])
            for row in connection.execute("SELECT station_code FROM trusted_endpoints WHERE enabled=0 OR endpoint_state='disabled'")
        }
        if any(str(item["station_code"]) in disabled_codes for item in preview["planned"]):
            preview = dict(preview, errors=sorted(set(preview["errors"]) | {"disabled_station_code"}))
        summary = _validate_station_code_identity_request(
            preview, expected_fingerprint, expected_accepted_rows, expected_ignored_rows,
        )
        expected_hmac = credential_hmac(credential, _credential_pepper())
        for item in preview["planned"]:
            endpoint = connection.execute("SELECT * FROM trusted_endpoints WHERE station_code=?", (item["station_code"],)).fetchone()
            expected_state = "bound" if item["business_site_id"] is not None else "unbound"
            if (endpoint is None or endpoint["business_site_id"] != item["business_site_id"] or not endpoint["enabled"]
                    or endpoint["endpoint_state"] != expected_state):
                raise ProvisionError("station identity endpoint is unavailable")
            if endpoint["credential_hmac"] != expected_hmac:
                raise ProvisionError("station identity credential summary differs from the private input")
            if item["business_site_id"] is not None and connection.execute(
                """SELECT 1 FROM trusted_endpoints
                   WHERE enabled=1 AND endpoint_state='bound' AND business_site_id=? AND station_code<>?""",
                (item["business_site_id"], item["station_code"]),
            ).fetchone() is not None:
                raise ProvisionError("station identity replacement is incomplete")
    return summary


def _station_code_identity_binding_preview_from_connection(
    connection: sqlite3.Connection, records: list[dict[str, object]], ignored_rows: int,
    target_business_site_id: int,
) -> dict[str, object]:
    """Preview only explicit promotions of existing B1 identities to known sites."""
    verify_station_monitoring_schema(connection)
    sites: dict[str, list[int]] = {}
    for row in connection.execute("SELECT id,name FROM sites"):
        sites.setdefault(str(row["name"]).strip(), []).append(int(row["id"]))
    endpoints = {
        str(row["station_code"]): row
        for row in connection.execute("SELECT * FROM trusted_endpoints")
    }
    names = [str(record["station_name"]) for record in records]
    codes = [str(record["station_code"]) for record in records]
    errors: list[str] = []
    if len(set(names)) != len(names):
        errors.append("duplicate_station_name")
    if len(set(codes)) != len(codes):
        errors.append("duplicate_station_code")
    planned: list[dict[str, object]] = []
    deferred_rows = 0
    for record in records:
        matched = sites.get(str(record["station_name"]), [])
        if not matched:
            deferred_rows += 1
            continue
        if len(matched) != 1:
            errors.append("ambiguous_station")
            continue
        if matched[0] != target_business_site_id:
            continue
        endpoint = endpoints.get(str(record["station_code"]))
        if endpoint is None:
            errors.append("missing_unbound_identity")
            continue
        if not endpoint["enabled"] or endpoint["endpoint_state"] == "disabled":
            errors.append("disabled_station_code")
            continue
        if endpoint["business_site_id"] is None and endpoint["endpoint_state"] == "unbound":
            planned.append(dict(record, business_site_id=matched[0], endpoint_id=int(endpoint["id"]), binding_state="bind"))
        elif endpoint["business_site_id"] == matched[0] and endpoint["endpoint_state"] == "bound":
            planned.append(dict(record, business_site_id=matched[0], endpoint_id=int(endpoint["id"]), binding_state="already_bound"))
        else:
            errors.append("station_code_conflict")
    if len(planned) != 1:
        errors.append("target_station_identity_unavailable")
    disable_ids: list[int] = []
    if planned:
        item = planned[0]
        old_endpoints = [row for row in endpoints.values() if row["enabled"]
                         and row["business_site_id"] == target_business_site_id
                         and row["station_code"] != item["station_code"]]
        if len(old_endpoints) > 1:
            errors.append("ambiguous_superseded_station_identity")
        elif old_endpoints:
            if old_endpoints[0]["endpoint_state"] != "bound":
                errors.append("station_code_conflict")
            else:
                disable_ids = [int(old_endpoints[0]["id"])]
    return {"accepted_rows": len(records), "ignored_rows": ignored_rows, "errors": sorted(set(errors)),
            "records": [dict(record) for record in records], "planned": planned, "disable_endpoint_ids": disable_ids,
            "deferred_rows": deferred_rows, "target_business_site_id": target_business_site_id}


def _station_code_identity_binding_summary(preview: dict[str, object]) -> dict[str, object]:
    summary = _station_code_identity_summary(preview)
    encoded = json.dumps({"source": summary["fingerprint"], "target_business_site_id": preview["target_business_site_id"],
                          "planned": [(item["endpoint_id"], item["business_site_id"], item["binding_state"])
                                      for item in preview["planned"]], "retire": preview["disable_endpoint_ids"]},
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    summary.update({
        "binding_rows": sum(1 for item in preview["planned"] if item["binding_state"] == "bind"),
        "already_bound_rows": sum(1 for item in preview["planned"] if item["binding_state"] == "already_bound"),
        "deferred_rows": int(preview["deferred_rows"]),
        "fingerprint": "sha256:" + hashlib.sha256(encoded).hexdigest()[:16],
    })
    return summary


def preview_station_code_identity_binding(database: Path, workbook: Path, target_business_site_id: int) -> dict[str, object]:
    database = _require_existing_database(database)
    records, ignored_rows = load_station_code_workbook(workbook)
    with closing(_connect(database)) as connection:
        return _station_code_identity_binding_preview_from_connection(connection, records, ignored_rows, target_business_site_id)


def _validate_station_code_identity_binding_request(
    preview: dict[str, object], expected_fingerprint: str | None,
    expected_accepted_rows: int | None, expected_ignored_rows: int | None,
) -> dict[str, object]:
    source = _station_code_identity_summary(preview)
    if (preview["errors"] or source["source_status"] != "ready"
            or source["accepted_rows"] != expected_accepted_rows or source["ignored_rows"] != expected_ignored_rows):
        raise ProvisionError("station identity binding validation failed")
    summary = _station_code_identity_binding_summary(preview)
    if not expected_fingerprint or summary["fingerprint"] != expected_fingerprint:
        raise ProvisionError("station identity binding changed after preview")
    return summary


def apply_station_code_identity_binding(
    database: Path, workbook: Path, *, credential: bytes, offline_confirmed: bool,
    target_business_site_id: int, retire_confirmed: bool,
    expected_fingerprint: str | None, expected_accepted_rows: int | None, expected_ignored_rows: int | None,
) -> dict[str, object]:
    """Atomically bind pre-existing B1 identities after the business site is created."""
    if not offline_confirmed:
        raise ProvisionError("offline confirmation is required before binding station identities")
    if not credential or len(credential) > 128 or not credential.isascii():
        raise ProvisionError("HJ212 credential must be non-empty ASCII text")
    database = _require_existing_database(database)
    records, ignored_rows = load_station_code_workbook(workbook)
    with closing(_connect(database)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            preview = _station_code_identity_binding_preview_from_connection(connection, records, ignored_rows, target_business_site_id)
            summary = _validate_station_code_identity_binding_request(
                preview, expected_fingerprint, expected_accepted_rows, expected_ignored_rows,
            )
            if preview["disable_endpoint_ids"] and not retire_confirmed:
                raise ProvisionError("explicit confirmation is required before retiring superseded station identities")
            expected_hmac = credential_hmac(credential, _credential_pepper())
            for endpoint_id in preview["disable_endpoint_ids"]:
                old = connection.execute("SELECT credential_hmac FROM trusted_endpoints WHERE id=?", (endpoint_id,)).fetchone()
                if old is None or old["credential_hmac"] != expected_hmac:
                    raise ProvisionError("superseded station identity credential differs from the private input")
            for item in preview["planned"]:
                endpoint = connection.execute("SELECT * FROM trusted_endpoints WHERE id=?", (item["endpoint_id"],)).fetchone()
                if endpoint is None or endpoint["credential_hmac"] != expected_hmac:
                    raise ProvisionError("station identity credential summary differs from the private input")
                if item["binding_state"] == "bind":
                    connection.execute(
                        "UPDATE trusted_endpoints SET business_site_id=?,endpoint_state='bound',updated_at=? WHERE id=?",
                        (item["business_site_id"], datetime.now(timezone.utc).replace(microsecond=0).isoformat(), endpoint["id"]),
                    )
            for endpoint_id in preview["disable_endpoint_ids"]:
                connection.execute("UPDATE trusted_endpoints SET enabled=0,endpoint_state='disabled',updated_at=? WHERE id=?",
                                   (datetime.now(timezone.utc).replace(microsecond=0).isoformat(), endpoint_id))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return summary


def verify_station_code_identity_binding(
    database: Path, workbook: Path, *, credential: bytes, expected_fingerprint: str,
    target_business_site_id: int,
    expected_accepted_rows: int, expected_ignored_rows: int,
) -> dict[str, object]:
    if not credential or len(credential) > 128 or not credential.isascii():
        raise ProvisionError("HJ212 credential must be non-empty ASCII text")
    database = _require_existing_database(database)
    records, ignored_rows = load_station_code_workbook(workbook)
    with closing(_connect(database)) as connection:
        preview = _station_code_identity_binding_preview_from_connection(connection, records, ignored_rows, target_business_site_id)
        summary = _validate_station_code_identity_binding_request(
            preview, expected_fingerprint, expected_accepted_rows, expected_ignored_rows,
        )
        if preview["disable_endpoint_ids"]:
            raise ProvisionError("station identity binding has another active endpoint")
        expected_hmac = credential_hmac(credential, _credential_pepper())
        for item in preview["planned"]:
            endpoint = connection.execute("SELECT * FROM trusted_endpoints WHERE id=?", (item["endpoint_id"],)).fetchone()
            if (endpoint is None or endpoint["business_site_id"] != item["business_site_id"]
                    or endpoint["endpoint_state"] != "bound" or endpoint["credential_hmac"] != expected_hmac):
                raise ProvisionError("station identity binding is unavailable")
    return summary


def load_station_code_import_template(path: Path) -> dict[str, Any]:
    """Load the common, non-secret profile/mapping contract for all workbook sites."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvisionError("station-code import template cannot be read as JSON") from exc
    if not isinstance(document, dict):
        raise ProvisionError("station-code import template must be an object")
    _reject_secret_fields(document)
    required = {"timezone", "effective_from", "expected_interval_seconds", "mappings"}
    allowed = required | {"rtu_asset_code", "instrument_asset_code", "effective_to", "expected_granularity"}
    _require_keys(document, required, allowed, "station-code import template")
    sample = dict(document, station_code="BULKTEMPLATE", business_site_id=1)
    return load_configuration_value(sample)


def load_configuration_value(document: dict[str, Any]) -> dict[str, Any]:
    """Validate a parsed configuration object; used by private bulk templates too."""
    # Keep the file reader as the public boundary while avoiding a temporary file for
    # the bulk template's per-site materialization.
    temporary = Path(os.devnull)
    del temporary
    _reject_secret_fields(document)
    _require_keys(
        document,
        {"station_code", "business_site_id", "timezone", "effective_from", "expected_interval_seconds", "mappings"},
        {"station_code", "business_site_id", "rtu_asset_code", "instrument_asset_code", "timezone",
         "effective_from", "effective_to", "expected_granularity", "expected_interval_seconds", "mappings"},
        "configuration",
    )
    # Reuse the established validation exactly, via its existing in-memory logic.
    # The following local parser mirrors the post-JSON portion of load_configuration.
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
    interval = _positive_integer(document["expected_interval_seconds"], "expected_interval_seconds")
    mappings = document["mappings"]
    if not isinstance(mappings, list) or not mappings:
        raise ProvisionError("at least one factor mapping is required")
    normalized_mappings = []
    for item in mappings:
        if not isinstance(item, dict):
            raise ProvisionError("factor mapping is invalid")
        _require_keys(item, {"protocol_code"}, {"protocol_code", "business_metric", "instrument_asset_code", "expected_interval_seconds", "tolerance_seconds", "effective_from", "effective_to"}, "factor mapping")
        code = _optional_text(item["protocol_code"], "protocol_code")
        if code is None or not code.isascii() or len(code) > 128:
            raise ProvisionError("protocol_code is invalid")
        mapping_from = _utc_time(item.get("effective_from", effective_from), "mapping effective_from")
        mapping_to = _utc_time(item.get("effective_to", effective_to), "mapping effective_to", nullable=True)
        if mapping_to is not None and mapping_to <= mapping_from:
            raise ProvisionError("mapping effective period is invalid")
        if mapping_from < effective_from or (effective_to is not None and (mapping_to is None or mapping_to > effective_to)):
            raise ProvisionError("mapping period must be contained by the endpoint profile")
        normalized_mappings.append({"protocol_code": code, "business_metric": _optional_text(item.get("business_metric"), "business_metric"), "instrument_asset_code": _optional_text(item.get("instrument_asset_code", document.get("instrument_asset_code")), "instrument_asset_code"), "expected_interval_seconds": _positive_integer(item.get("expected_interval_seconds", interval), "mapping expected_interval_seconds"), "tolerance_seconds": _nonnegative_integer(item.get("tolerance_seconds", 0), "tolerance_seconds"), "effective_from": mapping_from, "effective_to": mapping_to})
    return {"station_code": station_code, "business_site_id": site_id, "rtu_asset_code": _optional_text(document.get("rtu_asset_code"), "rtu_asset_code"), "instrument_asset_code": _optional_text(document.get("instrument_asset_code"), "instrument_asset_code"), "timezone": timezone_name, "effective_from": effective_from, "effective_to": effective_to, "expected_granularity": _optional_text(document.get("expected_granularity", "realtime"), "expected_granularity"), "expected_interval_seconds": interval, "mappings": sorted(normalized_mappings, key=lambda item: (item["protocol_code"], item["effective_from"], item["effective_to"] or ""))}


def _materialize_station_code_template(template: dict[str, Any], item: dict[str, object]) -> dict[str, Any]:
    configuration = dict(template)
    configuration["station_code"] = str(item["station_code"])
    configuration["business_site_id"] = int(item["business_site_id"])
    configuration["mappings"] = [dict(mapping) for mapping in template["mappings"]]
    return configuration


def apply_station_code_import(
    database: Path,
    workbook: Path,
    *,
    credential: bytes,
    offline_confirmed: bool,
    template: dict[str, Any] | None = None,
    expected_fingerprint: str | None = None,
    expected_accepted_rows: int | None = None,
    expected_ignored_rows: int | None = None,
) -> dict[str, int]:
    if not offline_confirmed:
        raise ProvisionError("offline confirmation is required before writing configuration")
    if not credential or len(credential) > 128 or not credential.isascii():
        raise ProvisionError("HJ212 credential must be non-empty ASCII text")
    if template is None:
        raise ProvisionError("station-code import requires a profile and factor-mapping template")
    if not expected_fingerprint or expected_accepted_rows != FORMAL_STATION_CODE_ROWS or expected_ignored_rows != FORMAL_STATION_CODE_IGNORED_ROWS:
        raise ProvisionError("station-code import requires the formal preview fingerprint and 43+1 expected counts")
    database = _require_existing_database(database)
    records, ignored_rows = load_station_code_workbook(workbook)
    with closing(_connect(database)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            preview = _station_code_preview_from_connection(connection, records, ignored_rows)
            if preview["errors"]:
                raise ProvisionError("station-code import validation failed")
            summary = _station_code_import_summary(preview, template)
            if summary["fingerprint"] != expected_fingerprint:
                raise ProvisionError("station-code workbook or template changed after preview")
            for item in preview["planned"]:
                configuration = _materialize_station_code_template(template, item)
                _validate_configuration_for_database(connection, configuration)
                _apply(connection, configuration, credential)
            for endpoint_id in preview["disable_endpoint_ids"]:
                connection.execute("UPDATE trusted_endpoints SET enabled=0,endpoint_state='disabled',updated_at=? WHERE id=?",
                                   (datetime.now(timezone.utc).replace(microsecond=0).isoformat(), endpoint_id))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {"accepted_rows": int(preview["accepted_rows"]), "ignored_rows": int(preview["ignored_rows"]),
            "disabled_endpoints": len(preview["disable_endpoint_ids"])}


def verify_station_code_import(
    database: Path, workbook: Path, *, template: dict[str, Any], credential: bytes,
    expected_fingerprint: str, expected_accepted_rows: int, expected_ignored_rows: int,
) -> dict[str, object]:
    database = _require_existing_database(database)
    records, ignored_rows = load_station_code_workbook(workbook)
    with closing(_connect(database)) as connection:
        preview = _station_code_preview_from_connection(connection, records, ignored_rows)
        if preview["errors"]:
            raise ProvisionError("station-code import validation failed")
        summary = _station_code_import_summary(preview, template)
        if (expected_accepted_rows != FORMAL_STATION_CODE_ROWS or expected_ignored_rows != FORMAL_STATION_CODE_IGNORED_ROWS
                or summary["fingerprint"] != expected_fingerprint):
            raise ProvisionError("station-code verification does not match the formal preview")
        expected_credential_hmac = credential_hmac(credential, _credential_pepper())
        for item in preview["planned"]:
            configuration = _materialize_station_code_template(template, item)
            _validate_configuration_for_database(connection, configuration)
            endpoint = _check_existing_configuration(connection, configuration)
            if endpoint is None or not endpoint["enabled"] or endpoint["endpoint_state"] == "disabled":
                raise ProvisionError("station-code endpoint is unavailable")
            if endpoint["credential_hmac"] != expected_credential_hmac:
                raise ProvisionError("station-code endpoint credential summary differs from the private input")
            profile = connection.execute(
                "SELECT 1 FROM monitoring_endpoint_profiles WHERE endpoint_id=? AND effective_from=?",
                (endpoint["id"], configuration["effective_from"]),
            ).fetchone()
            if profile is None:
                raise ProvisionError("station-code endpoint profile is unavailable")
            for mapping in configuration["mappings"]:
                if not connection.execute(
                    "SELECT 1 FROM monitoring_factor_mappings WHERE endpoint_id=? AND protocol_code=? AND effective_from=? AND enabled=1",
                    (endpoint["id"], mapping["protocol_code"], mapping["effective_from"]),
                ).fetchone():
                    raise ProvisionError("station-code endpoint factor mapping is unavailable")
    return summary


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


def load_b2_factor_matrix(path: Path) -> dict[str, Any]:
    """Load only the approved identity/factor facts needed by the B2 offline tool."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvisionError("B2 factor matrix cannot be read") from exc
    if not isinstance(document, dict) or not isinstance(document.get("approved_identities"), list):
        raise ProvisionError("B2 factor matrix fields are invalid")
    for flag in ("contains_raw_payloads", "contains_observed_values", "contains_credentials"):
        if document.get(flag) is not False:
            raise ProvisionError("B2 factor matrix must not contain private or observed values")
    identities = []
    station_codes = set()
    for index, item in enumerate(document["approved_identities"]):
        if not isinstance(item, dict):
            raise ProvisionError("B2 factor matrix identity is invalid")
        station_code = item.get("station_code")
        binding_status = item.get("binding_status")
        system_name = item.get("system_name")
        original_name = item.get("original_name")
        factors = item.get("observed_hj212_factor_codes")
        if (not isinstance(station_code, str) or not station_code.strip() or len(station_code) > 128
                or not station_code.isascii() or binding_status not in {"bound", "unbound"}
                or not isinstance(system_name, str) or not system_name.strip()
                or not isinstance(original_name, str) or not original_name.strip()
                or not isinstance(factors, list)
                or any(not isinstance(code, str) or not code.startswith("HJ212:") or len(code) > 128 for code in factors)):
            raise ProvisionError("B2 factor matrix identity fields are invalid")
        station_code = station_code.strip()
        if station_code in station_codes:
            raise ProvisionError("B2 factor matrix contains duplicate station identities")
        station_codes.add(station_code)
        identities.append({
            "source_index": index,
            "station_code": station_code,
            "binding_status": binding_status,
            "system_name": system_name.strip(),
            "original_name": original_name.strip(),
            "factors": sorted(set(factors)),
        })
    canonical = {"report_type": document.get("report_type"), "identities": identities}
    encoded = json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"identities": identities, "source_fingerprint": "sha256:" + hashlib.sha256(encoded).hexdigest()[:16]}


def _b2_effective_boundary(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ProvisionError("B2 planning time must include timezone")
    local = current.astimezone(ZoneInfo(B2_TIMEZONE))
    seconds = local.hour * 3600 + local.minute * 60 + local.second
    if local.microsecond:
        seconds += 1
    boundary_seconds = ((seconds + B2_INTERVAL_SECONDS - 1) // B2_INTERVAL_SECONDS) * B2_INTERVAL_SECONDS
    boundary_day = local.date()
    if boundary_seconds >= 86400:
        boundary_day += timedelta(days=1)
        boundary_seconds = 0
    boundary = datetime.combine(boundary_day, datetime.min.time(), local.tzinfo) + timedelta(seconds=boundary_seconds)
    return boundary.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _b2_matches(row: sqlite3.Row, expected: dict[str, Any], keys: tuple[str, ...]) -> bool:
    return all(row[key] == expected[key] for key in keys)


def _b2_period_action(
    rows: list[sqlite3.Row], effective_from: str, expected: dict[str, Any], keys: tuple[str, ...],
    kind: str, hard_errors: list[dict[str, Any]], context: dict[str, Any], operations: list[dict[str, Any]],
) -> str:
    exact = [row for row in rows if row["effective_from"] == effective_from]
    active = [row for row in rows if row["enabled"] and row["effective_from"] <= effective_from
              and (row["effective_to"] is None or row["effective_to"] > effective_from)]
    future = [row for row in rows if row["enabled"] and row["effective_from"] > effective_from]
    if len(exact) > 1 or len(active) > 1:
        hard_errors.append(dict(context, category=f"ambiguous_{kind}_period"))
        return "conflict"
    if exact:
        row = exact[0]
        if row["enabled"] and _b2_matches(row, expected, keys):
            return "reuse"
        hard_errors.append(dict(context, category=f"{kind}_boundary_conflict"))
        return "conflict"
    if future:
        hard_errors.append(dict(context, category=f"{kind}_future_period_conflict"))
        return "conflict"
    if active:
        row = active[0]
        if _b2_matches(row, expected, keys):
            return "reuse"
        operations.append({"kind": kind, "action": "replace", "close_id": int(row["id"]), "values": expected})
        return "replace"
    operations.append({"kind": kind, "action": "create", "close_id": None, "values": expected})
    return "create"


def _b2_plan_from_connection(
    connection: sqlite3.Connection, matrix: dict[str, Any], effective_from: str,
    identity_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    verify_station_monitoring_schema(connection)
    resolutions_by_station_code = {
        resolution["station_code"]: resolution
        for resolution in (identity_manifest or {}).get("resolutions", [])
    }
    definitions = {
        row["protocol_code"]: row for row in connection.execute(
            "SELECT protocol_code,business_metric,is_published FROM monitoring_factor_definitions"
        )
    }
    operations: list[dict[str, Any]] = []
    endpoints = []
    soft_differences: list[dict[str, Any]] = []
    hard_errors: list[dict[str, Any]] = []
    for identity in matrix["identities"]:
        source_context = {"source_index": identity["source_index"]}
        if identity["binding_status"] != "bound":
            soft_differences.append(dict(source_context, category="unbound_identity"))
            continue
        if not identity["factors"]:
            soft_differences.append(dict(source_context, category="no_observed_factors"))
            continue
        endpoint = connection.execute(
            """SELECT endpoint.*,site.name AS business_site_name FROM trusted_endpoints endpoint
               LEFT JOIN sites site ON site.id=endpoint.business_site_id WHERE endpoint.station_code=?""",
            (identity["station_code"],),
        ).fetchone()
        if (endpoint is None or not endpoint["enabled"] or endpoint["endpoint_state"] != "bound"
                or endpoint["business_site_id"] is None or endpoint["business_site_name"] is None):
            hard_errors.append(dict(source_context, category="bound_identity_unavailable"))
            continue
        context = {"endpoint_id": int(endpoint["id"]), "business_site_id": int(endpoint["business_site_id"])}
        identity_names = {identity["system_name"], identity["original_name"]}
        resolution = resolutions_by_station_code.get(identity["station_code"])
        if resolution is None:
            name_matches = str(endpoint["business_site_name"]).strip() in identity_names
        else:
            canonical_sites = connection.execute(
                "SELECT id FROM sites WHERE name=?", (resolution["canonical_name"],)
            ).fetchall()
            approved_names = {resolution["canonical_name"], *resolution["aliases"]}
            name_matches = (
                len(canonical_sites) == 1
                and int(canonical_sites[0]["id"]) == int(endpoint["business_site_id"])
                and identity_names.issubset(approved_names)
            )
        if not name_matches:
            hard_errors.append(dict(context, category="business_site_binding_mismatch"))
            continue
        credential_summary = str(endpoint["credential_hmac"] or "")
        if len(credential_summary) != 64 or any(
            character not in "0123456789abcdef" for character in credential_summary.lower()
        ):
            hard_errors.append(dict(context, category="invalid_credential_summary"))
            continue
        factors = []
        for code in identity["factors"]:
            definition = definitions.get(code)
            if definition is None or not definition["is_published"] or not definition["business_metric"]:
                soft_differences.append(dict(context, category="unknown_or_unpublished_factor", protocol_code=code))
            else:
                factors.append((code, str(definition["business_metric"])))
        if not factors:
            soft_differences.append(dict(context, category="only_unknown_factors"))
            continue

        profile_rows = list(connection.execute(
            "SELECT * FROM monitoring_endpoint_profiles WHERE endpoint_id=? ORDER BY effective_from,id", (endpoint["id"],)
        ))
        active_profile = next((row for row in profile_rows if row["enabled"] and row["effective_from"] <= effective_from
                               and (row["effective_to"] is None or row["effective_to"] > effective_from)), None)
        if active_profile is not None and active_profile["business_site_id"] != endpoint["business_site_id"]:
            hard_errors.append(dict(context, category="profile_business_site_mismatch"))
            continue
        profile_expected = {
            "endpoint_id": int(endpoint["id"]), "business_site_id": int(endpoint["business_site_id"]),
            "rtu_asset_code": active_profile["rtu_asset_code"] if active_profile else None,
            "instrument_asset_code": active_profile["instrument_asset_code"] if active_profile else None,
            "timezone": B2_TIMEZONE, "enabled": 1, "expected_granularity": "realtime",
            "expected_interval_seconds": B2_INTERVAL_SECONDS, "effective_from": effective_from, "effective_to": None,
        }
        profile_action = _b2_period_action(
            profile_rows, effective_from, profile_expected,
            ("business_site_id", "rtu_asset_code", "instrument_asset_code", "timezone", "enabled",
             "expected_granularity", "expected_interval_seconds", "effective_to"),
            "profile", hard_errors, context, operations,
        )
        factor_plans = []
        asset_mappings = []
        for code, business_metric in factors:
            factor_context = dict(context, protocol_code=code)
            mapping_rows = list(connection.execute(
                """SELECT * FROM monitoring_factor_mappings
                   WHERE endpoint_id=? AND protocol_code=? ORDER BY effective_from,id""", (endpoint["id"], code)
            ))
            active_mapping = next((row for row in mapping_rows if row["enabled"] and row["effective_from"] <= effective_from
                                   and (row["effective_to"] is None or row["effective_to"] > effective_from)), None)
            mapping_expected = {
                "endpoint_id": int(endpoint["id"]), "protocol_code": code, "business_metric": business_metric,
                "instrument_asset_code": (active_mapping["instrument_asset_code"] if active_mapping
                                          else profile_expected["instrument_asset_code"]),
                "expected_interval_seconds": B2_INTERVAL_SECONDS, "tolerance_seconds": B2_TOLERANCE_SECONDS,
                "effective_from": effective_from, "effective_to": None, "enabled": 1,
            }
            mapping_action = _b2_period_action(
                mapping_rows, effective_from, mapping_expected,
                ("protocol_code", "business_metric", "instrument_asset_code", "expected_interval_seconds",
                 "tolerance_seconds", "effective_to", "enabled"),
                "mapping", hard_errors, factor_context, operations,
            )
            asset_mappings.append(mapping_expected)
            schedule_rows = list(connection.execute(
                """SELECT * FROM monitoring_business_schedules
                   WHERE endpoint_id=? AND protocol_code=? ORDER BY effective_from,id""", (endpoint["id"], code)
            ))
            schedule_expected = {
                "endpoint_id": int(endpoint["id"]), "protocol_code": code, "timezone": B2_TIMEZONE,
                "interval_seconds": B2_INTERVAL_SECONDS, "anchor_local_time": B2_ANCHOR_LOCAL_TIME,
                "tolerance_seconds": B2_TOLERANCE_SECONDS,
                "result_delay_seconds": B2_RESULT_DELAY_SECONDS, "effective_from": effective_from,
                "effective_to": None, "enabled": 1,
            }
            schedule_action = _b2_period_action(
                schedule_rows, effective_from, schedule_expected,
                ("protocol_code", "timezone", "interval_seconds", "anchor_local_time", "tolerance_seconds",
                 "result_delay_seconds", "effective_to", "enabled"),
                "schedule", hard_errors, factor_context, operations,
            )
            factor_plans.append({"protocol_code": code, "mapping": mapping_action, "schedule": schedule_action})
        try:
            _verify_assets_when_available(connection, {
                "business_site_id": int(endpoint["business_site_id"]),
                "rtu_asset_code": profile_expected["rtu_asset_code"],
                "instrument_asset_code": profile_expected["instrument_asset_code"],
                "mappings": asset_mappings,
            })
        except ProvisionError:
            hard_errors.append(dict(context, category="asset_business_site_mismatch"))
        endpoints.append(dict(context, profile=profile_action, factors=factor_plans))

    fingerprint_value = {
        "source_fingerprint": matrix["source_fingerprint"], "effective_from": effective_from,
        "identity_resolution_source_fingerprint": (
            identity_manifest["source_fingerprint"] if identity_manifest else None
        ),
        "constants": [B2_INTERVAL_SECONDS, B2_TIMEZONE, B2_ANCHOR_LOCAL_TIME,
                      B2_TOLERANCE_SECONDS, B2_RESULT_DELAY_SECONDS],
        "endpoints": endpoints, "soft_differences": soft_differences, "hard_errors": hard_errors,
        "operations": operations,
    }
    encoded = json.dumps(fingerprint_value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "action": "b2-batch-plan", "result": "ready" if not hard_errors else "conflicted",
        "source_fingerprint": matrix["source_fingerprint"], "fingerprint": "sha256:" + hashlib.sha256(encoded).hexdigest()[:16],
        "effective_from": effective_from, "eligible_endpoint_count": len(endpoints),
        "eligible_factor_count": sum(len(item["factors"]) for item in endpoints),
        "soft_difference_count": len(soft_differences), "soft_differences": soft_differences,
        "hard_error_count": len(hard_errors), "hard_errors": hard_errors, "endpoints": endpoints,
        "_operations": operations,
    }


def _b2_public(plan: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in plan.items() if not key.startswith("_")}


def preview_b2_batch(
    database: Path, matrix_path: Path, *, identity_resolution_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    matrix = load_b2_factor_matrix(matrix_path)
    identity_manifest = (
        load_b2_identity_resolutions(identity_resolution_path) if identity_resolution_path else None
    )
    with closing(_connect(_require_existing_database(Path(database)))) as connection:
        return _b2_public(_b2_plan_from_connection(
            connection, matrix, _b2_effective_boundary(now), identity_manifest,
        ))


def _apply_b2_operations(connection: sqlite3.Connection, operations: list[dict[str, Any]], effective_from: str) -> None:
    tables = {"profile": "monitoring_endpoint_profiles", "mapping": "monitoring_factor_mappings",
              "schedule": "monitoring_business_schedules"}
    for operation in operations:
        if operation["close_id"] is not None:
            cursor = connection.execute(
                f"UPDATE {tables[operation['kind']]} SET effective_to=? WHERE id=? AND enabled=1 AND (effective_to IS NULL OR effective_to>?)",
                (effective_from, operation["close_id"], effective_from),
            )
            if cursor.rowcount != 1:
                raise ProvisionError("B2 configuration changed while applying")
        values = operation["values"]
        if operation["kind"] == "profile":
            connection.execute(
                """INSERT INTO monitoring_endpoint_profiles(endpoint_id,business_site_id,rtu_asset_code,instrument_asset_code,
                   timezone,enabled,expected_granularity,expected_interval_seconds,effective_from,effective_to)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                tuple(values[key] for key in ("endpoint_id", "business_site_id", "rtu_asset_code", "instrument_asset_code",
                      "timezone", "enabled", "expected_granularity", "expected_interval_seconds", "effective_from", "effective_to")),
            )
        elif operation["kind"] == "mapping":
            connection.execute(
                """INSERT INTO monitoring_factor_mappings(endpoint_id,protocol_code,business_metric,instrument_asset_code,
                   expected_interval_seconds,tolerance_seconds,effective_from,effective_to,enabled)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                tuple(values[key] for key in ("endpoint_id", "protocol_code", "business_metric", "instrument_asset_code",
                      "expected_interval_seconds", "tolerance_seconds", "effective_from", "effective_to", "enabled")),
            )
        else:
            connection.execute(
                """INSERT INTO monitoring_business_schedules(endpoint_id,protocol_code,timezone,interval_seconds,
                   anchor_local_time,tolerance_seconds,result_delay_seconds,effective_from,effective_to,enabled)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                tuple(values[key] for key in ("endpoint_id", "protocol_code", "timezone", "interval_seconds",
                      "anchor_local_time", "tolerance_seconds", "result_delay_seconds",
                      "effective_from", "effective_to", "enabled")),
            )


def apply_b2_batch(
    database: Path, matrix_path: Path, *, expected_fingerprint: str, effective_from: str,
    offline_confirmed: bool, identity_resolution_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not offline_confirmed or not expected_fingerprint:
        raise ProvisionError("B2 batch apply requires offline confirmation and preview fingerprint")
    locked_boundary = _utc_time(effective_from, "B2 effective_from")
    current_value = now or datetime.now(timezone.utc)
    if current_value.tzinfo is None:
        raise ProvisionError("B2 apply time must include timezone")
    current = current_value.astimezone(timezone.utc).replace(microsecond=0)
    if datetime.fromisoformat(locked_boundary) < current:
        raise ProvisionError("B2 effective boundary has passed; create a new preview")
    matrix = load_b2_factor_matrix(matrix_path)
    identity_manifest = (
        load_b2_identity_resolutions(identity_resolution_path) if identity_resolution_path else None
    )
    with closing(_connect(_require_existing_database(Path(database)))) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            plan = _b2_plan_from_connection(connection, matrix, locked_boundary, identity_manifest)
            if plan["hard_error_count"]:
                raise ProvisionError("B2 batch contains hard configuration errors")
            if plan["fingerprint"] != expected_fingerprint:
                raise ProvisionError("B2 preview fingerprint or input matrix changed")
            _apply_b2_operations(connection, plan["_operations"], locked_boundary)
            verify_station_monitoring_schema(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    outcome = _b2_public(plan)
    outcome.update({"action": "b2-batch-apply", "result": "applied", "write_count": len(plan["_operations"])})
    return outcome


def verify_b2_batch(
    database: Path, matrix_path: Path, *, effective_from: str,
    identity_resolution_path: Path | None = None,
) -> dict[str, Any]:
    boundary = _utc_time(effective_from, "B2 effective_from")
    matrix = load_b2_factor_matrix(matrix_path)
    identity_manifest = (
        load_b2_identity_resolutions(identity_resolution_path) if identity_resolution_path else None
    )
    with closing(_connect(_require_existing_database(Path(database)))) as connection:
        plan = _b2_plan_from_connection(connection, matrix, boundary, identity_manifest)
        errors = list(plan["hard_errors"])
        errors.extend({
            "endpoint_id": operation["values"]["endpoint_id"],
            **({"protocol_code": operation["values"]["protocol_code"]}
               if operation["kind"] != "profile" else {}),
            "category": f"{operation['kind']}_configuration_not_current",
        } for operation in plan["_operations"])
        profile_count = mapping_count = schedule_count = 0
        for endpoint in plan["endpoints"]:
            endpoint_id = endpoint["endpoint_id"]
            profiles = connection.execute(
                """SELECT * FROM monitoring_endpoint_profiles WHERE endpoint_id=? AND enabled=1 AND effective_from<=?
                   AND (effective_to IS NULL OR effective_to>?) ORDER BY effective_from DESC""",
                (endpoint_id, boundary, boundary),
            ).fetchall()
            if len(profiles) != 1 or profiles[0]["business_site_id"] != endpoint["business_site_id"] \
                    or profiles[0]["timezone"] != B2_TIMEZONE \
                    or profiles[0]["expected_interval_seconds"] != B2_INTERVAL_SECONDS:
                errors.append({"endpoint_id": endpoint_id, "category": "profile_verification_failed"})
            else:
                profile_count += 1
            for factor in endpoint["factors"]:
                code = factor["protocol_code"]
                mappings = connection.execute(
                    """SELECT * FROM monitoring_factor_mappings WHERE endpoint_id=? AND protocol_code=? AND enabled=1
                       AND effective_from<=? AND (effective_to IS NULL OR effective_to>?) ORDER BY effective_from DESC""",
                    (endpoint_id, code, boundary, boundary),
                ).fetchall()
                if len(mappings) != 1 or mappings[0]["expected_interval_seconds"] != B2_INTERVAL_SECONDS \
                        or mappings[0]["tolerance_seconds"] != B2_TOLERANCE_SECONDS:
                    errors.append({"endpoint_id": endpoint_id, "protocol_code": code,
                                   "category": "mapping_verification_failed"})
                else:
                    mapping_count += 1
                schedules = connection.execute(
                    """SELECT * FROM monitoring_business_schedules WHERE endpoint_id=?
                       AND (protocol_code=? OR protocol_code IS NULL) AND enabled=1
                       AND effective_from<=? AND (effective_to IS NULL OR effective_to>?)
                       ORDER BY CASE WHEN protocol_code=? THEN 0 ELSE 1 END,effective_from DESC""",
                    (endpoint_id, code, boundary, boundary, code),
                ).fetchall()
                if not schedules or schedules[0]["protocol_code"] != code \
                        or schedules[0]["interval_seconds"] != B2_INTERVAL_SECONDS \
                        or schedules[0]["tolerance_seconds"] != B2_TOLERANCE_SECONDS \
                        or schedules[0]["result_delay_seconds"] != B2_RESULT_DELAY_SECONDS \
                        or schedules[0]["timezone"] != B2_TIMEZONE \
                        or schedules[0]["anchor_local_time"] != B2_ANCHOR_LOCAL_TIME:
                    errors.append({"endpoint_id": endpoint_id, "protocol_code": code,
                                   "category": "schedule_verification_failed"})
                else:
                    schedule_count += 1
            if connection.execute(
                """SELECT 1 FROM monitoring_endpoint_profiles
                   WHERE endpoint_id=? AND effective_from=? AND expected_interval_seconds=3600 LIMIT 1""",
                (endpoint_id, boundary),
            ).fetchone():
                errors.append({"endpoint_id": endpoint_id, "category": "one_hour_configuration_detected",
                               "table": "monitoring_endpoint_profiles"})
            for factor in endpoint["factors"]:
                code = factor["protocol_code"]
                for table, interval_column in (("monitoring_factor_mappings", "expected_interval_seconds"),
                                               ("monitoring_business_schedules", "interval_seconds")):
                    if connection.execute(
                        f"""SELECT 1 FROM {table} WHERE endpoint_id=? AND protocol_code=?
                            AND effective_from=? AND {interval_column}=3600 LIMIT 1""",
                        (endpoint_id, code, boundary),
                    ).fetchone():
                        errors.append({"endpoint_id": endpoint_id, "protocol_code": code,
                                       "category": "one_hour_configuration_detected", "table": table})
    return {
        "action": "b2-batch-verify", "result": "verified" if not errors else "conflicted",
        "effective_from": boundary, "eligible_endpoint_count": plan["eligible_endpoint_count"],
        "eligible_factor_count": plan["eligible_factor_count"], "profile_count": profile_count,
        "mapping_count": mapping_count, "schedule_count": schedule_count,
        "soft_difference_count": plan["soft_difference_count"], "soft_differences": plan["soft_differences"],
        "hard_error_count": len(errors), "hard_errors": errors,
    }


def load_b2_identity_resolutions(path: Path) -> dict[str, Any]:
    """Load product-approved aliases without accepting credentials or observed data."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvisionError("B2 identity resolution file cannot be read") from exc
    if (not isinstance(document, dict) or document.get("contains_credentials") is not False
            or not isinstance(document.get("approved_resolutions"), list)):
        raise ProvisionError("B2 identity resolution fields are invalid")
    resolutions = []
    station_codes = set()
    canonical_names = set()
    for index, item in enumerate(document["approved_resolutions"]):
        if not isinstance(item, dict) or set(item) != {
                "canonical_system_name", "station_code", "approved_aliases"}:
            raise ProvisionError("B2 identity resolution item is invalid")
        canonical_name = item["canonical_system_name"]
        station_code = item["station_code"]
        aliases = item["approved_aliases"]
        if (not isinstance(canonical_name, str) or not canonical_name.strip()
                or not isinstance(station_code, str) or not station_code.strip()
                or len(station_code) > 128 or not station_code.isascii()
                or not isinstance(aliases, list)
                or any(not isinstance(alias, str) or not alias.strip() for alias in aliases)):
            raise ProvisionError("B2 identity resolution item is invalid")
        canonical_name = canonical_name.strip()
        station_code = station_code.strip()
        aliases = sorted({alias.strip() for alias in aliases})
        if canonical_name in aliases or station_code in station_codes or canonical_name in canonical_names:
            raise ProvisionError("B2 identity resolutions contain duplicate targets")
        station_codes.add(station_code)
        canonical_names.add(canonical_name)
        resolutions.append({
            "source_index": index, "canonical_name": canonical_name,
            "station_code": station_code, "aliases": aliases,
        })
    canonical = {"report_type": document.get("report_type"), "resolutions": resolutions}
    encoded = json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "resolutions": resolutions,
        "source_fingerprint": "sha256:" + hashlib.sha256(encoded).hexdigest()[:16],
    }


def _b2_identity_resolution_plan(connection: sqlite3.Connection, manifest: dict[str, Any]) -> dict[str, Any]:
    verify_station_monitoring_schema(connection)
    sites_by_name: dict[str, list[sqlite3.Row]] = {}
    for row in connection.execute("SELECT id,name FROM sites"):
        sites_by_name.setdefault(str(row["name"]).strip(), []).append(row)
    operations = []
    errors = []
    for resolution in manifest["resolutions"]:
        context = {"source_index": resolution["source_index"]}
        sites = sites_by_name.get(resolution["canonical_name"], [])
        if len(sites) != 1:
            errors.append(dict(context, category="canonical_site_unavailable"))
            continue
        site_id = int(sites[0]["id"])
        endpoint = connection.execute(
            "SELECT * FROM trusted_endpoints WHERE station_code=?", (resolution["station_code"],)
        ).fetchone()
        if endpoint is None:
            action = "create_bind"
            endpoint_id = None
        elif not endpoint["enabled"] or endpoint["endpoint_state"] == "disabled":
            errors.append(dict(context, category="approved_identity_disabled"))
            continue
        elif endpoint["business_site_id"] is None and endpoint["endpoint_state"] == "unbound":
            action = "bind"
            endpoint_id = int(endpoint["id"])
        elif endpoint["business_site_id"] == site_id and endpoint["endpoint_state"] == "bound":
            action = "reuse"
            endpoint_id = int(endpoint["id"])
        else:
            errors.append(dict(context, category="approved_identity_bound_elsewhere"))
            continue
        superseded = list(connection.execute(
            """SELECT id FROM trusted_endpoints
               WHERE business_site_id=? AND station_code<>? AND enabled=1 AND endpoint_state='bound'""",
            (site_id, resolution["station_code"]),
        ))
        if len(superseded) > 1:
            errors.append(dict(context, category="ambiguous_superseded_identity"))
            continue
        operations.append({
            "source_index": resolution["source_index"], "site_id": site_id,
            "station_code": resolution["station_code"], "endpoint_id": endpoint_id,
            "action": action, "retire_endpoint_ids": [int(row["id"]) for row in superseded],
        })
    locked = {
        "source_fingerprint": manifest["source_fingerprint"], "operations": operations, "errors": errors,
    }
    encoded = json.dumps(locked, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "action": "b2-identities-plan", "result": "ready" if not errors else "conflicted",
        "source_fingerprint": manifest["source_fingerprint"],
        "fingerprint": "sha256:" + hashlib.sha256(encoded).hexdigest()[:16],
        "resolution_count": len(manifest["resolutions"]), "planned_count": len(operations),
        "create_count": sum(item["action"] == "create_bind" for item in operations),
        "bind_count": sum(item["action"] == "bind" for item in operations),
        "reuse_count": sum(item["action"] == "reuse" for item in operations),
        "retire_count": sum(len(item["retire_endpoint_ids"]) for item in operations),
        "hard_error_count": len(errors), "hard_errors": errors, "_operations": operations,
    }


def _b2_identity_public(plan: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in plan.items() if not key.startswith("_")}


def preview_b2_identity_resolutions(database: Path, resolution_path: Path) -> dict[str, Any]:
    manifest = load_b2_identity_resolutions(resolution_path)
    with closing(_connect(_require_existing_database(Path(database)))) as connection:
        return _b2_identity_public(_b2_identity_resolution_plan(connection, manifest))


def apply_b2_identity_resolutions(
    database: Path, resolution_path: Path, *, credential: bytes, expected_fingerprint: str,
    offline_confirmed: bool, retire_confirmed: bool,
) -> dict[str, Any]:
    if not offline_confirmed or not expected_fingerprint:
        raise ProvisionError("B2 identity apply requires offline confirmation and preview fingerprint")
    if not credential or len(credential) > 128 or not credential.isascii():
        raise ProvisionError("HJ212 credential must be non-empty ASCII text")
    manifest = load_b2_identity_resolutions(resolution_path)
    expected_hmac = credential_hmac(credential, _credential_pepper())
    with closing(_connect(_require_existing_database(Path(database)))) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            plan = _b2_identity_resolution_plan(connection, manifest)
            if plan["hard_error_count"]:
                raise ProvisionError("B2 identity resolutions contain hard errors")
            if plan["fingerprint"] != expected_fingerprint:
                raise ProvisionError("B2 identity resolutions changed after preview")
            if plan["retire_count"] and not retire_confirmed:
                raise ProvisionError("explicit confirmation is required before retiring superseded identities")
            writes = 0
            now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            for operation in plan["_operations"]:
                endpoint = (connection.execute("SELECT * FROM trusted_endpoints WHERE id=?", (operation["endpoint_id"],)).fetchone()
                            if operation["endpoint_id"] is not None else None)
                if endpoint is not None and endpoint["credential_hmac"] != expected_hmac:
                    raise ProvisionError("approved identity credential differs from the private input")
                for endpoint_id in operation["retire_endpoint_ids"]:
                    old = connection.execute("SELECT credential_hmac FROM trusted_endpoints WHERE id=?", (endpoint_id,)).fetchone()
                    if old is None or old["credential_hmac"] != expected_hmac:
                        raise ProvisionError("superseded identity credential differs from the private input")
                    connection.execute(
                        "UPDATE trusted_endpoints SET enabled=0,endpoint_state='disabled',updated_at=? WHERE id=?",
                        (now, endpoint_id),
                    )
                    writes += 1
                if operation["action"] == "create_bind":
                    connection.execute(
                        """INSERT INTO trusted_endpoints(
                               station_code,credential_hmac,business_site_id,enabled,endpoint_state,updated_at)
                           VALUES (?,?,?,1,'bound',?)""",
                        (operation["station_code"], expected_hmac, operation["site_id"], now),
                    )
                    writes += 1
                elif operation["action"] == "bind":
                    connection.execute(
                        """UPDATE trusted_endpoints SET business_site_id=?,endpoint_state='bound',updated_at=?
                           WHERE id=? AND enabled=1 AND endpoint_state='unbound' AND business_site_id IS NULL""",
                        (operation["site_id"], now, operation["endpoint_id"]),
                    )
                    if connection.execute("SELECT changes()").fetchone()[0] != 1:
                        raise ProvisionError("B2 identity changed while applying")
                    writes += 1
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    outcome = _b2_identity_public(plan)
    outcome.update({"action": "b2-identities-apply", "result": "applied", "write_count": writes})
    return outcome


def verify_b2_identity_resolutions(
    database: Path, resolution_path: Path, *, credential: bytes,
) -> dict[str, Any]:
    if not credential or len(credential) > 128 or not credential.isascii():
        raise ProvisionError("HJ212 credential must be non-empty ASCII text")
    manifest = load_b2_identity_resolutions(resolution_path)
    expected_hmac = credential_hmac(credential, _credential_pepper())
    with closing(_connect(_require_existing_database(Path(database)))) as connection:
        verified = 0
        for resolution in manifest["resolutions"]:
            sites = connection.execute(
                "SELECT id FROM sites WHERE name=?", (resolution["canonical_name"],)
            ).fetchall()
            if len(sites) != 1:
                raise ProvisionError("canonical site is unavailable during verification")
            endpoint = connection.execute(
                """SELECT * FROM trusted_endpoints
                   WHERE station_code=? AND business_site_id=? AND enabled=1 AND endpoint_state='bound'""",
                (resolution["station_code"], sites[0]["id"]),
            ).fetchone()
            if endpoint is None or endpoint["credential_hmac"] != expected_hmac:
                raise ProvisionError("approved identity is unavailable during verification")
            if connection.execute(
                """SELECT 1 FROM trusted_endpoints
                   WHERE business_site_id=? AND id<>? AND enabled=1 AND endpoint_state='bound' LIMIT 1""",
                (sites[0]["id"], endpoint["id"]),
            ).fetchone():
                raise ProvisionError("canonical site has another active identity")
            verified += 1
    return {
        "action": "b2-identities-verify", "result": "verified",
        "source_fingerprint": manifest["source_fingerprint"], "verified_count": verified,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline station-monitoring provisioning")
    parser.add_argument("action", choices=(
        "plan", "apply", "verify", "disable",
        "b2-batch-plan", "b2-batch-apply", "b2-batch-verify",
        "b2-identities-plan", "b2-identities-apply", "b2-identities-verify",
        "station-codes-plan", "station-codes-apply", "station-codes-verify",
        "station-identities-plan", "station-identities-apply", "station-identities-verify",
        "station-identities-bind-plan", "station-identities-bind-apply", "station-identities-bind-verify",
    ))
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--matrix", type=Path)
    parser.add_argument("--identity-resolutions", type=Path)
    parser.add_argument("--workbook", type=Path)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--expected-fingerprint")
    parser.add_argument("--effective-from")
    parser.add_argument("--expected-accepted-rows", type=int)
    parser.add_argument("--expected-ignored-rows", type=int)
    parser.add_argument("--target-business-site-id", type=int)
    parser.add_argument("--offline-confirmation", action="store_true")
    parser.add_argument("--credential-stdin", action="store_true")
    parser.add_argument("--confirm-disable", action="store_true")
    parser.add_argument("--reason")
    arguments = parser.parse_args(argv)
    try:
        if arguments.action.startswith("b2-identities-"):
            if (arguments.identity_resolutions is None or arguments.config is not None
                    or arguments.matrix is not None or arguments.workbook is not None
                    or arguments.template is not None):
                raise ProvisionError("B2 identity actions require --identity-resolutions only")
            if arguments.action == "b2-identities-plan":
                outcome = preview_b2_identity_resolutions(arguments.database, arguments.identity_resolutions)
            elif arguments.action == "b2-identities-apply":
                if not arguments.credential_stdin:
                    raise ProvisionError("B2 identity apply requires private credential stdin")
                outcome = apply_b2_identity_resolutions(
                    arguments.database, arguments.identity_resolutions,
                    credential=_read_hj212_credential_from_stdin(),
                    expected_fingerprint=arguments.expected_fingerprint or "",
                    offline_confirmed=arguments.offline_confirmation,
                    retire_confirmed=arguments.confirm_disable,
                )
            else:
                if not arguments.credential_stdin:
                    raise ProvisionError("B2 identity verify requires private credential stdin")
                outcome = verify_b2_identity_resolutions(
                    arguments.database, arguments.identity_resolutions,
                    credential=_read_hj212_credential_from_stdin(),
                )
        elif arguments.action.startswith("b2-batch-"):
            if (arguments.matrix is None or arguments.config is not None or arguments.workbook is not None
                    or arguments.template is not None):
                raise ProvisionError("B2 batch actions require --matrix and optionally --identity-resolutions")
            if arguments.action == "b2-batch-plan":
                outcome = preview_b2_batch(
                    arguments.database, arguments.matrix,
                    identity_resolution_path=arguments.identity_resolutions,
                )
            elif arguments.action == "b2-batch-apply":
                if not arguments.effective_from:
                    raise ProvisionError("B2 batch apply requires the preview effective boundary")
                outcome = apply_b2_batch(
                    arguments.database, arguments.matrix, expected_fingerprint=arguments.expected_fingerprint or "",
                    effective_from=arguments.effective_from, offline_confirmed=arguments.offline_confirmation,
                    identity_resolution_path=arguments.identity_resolutions,
                )
            else:
                if not arguments.effective_from:
                    raise ProvisionError("B2 batch verify requires the applied effective boundary")
                outcome = verify_b2_batch(
                    arguments.database, arguments.matrix, effective_from=arguments.effective_from,
                    identity_resolution_path=arguments.identity_resolutions,
                )
        elif arguments.action.startswith("station-identities-"):
            if (arguments.config is not None or arguments.workbook is None or arguments.template is not None
                    or arguments.identity_resolutions is not None):
                raise ProvisionError("station identity actions require --workbook without --template")
            binding_action = arguments.action.startswith("station-identities-bind-")
            if binding_action != (arguments.target_business_site_id is not None):
                raise ProvisionError("binding actions require one explicit target business site ID")
            preview = (
                preview_station_code_identity_binding(arguments.database, arguments.workbook, arguments.target_business_site_id)
                if binding_action else preview_station_code_identity_import(arguments.database, arguments.workbook)
            )
            summary = _station_code_identity_binding_summary if binding_action else _station_code_identity_summary
            if arguments.action.endswith("-plan"):
                outcome = summary(preview)
                outcome["result"] = (
                    "ready" if not outcome["conflict_categories"] and outcome["source_status"] == "ready"
                    else "conflicted" if outcome["conflict_categories"] else "not_ready"
                )
            elif arguments.action.endswith("-verify"):
                if not arguments.credential_stdin:
                    raise ProvisionError("station identity verify requires private credential stdin")
                verify_identity = verify_station_code_identity_binding if binding_action else verify_station_code_identity_import
                verify_kwargs = {"target_business_site_id": arguments.target_business_site_id} if binding_action else {}
                outcome = verify_identity(
                    arguments.database, arguments.workbook, credential=_read_hj212_credential_from_stdin(),
                    expected_fingerprint=arguments.expected_fingerprint or "",
                    expected_accepted_rows=arguments.expected_accepted_rows or 0,
                    expected_ignored_rows=arguments.expected_ignored_rows or 0, **verify_kwargs,
                )
                outcome["result"] = "verified"
            else:
                if (not arguments.offline_confirmation or not arguments.credential_stdin
                        or not arguments.expected_fingerprint):
                    raise ProvisionError("station identity apply requires offline confirmation, credential stdin, and formal preview lock")
                apply_identity = apply_station_code_identity_binding if binding_action else apply_station_code_identity_import
                if binding_action:
                    outcome = apply_identity(
                        arguments.database, arguments.workbook, credential=_read_hj212_credential_from_stdin(),
                        offline_confirmed=True, target_business_site_id=arguments.target_business_site_id,
                        retire_confirmed=arguments.confirm_disable, expected_fingerprint=arguments.expected_fingerprint,
                        expected_accepted_rows=arguments.expected_accepted_rows,
                        expected_ignored_rows=arguments.expected_ignored_rows,
                    )
                else:
                    outcome = apply_identity(
                        arguments.database, arguments.workbook, credential=_read_hj212_credential_from_stdin(),
                        offline_confirmed=True, retire_confirmed=arguments.confirm_disable,
                        expected_fingerprint=arguments.expected_fingerprint,
                        expected_accepted_rows=arguments.expected_accepted_rows,
                        expected_ignored_rows=arguments.expected_ignored_rows,
                    )
                outcome.update(summary(preview))
                outcome["result"] = "applied"
        elif arguments.action.startswith("station-codes-"):
            if (arguments.config is not None or arguments.workbook is None or arguments.template is None
                    or arguments.identity_resolutions is not None):
                raise ProvisionError("station-code actions require --workbook and --template only")
            template = load_station_code_import_template(arguments.template)
            if arguments.action == "station-codes-plan":
                outcome = _station_code_import_summary(preview_station_code_import(arguments.database, arguments.workbook), template)
                outcome["result"] = "ready" if not outcome["conflict_categories"] else "conflicted"
            elif arguments.action == "station-codes-verify":
                if not arguments.credential_stdin:
                    raise ProvisionError("station-code verify requires private credential stdin")
                outcome = verify_station_code_import(
                    arguments.database, arguments.workbook, template=template, credential=_read_hj212_credential_from_stdin(),
                    expected_fingerprint=arguments.expected_fingerprint or "",
                    expected_accepted_rows=arguments.expected_accepted_rows or 0,
                    expected_ignored_rows=arguments.expected_ignored_rows or 0,
                )
                outcome["result"] = "verified"
            else:
                if (not arguments.offline_confirmation or not arguments.credential_stdin
                        or not arguments.expected_fingerprint):
                    raise ProvisionError("station-code apply requires offline confirmation, credential stdin, and formal preview lock")
                credential = _read_hj212_credential_from_stdin()
                preview = preview_station_code_import(arguments.database, arguments.workbook)
                applied = apply_station_code_import(
                    arguments.database, arguments.workbook, credential=credential,
                    offline_confirmed=True, template=template, expected_fingerprint=arguments.expected_fingerprint,
                    expected_accepted_rows=arguments.expected_accepted_rows,
                    expected_ignored_rows=arguments.expected_ignored_rows,
                )
                outcome = _station_code_import_summary(preview, template)
                outcome.update({"result": "applied", "disabled_endpoints": applied["disabled_endpoints"]})
        else:
            if (arguments.config is None or arguments.workbook is not None or arguments.template is not None
                    or arguments.identity_resolutions is not None):
                raise ProvisionError("single-endpoint actions require --config only")
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
    if arguments.action in {"b2-batch-verify", "b2-identities-verify"} and outcome["result"] != "verified":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
