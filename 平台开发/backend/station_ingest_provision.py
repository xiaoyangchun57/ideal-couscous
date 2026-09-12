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


def _station_code_preview_from_connection(connection: sqlite3.Connection, records: list[dict[str, object]], ignored_rows: int) -> dict[str, object]:
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
            errors.append("unmatched_station" if not matched else "ambiguous_station")
            continue
        planned.append(dict(record, business_site_id=matched[0]))
    planned_by_code = {str(item["station_code"]): item for item in planned}
    for endpoint in endpoint_rows:
        item = planned_by_code.get(str(endpoint["station_code"]))
        if item is not None and endpoint["business_site_id"] != item["business_site_id"]:
            errors.append("station_code_conflict")
    disable_ids = [int(endpoint["id"]) for endpoint in endpoint_rows if endpoint["enabled"]
                   and any(endpoint["business_site_id"] == item["business_site_id"]
                           and endpoint["station_code"] != item["station_code"] for item in planned)]
    if len(planned) != FORMAL_STATION_CODE_ROWS or ignored_rows != FORMAL_STATION_CODE_IGNORED_ROWS:
        errors.append("unexpected_record_count")
    return {"accepted_rows": len(planned), "ignored_rows": ignored_rows, "errors": sorted(set(errors)),
            "planned": planned, "disable_endpoint_ids": sorted(disable_ids)}


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
            for item in sorted(preview["planned"], key=lambda item: (str(item["station_name"]), str(item["station_code"])))
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline station-monitoring provisioning")
    parser.add_argument("action", choices=("plan", "apply", "verify", "disable", "station-codes-plan", "station-codes-apply", "station-codes-verify"))
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--workbook", type=Path)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--expected-fingerprint")
    parser.add_argument("--expected-accepted-rows", type=int)
    parser.add_argument("--expected-ignored-rows", type=int)
    parser.add_argument("--offline-confirmation", action="store_true")
    parser.add_argument("--credential-stdin", action="store_true")
    parser.add_argument("--confirm-disable", action="store_true")
    parser.add_argument("--reason")
    arguments = parser.parse_args(argv)
    try:
        if arguments.action.startswith("station-codes-"):
            if arguments.config is not None or arguments.workbook is None or arguments.template is None:
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
            if arguments.config is None or arguments.workbook is not None or arguments.template is not None:
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
