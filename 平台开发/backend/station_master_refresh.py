"""Preview and apply the approved station name/MN master refresh offline."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sqlite3
import unicodedata
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

try:
    from .migrate_station_ingestion import verify_station_monitoring_schema
except ImportError:  # pragma: no cover - direct script execution
    from migrate_station_ingestion import verify_station_monitoring_schema


class StationMasterRefreshError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_name(value: object) -> str:
    return "".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


def _cell_text(cell: ElementTree.Element, shared_strings: list[str], *, opaque_mn: bool) -> str | None:
    cell_type = cell.attrib.get("t")
    if cell_type == "s":
        value = cell.findtext("{*}v")
        if value is None or not value.isdigit() or int(value) >= len(shared_strings):
            raise StationMasterRefreshError("station workbook contains an invalid shared string")
        return shared_strings[int(value)]
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//{*}t"))
    value = cell.findtext("{*}v")
    if cell_type == "str" or value is None:
        return value
    if cell_type is not None:
        raise StationMasterRefreshError("station workbook contains an unsupported cell type")
    if not opaque_mn:
        return value
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise StationMasterRefreshError("station workbook contains an invalid numeric MN") from exc
    if not number.is_finite() or number != number.to_integral_value() or number < 0:
        raise StationMasterRefreshError("station workbook numeric MN must be a non-negative integer")
    return format(number, "f").split(".", 1)[0]


def load_station_master_workbook(path: Path) -> list[dict[str, object]]:
    path = Path(path)
    try:
        with ZipFile(path) as archive:
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                shared_strings = [
                    "".join(node.text or "" for node in item.findall(".//{*}t"))
                    for item in root.findall("{*}si")
                ]
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            relation_id = next((
                sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
                for sheet in workbook.findall(".//{*}sheet") if sheet.attrib.get("name") == "Sheet1"
            ), None)
            if not relation_id:
                raise StationMasterRefreshError("station workbook must contain Sheet1")
            relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            location = next((
                item.attrib.get("Target") for item in relationships.findall("{*}Relationship")
                if item.attrib.get("Id") == relation_id
            ), None)
            if not location:
                raise StationMasterRefreshError("station workbook Sheet1 is unavailable")
            sheet_path = location.lstrip("/")
            if not sheet_path.startswith("xl/"):
                sheet_path = "xl/" + sheet_path
            sheet = ElementTree.fromstring(archive.read(sheet_path))
    except StationMasterRefreshError:
        raise
    except (OSError, KeyError, BadZipFile, ElementTree.ParseError) as exc:
        raise StationMasterRefreshError("station workbook cannot be read") from exc

    rows: list[dict[str, object]] = []
    header: tuple[str, str] | None = None
    for row in sheet.findall(".//{*}row"):
        row_number = int(row.attrib.get("r", "0"))
        values: dict[str, str | None] = {"A": None, "B": None}
        for cell in row.findall("{*}c"):
            reference = cell.attrib.get("r", "")
            column = "".join(character for character in reference if character.isalpha())
            if column in values:
                values[column] = _cell_text(cell, shared_strings, opaque_mn=column == "B")
        name = str(values["A"] or "").strip()
        mn = str(values["B"] or "").strip()
        if row_number == 1:
            header = (name, mn)
            continue
        if not name and not mn:
            continue
        if not name or not mn:
            raise StationMasterRefreshError(f"station workbook row {row_number} is incomplete")
        if len(name) > 128 or not 4 <= len(mn) <= 32 or not mn.isascii() or not mn.isalnum():
            raise StationMasterRefreshError(f"station workbook row {row_number} is invalid")
        rows.append({"row": row_number, "name": name, "mn": mn})
    if header != ("站点名称", "站码"):
        raise StationMasterRefreshError("station workbook headers must be 站点名称 and 站码")
    if not rows:
        raise StationMasterRefreshError("station workbook contains no station rows")
    duplicate_mn = sorted({item["mn"] for item in rows if sum(row["mn"] == item["mn"] for row in rows) > 1})
    if duplicate_mn:
        raise StationMasterRefreshError("station workbook contains duplicate MN values")
    return rows


def _source_fingerprint(records: list[dict[str, object]]) -> str:
    source = [{"name": item["name"], "mn": item["mn"]} for item in sorted(records, key=lambda x: str(x["mn"]))]
    encoded = json.dumps(source, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _connect(database: Path) -> sqlite3.Connection:
    database = Path(database)
    if not database.is_file():
        raise StationMasterRefreshError("target database must already exist")
    connection = sqlite3.connect(str(database), isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _require_refresh_schema(connection: sqlite3.Connection) -> None:
    verify_station_monitoring_schema(connection)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(sites)")}
    missing = {"id", "code", "name", "type", "master_status"} - columns
    if missing:
        raise StationMasterRefreshError("sites schema is missing station master fields")


def _plan_from_connection(connection: sqlite3.Connection, records: list[dict[str, object]]) -> dict[str, object]:
    _require_refresh_schema(connection)
    sites = [dict(row) for row in connection.execute(
        "SELECT id,code,name,type,master_status FROM sites ORDER BY id"
    )]
    endpoints = [dict(row) for row in connection.execute(
        "SELECT id,station_code,business_site_id,enabled,endpoint_state FROM trusted_endpoints ORDER BY id"
    )]
    aliases = [dict(row) for row in connection.execute(
        "SELECT site_id,alias_name,normalized_alias FROM site_name_aliases ORDER BY site_id,id"
    )]
    current_profiles = [dict(row) for row in connection.execute(
        """SELECT id,endpoint_id,business_site_id FROM monitoring_endpoint_profiles
           WHERE enabled=1 AND effective_to IS NULL ORDER BY endpoint_id,id"""
    )]
    site_by_id = {int(row["id"]): row for row in sites}
    ids_by_code: dict[str, set[int]] = {}
    ids_by_endpoint_mn: dict[str, set[int]] = {}
    ids_by_name: dict[str, set[int]] = {}
    ids_by_alias: dict[str, set[int]] = {}
    endpoints_by_mn: dict[str, dict[str, object]] = {}
    profile_site_ids_by_endpoint: dict[int, set[int]] = {}
    for site in sites:
        ids_by_code.setdefault(str(site["code"] or "").strip(), set()).add(int(site["id"]))
        ids_by_name.setdefault(_normalize_name(site["name"]), set()).add(int(site["id"]))
    for alias in aliases:
        ids_by_alias.setdefault(str(alias["normalized_alias"]), set()).add(int(alias["site_id"]))
    for endpoint in endpoints:
        mn = str(endpoint["station_code"])
        endpoints_by_mn[mn] = endpoint
        if endpoint["business_site_id"] is not None:
            ids_by_endpoint_mn.setdefault(mn, set()).add(int(endpoint["business_site_id"]))
    for profile in current_profiles:
        profile_site_ids_by_endpoint.setdefault(int(profile["endpoint_id"]), set()).add(
            int(profile["business_site_id"]))

    planned: list[dict[str, object]] = []
    conflicts: list[dict[str, object]] = []
    used_site_ids: dict[int, str] = {}
    for record in records:
        mn = str(record["mn"])
        normalized_name = _normalize_name(record["name"])
        mn_candidates = set(ids_by_endpoint_mn.get(mn, set())) | set(ids_by_code.get(mn, set()))
        site_id: int | None = None
        match_method = "create"
        if len(mn_candidates) > 1:
            conflicts.append({"row": record["row"], "mn": mn, "category": "mn_multiple_sites"})
        elif mn_candidates:
            site_id = next(iter(mn_candidates))
            match_method = "exact_mn"
        else:
            name_candidates = set(ids_by_name.get(normalized_name, set())) | set(ids_by_alias.get(normalized_name, set()))
            if len(name_candidates) > 1:
                conflicts.append({"row": record["row"], "mn": mn, "category": "name_ambiguous"})
            elif name_candidates:
                site_id = next(iter(name_candidates))
                match_method = "name_alias"
        if site_id is not None and site_id in used_site_ids:
            conflicts.append({"row": record["row"], "mn": mn, "category": "site_reused"})
        elif site_id is not None:
            used_site_ids[site_id] = mn
        site = site_by_id.get(site_id) if site_id is not None else None
        planned.append({
            "row": record["row"], "name": record["name"], "mn": mn,
            "site_id": site_id, "match_method": match_method,
            "old_name": site["name"] if site else None,
            "old_code": site["code"] if site else None,
        })

    selected_ids = {int(item["site_id"]) for item in planned if item["site_id"] is not None}
    retire_ids = [
        int(site["id"]) for site in sites
        if site["master_status"] == "active" and int(site["id"]) not in selected_ids
    ]
    source_mn = {str(item["mn"]) for item in records}
    endpoint_actions = []
    for item in planned:
        endpoint = endpoints_by_mn.get(str(item["mn"]))
        if not endpoint:
            continue
        existing_site_id = endpoint["business_site_id"]
        if existing_site_id is not None and item["site_id"] is not None and int(existing_site_id) != int(item["site_id"]):
            conflicts.append({"row": item["row"], "mn": item["mn"], "category": "endpoint_binding_conflict"})
            continue
        profile_site_ids = profile_site_ids_by_endpoint.get(int(endpoint["id"]), set())
        if profile_site_ids and (item["site_id"] is None or profile_site_ids != {int(item["site_id"])}):
            conflicts.append({
                "row": item["row"], "mn": item["mn"],
                "category": "endpoint_profile_binding_conflict",
            })
            continue
        if existing_site_id is None or not endpoint["enabled"] or endpoint["endpoint_state"] != "bound":
            endpoint_actions.append({"endpoint_id": int(endpoint["id"]), "mn": item["mn"]})
    disabled_endpoint_ids = [
        int(endpoint["id"]) for endpoint in endpoints
        if endpoint["business_site_id"] in retire_ids
        and (endpoint["enabled"] or endpoint["endpoint_state"] != "disabled")
        and str(endpoint["station_code"]) not in source_mn
    ]
    state = {
        "source": _source_fingerprint(records),
        "sites": sites,
        "aliases": aliases,
        "endpoints": endpoints,
        "current_profiles": current_profiles,
        "planned": [{key: item[key] for key in ("name", "mn", "site_id", "match_method", "old_name", "old_code")} for item in planned],
        "retire_ids": retire_ids,
        "endpoint_actions": endpoint_actions,
        "disabled_endpoint_ids": disabled_endpoint_ids,
        "conflicts": conflicts,
    }
    encoded = json.dumps(state, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "source_fingerprint": state["source"],
        "preview_fingerprint": "sha256:" + hashlib.sha256(encoded).hexdigest(),
        "accepted_rows": len(records),
        "exact_mn_count": sum(item["match_method"] == "exact_mn" for item in planned),
        "alias_match_count": sum(item["match_method"] == "name_alias" for item in planned),
        "created_count": sum(item["match_method"] == "create" for item in planned),
        "retired_count": len(retire_ids),
        "rebound_endpoint_count": len(endpoint_actions),
        "disabled_endpoint_count": len(disabled_endpoint_ids),
        "conflicts": conflicts,
        "planned": planned,
        "retire_ids": retire_ids,
        "endpoint_actions": endpoint_actions,
        "disabled_endpoint_ids": disabled_endpoint_ids,
    }


def _public_plan(plan: dict[str, object]) -> dict[str, object]:
    result = {key: plan[key] for key in (
        "source_fingerprint", "preview_fingerprint", "accepted_rows", "exact_mn_count",
        "alias_match_count", "created_count", "retired_count", "rebound_endpoint_count",
        "disabled_endpoint_count", "conflicts",
    )}
    result["groups"] = {
        "exact_mn": [{"name": item["name"], "mn": item["mn"], "site_id": item["site_id"]}
                     for item in plan["planned"] if item["match_method"] == "exact_mn"],
        "name_alias": [{"name": item["name"], "mn": item["mn"], "site_id": item["site_id"]}
                       for item in plan["planned"] if item["match_method"] == "name_alias"],
        "create": [{"name": item["name"], "mn": item["mn"]}
                   for item in plan["planned"] if item["match_method"] == "create"],
        "retire_site_ids": list(plan["retire_ids"]),
    }
    return result


def preview_station_master_refresh(database: Path, workbook: Path) -> dict[str, object]:
    records = load_station_master_workbook(workbook)
    with closing(_connect(database)) as connection:
        return _public_plan(_plan_from_connection(connection, records))


def _backup_database(database: Path, backup_dir: Path, source_fingerprint: str) -> Path:
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = backup_dir / f"{Path(database).stem}-station-master-{source_fingerprint[7:19]}-{stamp}.db"
    with closing(sqlite3.connect(str(database))) as source, closing(sqlite3.connect(str(target))) as destination:
        source.backup(destination)
    with closing(sqlite3.connect(str(target))) as verified:
        if verified.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            target.unlink(missing_ok=True)
            raise StationMasterRefreshError("station master backup integrity check failed")
    return target


def _apply_plan(connection: sqlite3.Connection, plan: dict[str, object]) -> None:
    now = _utc_now()
    site_by_mn: dict[str, int] = {}
    for item in plan["planned"]:
        site_id = item["site_id"]
        if site_id is None:
            site_id = connection.execute(
                "INSERT INTO sites(code,name,type,master_status) VALUES (?,?,?,'active')",
                (item["mn"], item["name"], "water_quality"),
            ).lastrowid
        else:
            if _normalize_name(item["old_name"]) != _normalize_name(item["name"]):
                connection.execute(
                    """INSERT OR IGNORE INTO site_name_aliases(site_id,alias_name,normalized_alias)
                       VALUES (?,?,?)""",
                    (site_id, item["old_name"], _normalize_name(item["old_name"])),
                )
            connection.execute(
                "UPDATE sites SET code=?,name=?,master_status='active' WHERE id=?",
                (item["mn"], item["name"], site_id),
            )
        site_by_mn[str(item["mn"])] = int(site_id)
    if plan["retire_ids"]:
        marks = ",".join("?" * len(plan["retire_ids"]))
        connection.execute(
            f"UPDATE sites SET master_status='retired' WHERE id IN ({marks})",
            list(plan["retire_ids"]),
        )
    for action in plan["endpoint_actions"]:
        connection.execute(
            """UPDATE trusted_endpoints
               SET business_site_id=?,enabled=1,endpoint_state='bound',updated_at=? WHERE id=?""",
            (site_by_mn[str(action["mn"])], now, action["endpoint_id"]),
        )
    if plan["disabled_endpoint_ids"]:
        marks = ",".join("?" * len(plan["disabled_endpoint_ids"]))
        connection.execute(
            f"""UPDATE trusted_endpoints SET enabled=0,endpoint_state='disabled',updated_at=?
                WHERE id IN ({marks})""",
            [now, *plan["disabled_endpoint_ids"]],
        )


def _verify_applied(connection: sqlite3.Connection, records: list[dict[str, object]]) -> None:
    expected = {(str(item["mn"]), str(item["name"])) for item in records}
    actual = {
        (str(row["code"]), str(row["name"])) for row in connection.execute(
            "SELECT code,name FROM sites WHERE master_status='active'"
        )
    }
    if actual != expected:
        raise StationMasterRefreshError("active station catalogue does not match the approved workbook")
    active_ids = {
        int(row["id"]) for row in connection.execute("SELECT id FROM sites WHERE master_status='active'")
    }
    invalid_endpoint = connection.execute(
        """SELECT 1 FROM trusted_endpoints endpoint
           JOIN sites site ON site.id=endpoint.business_site_id
           WHERE endpoint.enabled=1 AND endpoint.endpoint_state='bound'
             AND site.master_status='retired' LIMIT 1"""
    ).fetchone()
    if invalid_endpoint:
        raise StationMasterRefreshError("retired station still has an enabled bound endpoint")
    for record in records:
        endpoint = connection.execute(
            "SELECT business_site_id,enabled,endpoint_state FROM trusted_endpoints WHERE station_code=?",
            (record["mn"],),
        ).fetchone()
        if endpoint:
            if (endpoint["business_site_id"] is None
                    or int(endpoint["business_site_id"]) not in active_ids
                    or not endpoint["enabled"] or endpoint["endpoint_state"] != "bound"):
                raise StationMasterRefreshError("active station endpoint binding is inconsistent")

    source_mn = [str(record["mn"]) for record in records]
    marks = ",".join("?" * len(source_mn))
    invalid_profile = connection.execute(
        f"""SELECT 1 FROM monitoring_endpoint_profiles profile
            JOIN trusted_endpoints endpoint ON endpoint.id=profile.endpoint_id
            WHERE endpoint.station_code IN ({marks}) AND profile.enabled=1
              AND profile.effective_to IS NULL
              AND profile.business_site_id!=endpoint.business_site_id LIMIT 1""",
        source_mn,
    ).fetchone()
    if invalid_profile:
        raise StationMasterRefreshError("active monitoring profile binding is inconsistent")


def apply_station_master_refresh(
    database: Path,
    workbook: Path,
    *,
    backup_dir: Path,
    expected_fingerprint: str,
    expected_row_count: int,
    offline_confirmed: bool,
    retirement_confirmed: bool,
) -> dict[str, object]:
    if not offline_confirmed or not retirement_confirmed:
        raise StationMasterRefreshError("apply requires offline and retirement confirmation")
    records = load_station_master_workbook(workbook)
    with closing(_connect(database)) as connection:
        preflight = _plan_from_connection(connection, records)
        existing_audit = connection.execute(
            "SELECT preview_fingerprint FROM station_master_refresh_audits WHERE source_fingerprint=?",
            (preflight["source_fingerprint"],),
        ).fetchone()
    if preflight["conflicts"]:
        raise StationMasterRefreshError("station master preview contains unresolved conflicts")
    if expected_row_count != len(records) or expected_fingerprint != preflight["preview_fingerprint"]:
        raise StationMasterRefreshError("station master source or database changed after preview")
    if existing_audit:
        with closing(_connect(database)) as connection:
            _verify_applied(connection, records)
        return dict(_public_plan(preflight), result="already_applied", backup=None)
    backup = _backup_database(database, backup_dir, str(preflight["source_fingerprint"]))
    with closing(_connect(database)) as connection:
        before_foreign_keys = {
            tuple(row) for row in connection.execute("PRAGMA foreign_key_check").fetchall()
        }
        try:
            connection.execute("BEGIN IMMEDIATE")
            locked = _plan_from_connection(connection, records)
            if locked["conflicts"] or locked["preview_fingerprint"] != expected_fingerprint:
                raise StationMasterRefreshError("station master source or database changed after preview")
            _apply_plan(connection, locked)
            _verify_applied(connection, records)
            after_foreign_keys = {
                tuple(row) for row in connection.execute("PRAGMA foreign_key_check").fetchall()
            }
            if after_foreign_keys != before_foreign_keys:
                raise StationMasterRefreshError("station master refresh changed foreign key violations")
            connection.execute(
                """INSERT INTO station_master_refresh_audits(
                       source_fingerprint,preview_fingerprint,accepted_rows,exact_mn_count,
                       alias_match_count,created_count,retired_count,rebound_endpoint_count,
                       disabled_endpoint_count,applied_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (locked["source_fingerprint"], locked["preview_fingerprint"], locked["accepted_rows"],
                 locked["exact_mn_count"], locked["alias_match_count"], locked["created_count"],
                 locked["retired_count"], locked["rebound_endpoint_count"],
                 locked["disabled_endpoint_count"], _utc_now()),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return dict(_public_plan(preflight), result="applied", backup=str(backup))


def verify_station_master_refresh(
    database: Path, workbook: Path, *, expected_source_fingerprint: str | None = None,
) -> dict[str, object]:
    records = load_station_master_workbook(workbook)
    source_fingerprint = _source_fingerprint(records)
    if expected_source_fingerprint and expected_source_fingerprint != source_fingerprint:
        raise StationMasterRefreshError("station master source changed after application")
    with closing(_connect(database)) as connection:
        _require_refresh_schema(connection)
        audit = connection.execute(
            "SELECT * FROM station_master_refresh_audits WHERE source_fingerprint=?",
            (source_fingerprint,),
        ).fetchone()
        if not audit:
            raise StationMasterRefreshError("station master refresh audit record is missing")
        _verify_applied(connection, records)
        return {"result": "verified", "source_fingerprint": source_fingerprint,
                "accepted_rows": len(records), "applied_at": audit["applied_at"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("plan", "apply", "verify"))
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--workbook", required=True, type=Path)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--expected-fingerprint")
    parser.add_argument("--expected-source-fingerprint")
    parser.add_argument("--expected-row-count", type=int)
    parser.add_argument("--offline-confirmed", action="store_true")
    parser.add_argument("--retirement-confirmed", action="store_true")
    arguments = parser.parse_args()
    if arguments.action == "plan":
        result = preview_station_master_refresh(arguments.database, arguments.workbook)
    elif arguments.action == "apply":
        if not arguments.backup_dir or not arguments.expected_fingerprint or arguments.expected_row_count is None:
            raise StationMasterRefreshError("apply requires backup, preview fingerprint, and expected row count")
        result = apply_station_master_refresh(
            arguments.database, arguments.workbook, backup_dir=arguments.backup_dir,
            expected_fingerprint=arguments.expected_fingerprint,
            expected_row_count=arguments.expected_row_count,
            offline_confirmed=arguments.offline_confirmed,
            retirement_confirmed=arguments.retirement_confirmed,
        )
    else:
        result = verify_station_master_refresh(
            arguments.database, arguments.workbook,
            expected_source_fingerprint=arguments.expected_source_fingerprint,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
