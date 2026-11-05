#!/usr/bin/env python3
"""Build a compact SQLite database from an immutable recovery copy.

The command deliberately separates inventory from rebuild. Inventory emits a
complete, pending table contract. Rebuild refuses to run until every source
table is explicitly approved in that contract.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import shutil
import sqlite3
import struct
import sys
import time
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote


TOOL_VERSION = "offline-rebuild-water-monitor-v4"
CHUNK_SIZE = 1000
ALLOWED_ACTIONS = {
    "copy_all",
    "skip_excluded_raw_rows",
    "skip_rows_for_excluded_raw",
    "null_excluded_raw_reference",
    "skip_excluded_canonical_rows",
    "copy_retained_raw_rows",
    "copy_retained_parse_rows",
    "copy_retained_error_rows",
    "copy_rows_for_retained_raw",
}
MANIFEST_TIME_KEYS = {
    "as_of", "snapshot_at", "backup_at", "backup_time", "consistent_at",
    "consistency_time",
}
MANIFEST_PATH_KEYS = {
    "path", "local_path", "absolute_path", "database_path", "output_path",
}


class RebuildError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_utc_timestamp(value: str, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise RebuildError(f"{label} must be a non-empty timezone-aware timestamp")
    normalized = value.strip()
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise RebuildError(f"{label} is not a valid ISO-8601 timestamp: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RebuildError(f"{label} must include an explicit timezone: {value}")
    return parsed.astimezone(timezone.utc)


def _format_utc_timestamp(value: datetime) -> str:
    timespec = "microseconds" if value.microsecond else "seconds"
    return value.astimezone(timezone.utc).isoformat(timespec=timespec).replace("+00:00", "Z")


def _selection_boundaries(as_of: str) -> dict[str, str]:
    as_of_utc = _parse_utc_timestamp(as_of, "--as-of")
    return {
        "as_of_utc": _format_utc_timestamp(as_of_utc),
        "raw_received_from": _format_utc_timestamp(as_of_utc - timedelta(hours=168)),
        "parse_created_from": _format_utc_timestamp(as_of_utc - timedelta(hours=72)),
    }


def _timestamps_match(left: str, right: str, label: str) -> bool:
    return _parse_utc_timestamp(left, label) == _parse_utc_timestamp(right, label)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RebuildError(f"cannot read JSON: {path}") from exc


def _sha256_file(path: Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _walk_json(value: Any) -> Iterable[tuple[str | None, Any, dict[str, Any] | None]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item, value
            yield from _walk_json(item)
    elif isinstance(value, list):
        for item in value:
            yield None, item, None
            yield from _walk_json(item)


def _manifest_entry(manifest: Any, source: Path, as_of: str) -> dict[str, Any]:
    source_resolved = source.resolve()
    times = [
        item for key, item, _parent in _walk_json(manifest)
        if key in MANIFEST_TIME_KEYS and isinstance(item, str)
    ]
    if not times:
        raise RebuildError("source manifest has no consistency timestamp")
    as_of_utc = _parse_utc_timestamp(as_of, "--as-of")
    parsed_times = []
    for item in times:
        try:
            parsed_times.append(_parse_utc_timestamp(item, "source manifest timestamp"))
        except RebuildError:
            continue
    if as_of_utc not in parsed_times:
        raise RebuildError("--as-of does not match the source manifest")

    matches: list[dict[str, Any]] = []
    for key, item, parent in _walk_json(manifest):
        if key not in MANIFEST_PATH_KEYS or not isinstance(item, str) or parent is None:
            continue
        try:
            if Path(item).resolve() == source_resolved:
                matches.append(parent)
        except OSError:
            continue
    if not matches:
        raise RebuildError("source database is not declared in SOURCE_MANIFEST.json")
    entry = matches[0]
    declared_size = entry.get(
        "size", entry.get("size_bytes", entry.get("output_size_bytes"))
    )
    declared_mtime_ns = entry.get("mtime_ns")
    declared_sha = entry.get("sha256", entry.get("checksum_sha256"))
    if declared_size is None:
        raise RebuildError("source manifest entry requires size evidence")
    if int(declared_size) != source.stat().st_size:
        raise RebuildError("source size does not match the source manifest")
    if declared_mtime_ns is not None and int(declared_mtime_ns) != source.stat().st_mtime_ns:
        raise RebuildError("source mtime does not match the prepared source manifest")
    if declared_sha and re.fullmatch(r"[0-9a-fA-F]{64}", str(declared_sha)):
        identity = {
            "declared_sha256": str(declared_sha).lower(),
            "identity_basis": "combined_file_sha256",
        }
    else:
        source_section = manifest.get("source") if isinstance(manifest, dict) else None
        parts = manifest.get("parts") if isinstance(manifest, dict) else None
        restore_order = source_section.get("restore_order") if isinstance(source_section, dict) else None
        if not isinstance(parts, list) or not isinstance(restore_order, list) or not parts:
            raise RebuildError("source manifest lacks combined SHA or verified ordered parts")
        ordered = sorted(parts, key=lambda item: int(item.get("order", 0)))
        if [item.get("filename") for item in ordered] != restore_order:
            raise RebuildError("source manifest part order does not match restore_order")
        part_size = 0
        part_evidence: list[dict[str, Any]] = []
        for item in ordered:
            digest = str(item.get("sha256") or "")
            local_path = Path(str(item.get("local_path") or ""))
            size = int(item.get("size_bytes", -1))
            if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                raise RebuildError("source manifest part sha256 evidence is malformed")
            if item.get("local_size_and_sha256") != "PASS":
                raise RebuildError("source manifest part is not locally verified")
            if not local_path.is_file() or local_path.stat().st_size != size:
                raise RebuildError("verified source part is missing or has changed size")
            part_size += size
            part_evidence.append({
                "filename": item.get("filename"), "size": size,
                "sha256": digest.lower(),
            })
        if part_size != int(declared_size):
            raise RebuildError("ordered source part sizes do not match assembled database")
        identity = {
            "declared_sha256": None,
            "identity_basis": "verified_part_sha256_order_and_assembled_size",
            "parts": part_evidence,
        }
    return {
        "declared_size": int(declared_size),
        "declared_mtime_ns": int(declared_mtime_ns) if declared_mtime_ns is not None else None,
        "as_of": as_of,
        **identity,
    }


def _source_connection(path: Path) -> sqlite3.Connection:
    uri = "file:" + quote(str(path.resolve()).replace("\\", "/"), safe="/:") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    # mode=ro protects the source while still allowing private TEMP selection tables.
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def _configure_sqlite_temp(connection: sqlite3.Connection, temp_dir: Path) -> dict[str, Any]:
    temp_dir.mkdir(parents=True, exist_ok=True)
    if not temp_dir.is_dir():
        raise RebuildError(f"SQLite TEMP path is not a directory: {temp_dir}")
    resolved = temp_dir.resolve()
    escaped = str(resolved).replace("'", "''")
    try:
        connection.execute("PRAGMA temp_store=FILE")
        connection.execute(f"PRAGMA temp_store_directory='{escaped}'")
        temp_store = int(connection.execute("PRAGMA temp_store").fetchone()[0])
        row = connection.execute("PRAGMA temp_store_directory").fetchone()
    except sqlite3.DatabaseError as exc:
        raise RebuildError(f"cannot configure SQLite TEMP directory: {resolved}") from exc
    configured = Path(str(row[0])).resolve() if row and row[0] else None
    if temp_store != 1 or configured != resolved:
        raise RebuildError(f"SQLite did not adopt the required TEMP directory: {resolved}")
    return {
        "path": str(resolved),
        "temp_store": "FILE",
        "pragma_temp_store": temp_store,
        "pragma_temp_store_directory": str(configured),
        "free_bytes_before": shutil.disk_usage(resolved).free,
    }


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _schema_objects(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT type,name,tbl_name,sql FROM sqlite_master
           WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL
           ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1
                              WHEN 'view' THEN 2 WHEN 'trigger' THEN 3 ELSE 4 END,
                    name"""
    ).fetchall()
    return [dict(row) for row in rows]


def _schema_digest(objects: list[dict[str, Any]]) -> str:
    normalized = [
        {key: item.get(key) for key in ("type", "name", "tbl_name", "sql")}
        for item in objects
    ]
    return _sha256_json(normalized)


def _tables(objects: list[dict[str, Any]]) -> list[str]:
    return [str(item["name"]) for item in objects if item["type"] == "table"]


def _schema_object_map(
    objects: list[dict[str, Any]], *, label: str,
) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for item in objects:
        if not isinstance(item, dict):
            raise RebuildError(f"{label} contains a non-object schema entry")
        normalized = {
            key: item.get(key) for key in ("type", "name", "tbl_name", "sql")
        }
        if not all(isinstance(normalized[key], str) for key in normalized):
            raise RebuildError(f"{label} contains an incomplete schema entry")
        identity = f"{normalized['type']}:{normalized['name']}"
        if identity in mapped:
            raise RebuildError(f"{label} contains a duplicate schema object: {identity}")
        mapped[identity] = normalized
    return mapped


def _table_columns(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    rows = connection.execute(f"PRAGMA table_xinfo({_quote_identifier(table)})").fetchall()
    columns = []
    for row in rows:
        item = dict(row)
        if int(item.get("hidden", 0)) == 0:
            columns.append(item)
    if not columns:
        raise RebuildError(f"table has no writable columns: {table}")
    return columns


def _order_clause(columns: list[dict[str, Any]]) -> str:
    primary = sorted(
        ((int(column["pk"]), str(column["name"])) for column in columns if int(column["pk"])),
        key=lambda item: item[0],
    )
    if primary:
        return ",".join(_quote_identifier(name) for _position, name in primary)
    return "rowid"


def _hash_value(digest: Any, value: Any) -> None:
    if value is None:
        payload = b"n"
    elif isinstance(value, bytes):
        payload = b"b" + value
    elif isinstance(value, int):
        payload = b"i" + str(value).encode("ascii")
    elif isinstance(value, float):
        payload = b"f" + struct.pack(">d", value)
    elif isinstance(value, str):
        payload = b"s" + value.encode("utf-8")
    else:
        raise RebuildError(f"unsupported SQLite value type: {type(value).__name__}")
    digest.update(struct.pack(">Q", len(payload)))
    digest.update(payload)


def _hash_row(digest: Any, row: tuple[Any, ...]) -> None:
    digest.update(struct.pack(">I", len(row)))
    for value in row:
        _hash_value(digest, value)


def _known_responsibility(table: str) -> str:
    if table in {
        "observation_batches", "observation_values", "monitoring_business_observations",
        "monitoring_hourly_values", "monitoring_hourly_value_series",
    }:
        return "monitoring_result_full_preserve"
    if table.startswith("ingest_") or table.startswith("station_ingest_"):
        return "ingest_state_review_required"
    if table.startswith("monitoring_"):
        return "monitoring_configuration_or_evidence"
    if table in {"users", "user_roles", "user_sites", "sites", "trusted_endpoints"}:
        return "identity_configuration_full_preserve"
    return "business_or_unknown_small_table_full_preserve"


def _consumer_files(code_root: Path, tables: list[str]) -> dict[str, list[str]]:
    result = {table: [] for table in tables}
    if not code_root.exists():
        return result
    candidates = [path for path in code_root.rglob("*.py") if "__pycache__" not in path.parts]
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = path.relative_to(code_root).as_posix()
        for table in tables:
            if re.search(rf"\b{re.escape(table)}\b", text):
                result[table].append(relative)
    return result


def inventory(args: argparse.Namespace) -> dict[str, Any]:
    source = args.source.resolve()
    if not source.is_file():
        raise RebuildError(f"source database does not exist: {source}")
    manifest = _load_json(args.source_manifest)
    manifest_evidence = _manifest_entry(manifest, source, args.as_of)
    with closing(_source_connection(source)) as connection:
        objects = _schema_objects(connection)
        tables = _tables(objects)
    consumers = _consumer_files(args.code_root, tables)
    manifest_digest = _sha256_file(args.source_manifest)
    rules = {
        "tool_version": TOOL_VERSION,
        "version": args.rules_version,
        "as_of": args.as_of,
        "review_status": "pending",
        "source_manifest_sha256": manifest_digest,
        "source_schema_sha256": _schema_digest(objects),
        "exact_duplicate_policy": None,
        "tables": {
            table: {
                "action": "copy_all",
                "review_status": "pending",
                "responsibility": _known_responsibility(table),
                "reason": "Default preserve pending source-specific product review",
            }
            for table in tables
        },
    }
    _write_json(args.rules_output, rules)

    lines = [
        "# Rebuild Plan", "", f"- Status: inventory complete; product rules pending",
        f"- Source: `{source}`", f"- As-of: `{args.as_of}`",
        f"- Source bytes: `{manifest_evidence['declared_size']}`",
        f"- Source schema SHA-256: `{rules['source_schema_sha256']}`", "",
        "| Table | Responsibility | Proposed action | Consumers | Count/bytes | Review |",
        "|---|---|---|---|---|---|",
    ]
    for table in tables:
        refs = ", ".join(consumers[table][:6]) or "No Python reference found"
        lines.append(
            f"| `{table}` | `{_known_responsibility(table)}` | `copy_all` | "
            f"{refs} | UNKNOWN until rebuild | PENDING |"
        )
    args.plan_output.parent.mkdir(parents=True, exist_ok=True)
    args.plan_output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "status": "inventory_complete",
        "tables": len(tables),
        "rules_output": str(args.rules_output.resolve()),
        "plan_output": str(args.plan_output.resolve()),
        "source_manifest_sha256": manifest_digest,
        "source_schema_sha256": rules["source_schema_sha256"],
    }


def _validate_reviewed_template(
    template: dict[str, Any], objects: list[dict[str, Any]],
) -> dict[str, Any]:
    if template.get("review_status") != "approved":
        raise RebuildError("template rules are not approved")
    template_tables = template.get("tables")
    if not isinstance(template_tables, dict):
        raise RebuildError("template rules do not contain table classifications")
    actual_tables = set(_tables(objects))
    declared_tables = set(template_tables)
    schema_comparison = {
        "template_schema_sha256": template.get("source_schema_sha256"),
        "actual_schema_sha256": _schema_digest(objects),
        "added_tables": sorted(actual_tables - declared_tables),
        "removed_tables": sorted(declared_tables - actual_tables),
        "reviewed_schema_objects_present": False,
        "added_objects": [],
        "removed_objects": [],
        "changed_objects": [],
    }
    reviewed_objects = template.get("reviewed_schema_objects")
    if reviewed_objects is not None:
        if not isinstance(reviewed_objects, list):
            raise RebuildError("template reviewed_schema_objects must be a list")
        reviewed_map = _schema_object_map(
            reviewed_objects, label="template reviewed_schema_objects",
        )
        actual_map = _schema_object_map(objects, label="actual source schema")
        reviewed_keys = set(reviewed_map)
        actual_keys = set(actual_map)
        schema_comparison.update({
            "reviewed_schema_objects_present": True,
            "added_objects": sorted(actual_keys - reviewed_keys),
            "removed_objects": sorted(reviewed_keys - actual_keys),
            "changed_objects": sorted(
                key for key in reviewed_keys & actual_keys
                if reviewed_map[key] != actual_map[key]
            ),
        })
    schema_comparison["matches"] = (
        schema_comparison["template_schema_sha256"]
        == schema_comparison["actual_schema_sha256"]
        and not schema_comparison["added_tables"]
        and not schema_comparison["removed_tables"]
        and not schema_comparison["added_objects"]
        and not schema_comparison["removed_objects"]
        and not schema_comparison["changed_objects"]
    )
    if not schema_comparison["matches"]:
        return schema_comparison
    for table, rule in template_tables.items():
        if not isinstance(rule, dict) or rule.get("review_status") != "approved":
            raise RebuildError(f"template table rule is not approved: {table}")
        if rule.get("action") not in ALLOWED_ACTIONS:
            raise RebuildError(f"template table action is unsupported: {table}")
    selection = template.get("selection_policy")
    retained_actions = {
        rule.get("action") for rule in template_tables.values()
    } & {
        "copy_retained_raw_rows", "copy_retained_parse_rows",
        "copy_retained_error_rows", "copy_rows_for_retained_raw",
    }
    if retained_actions:
        if not isinstance(selection, dict) or selection.get("review_status") != "approved":
            raise RebuildError("template retained-row rules require an approved selection policy")
        if selection.get("error_policy") != "active_unknown_endpoint_reauthentication_only":
            raise RebuildError("template selection error policy is not approved")
    return schema_comparison


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    source = args.source.resolve()
    manifest_output = args.manifest_output.resolve()
    rules_output = args.rules_output.resolve()
    report_path = args.report.resolve()
    if not args.consistent_source_confirmed:
        raise RebuildError(
            "prepare requires confirmation of a stopped-writer source or complete consistent snapshot"
        )
    if not source.is_file():
        raise RebuildError(f"source database does not exist: {source}")
    for path in (manifest_output, rules_output, report_path):
        if path.exists():
            raise RebuildError(f"output already exists: {path}")
    template = _load_json(args.template_rules)
    if not isinstance(template, dict):
        raise RebuildError("template rules root must be an object")
    source_before = source.stat()
    with closing(_source_connection(source)) as connection:
        objects = _schema_objects(connection)
    schema_comparison = _validate_reviewed_template(template, objects)
    report: dict[str, Any] = {
        "tool_version": TOOL_VERSION,
        "status": "schema_rejected" if not schema_comparison["matches"] else "preparing",
        "started_at": _utc_now(),
        "source": str(source),
        "as_of": args.as_of,
        "as_of_utc": _selection_boundaries(args.as_of)["as_of_utc"],
        "template_rules": str(args.template_rules.resolve()),
        "template_rules_sha256": _sha256_file(args.template_rules),
        "schema_comparison": schema_comparison,
        "source_stat_before": {
            "size": source_before.st_size, "mtime_ns": source_before.st_mtime_ns,
        },
    }
    if not schema_comparison["matches"]:
        report["error"] = (
            "source schema differs from the reviewed template; no manifest or executable rules generated"
        )
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        _write_json(report_path, report)
        raise RebuildError(report["error"])

    identity_started = time.monotonic()
    source_sha256 = _sha256_file(source)
    identity_seconds = round(time.monotonic() - identity_started, 3)
    source_after_hash = source.stat()
    if (source_before.st_size, source_before.st_mtime_ns) != (
        source_after_hash.st_size, source_after_hash.st_mtime_ns,
    ):
        raise RebuildError("source database changed while computing final identity")

    manifest = {
        "version": "offline-rebuild-source-manifest-v1",
        "consistent_at": args.as_of,
        "consistency_basis": "stopped_writers_or_complete_consistent_snapshot_confirmed",
        "files": [{
            "local_path": str(source),
            "size_bytes": source_before.st_size,
            "mtime_ns": source_before.st_mtime_ns,
            "sha256": source_sha256,
            "identity_computation": "single_full_file_sha256",
            "identity_elapsed_seconds": identity_seconds,
        }],
    }
    _write_json(manifest_output, manifest)
    manifest_digest = _sha256_file(manifest_output)

    rules = copy.deepcopy(template)
    rules.pop("reviewed_schema_objects", None)
    rules.pop("template_rebinding", None)
    rules["tool_version"] = TOOL_VERSION
    rules["version"] = args.rules_version
    rules["as_of"] = args.as_of
    rules["source_manifest_sha256"] = manifest_digest
    rules["source_schema_sha256"] = schema_comparison["actual_schema_sha256"]
    selection = rules.get("selection_policy")
    if isinstance(selection, dict):
        boundaries = _selection_boundaries(args.as_of)
        selection["raw_received_from"] = boundaries["raw_received_from"]
        selection["parse_created_from"] = boundaries["parse_created_from"]
    _validate_rules(rules, objects, manifest_digest, args.as_of)
    _write_json(rules_output, rules)

    source_after = source.stat()
    report.update({
        "status": "completed",
        "completed_at": _utc_now(),
        "source_identity": {
            "sha256": source_sha256,
            "computation": "single_full_file_sha256",
            "elapsed_seconds": identity_seconds,
            "reused_by_rebuild": True,
        },
        "source_manifest": str(manifest_output),
        "source_manifest_sha256": manifest_digest,
        "rules_output": str(rules_output),
        "rules_sha256": _sha256_file(rules_output),
        "rules_version": args.rules_version,
        "selection_boundaries": _selection_boundaries(args.as_of),
        "source_stat_after": {
            "size": source_after.st_size, "mtime_ns": source_after.st_mtime_ns,
        },
        "elapsed_seconds": round(time.monotonic() - started, 3),
    })
    if report["source_stat_before"] != report["source_stat_after"]:
        raise RebuildError("source database changed during prepare")
    _write_json(report_path, report)
    return report


def _require_exact_policy_tables(tables: set[str]) -> None:
    required = {
        "ingest_raw_frames", "ingest_errors", "observation_batches",
        "monitoring_quality_issues", "monitoring_normalization_retries",
        "station_ingest_slot_summaries", "station_ingest_dedup_keys",
        "station_ingest_duplicate_links", "station_ingest_recent_receipts",
        "station_ingest_current_state",
    }
    missing = sorted(required - tables)
    if missing:
        raise RebuildError("exact duplicate policy requires tables: " + ", ".join(missing))


def _excluded_exact_duplicate_ids(
    connection: sqlite3.Connection, policy: dict[str, Any], tables: set[str]
) -> set[int]:
    _require_exact_policy_tables(tables)
    cutoff = str(policy.get("cutoff") or "")
    receipt_cutoff = str(policy.get("receipt_cutoff") or "")
    ranges = policy.get("candidate_ranges")
    if not cutoff or not receipt_cutoff or not isinstance(ranges, list) or not ranges:
        raise RebuildError("exact duplicate policy requires cutoff, receipt_cutoff and ranges")
    excluded: set[int] = set()
    query = """SELECT raw.id
                 FROM ingest_raw_frames raw
                 JOIN ingest_raw_frames canonical
                   ON canonical.id=raw.duplicate_of_raw_frame_id
                  AND canonical.endpoint_id=raw.endpoint_id
                  AND canonical.logical_key_sha256=raw.logical_key_sha256
                  AND canonical.frame_sha256=raw.frame_sha256
                  AND canonical.raw_frame=raw.raw_frame
                WHERE raw.id BETWEEN ? AND ?
                  AND raw.received_at<?
                  AND raw.disposition='duplicate'
                  AND raw.persistence_state='persisted'
                  AND NOT EXISTS (SELECT 1 FROM observation_batches b WHERE b.raw_frame_id=raw.id)
                  AND NOT EXISTS (SELECT 1 FROM ingest_errors e WHERE e.raw_frame_id=raw.id)
                  AND NOT EXISTS (SELECT 1 FROM monitoring_quality_issues q WHERE q.raw_frame_id=raw.id)
                  AND NOT EXISTS (SELECT 1 FROM monitoring_normalization_retries r WHERE r.raw_frame_id=raw.id)
                  AND NOT EXISTS (SELECT 1 FROM station_ingest_slot_summaries s WHERE s.canonical_raw_frame_id=raw.id)
                  AND NOT EXISTS (
                      SELECT 1 FROM station_ingest_dedup_keys d
                       WHERE d.canonical_raw_frame_id=raw.id AND d.protected=1)
                  AND NOT EXISTS (SELECT 1 FROM station_ingest_duplicate_links l WHERE l.canonical_raw_frame_id=raw.id)
                  AND NOT EXISTS (SELECT 1 FROM ingest_raw_frames child WHERE child.duplicate_of_raw_frame_id=raw.id)
                  AND NOT EXISTS (
                      SELECT 1 FROM station_ingest_recent_receipts rr
                       WHERE rr.raw_frame_id=raw.id AND rr.received_at>=?)
                  AND NOT EXISTS (
                      SELECT 1 FROM station_ingest_current_state state
                       WHERE state.latest_raw_frame_id=raw.id
                          OR state.latest_authenticated_raw_frame_id=raw.id
                          OR state.latest_credential_failed_raw_frame_id=raw.id)
             ORDER BY raw.id"""
    previous_end = 0
    for item in ranges:
        if not isinstance(item, list) or len(item) != 2:
            raise RebuildError("candidate ranges must be [start, end] pairs")
        start, end = int(item[0]), int(item[1])
        if start <= 0 or end < start or start <= previous_end:
            raise RebuildError("candidate ranges must be positive, ordered and non-overlapping")
        previous_end = end
        excluded.update(
            int(row[0]) for row in connection.execute(
                query, (start, end, cutoff, receipt_cutoff)
            )
        )
    return excluded


def _validate_rules(
    rules: dict[str, Any], objects: list[dict[str, Any]], manifest_digest: str, as_of: str
) -> None:
    if rules.get("tool_version") != TOOL_VERSION:
        raise RebuildError("rules target a different rebuild tool version")
    if rules.get("review_status") != "approved":
        raise RebuildError("rules are not approved")
    rules_as_of = rules.get("as_of")
    if not isinstance(rules_as_of, str) or not _timestamps_match(
        rules_as_of, as_of, "rules/as-of timestamp"
    ):
        raise RebuildError("rules as_of does not match --as-of")
    if rules.get("source_manifest_sha256") != manifest_digest:
        raise RebuildError("rules do not match SOURCE_MANIFEST.json")
    if rules.get("source_schema_sha256") != _schema_digest(objects):
        raise RebuildError("rules do not match the source schema")
    actual = set(_tables(objects))
    declared = set((rules.get("tables") or {}).keys())
    if actual != declared:
        raise RebuildError(
            "rules must classify every actual table; missing="
            + ",".join(sorted(actual - declared))
            + " extra=" + ",".join(sorted(declared - actual))
        )
    for table, rule in rules["tables"].items():
        if rule.get("review_status") != "approved":
            raise RebuildError(f"table rule is not approved: {table}")
        if rule.get("action") not in ALLOWED_ACTIONS:
            raise RebuildError(f"unsupported table action for {table}: {rule.get('action')}")

    selection = rules.get("selection_policy")
    selection_actions = {
        rule.get("action") for rule in rules["tables"].values()
    } & {
        "copy_retained_raw_rows", "copy_retained_parse_rows",
        "copy_retained_error_rows", "copy_rows_for_retained_raw",
    }
    if selection_actions:
        if not isinstance(selection, dict) or selection.get("review_status") != "approved":
            raise RebuildError("approved selection_policy is required for retained-row actions")
        expected = _selection_boundaries(as_of)
        raw_boundary = selection.get("raw_received_from")
        parse_boundary = selection.get("parse_created_from")
        if not isinstance(raw_boundary, str) or not _timestamps_match(
            raw_boundary, expected["raw_received_from"], "selection raw boundary"
        ):
            raise RebuildError(
                "selection_policy raw boundary does not equal --as-of minus 168 hours"
            )
        if not isinstance(parse_boundary, str) or not _timestamps_match(
            parse_boundary, expected["parse_created_from"], "selection parse boundary"
        ):
            raise RebuildError(
                "selection_policy parse boundary does not equal --as-of minus 72 hours"
            )
        if selection.get("error_policy") != "active_unknown_endpoint_reauthentication_only":
            raise RebuildError("selection_policy error rule does not match OFFLINE-R4")
    target_migrations = rules.get("target_migrations", [])
    if not isinstance(target_migrations, list) or any(
        not isinstance(item, str) for item in target_migrations
    ):
        raise RebuildError("target_migrations must be an ordered string list")


def _insert_ids(connection: sqlite3.Connection, query: str, parameters: tuple[Any, ...] = ()) -> int:
    before = connection.total_changes
    connection.execute(query, parameters)
    return connection.total_changes - before


def _prepare_selection(
    connection: sqlite3.Connection, rules: dict[str, Any], tables: set[str]
) -> dict[str, Any]:
    policy = rules.get("selection_policy")
    if not isinstance(policy, dict):
        return {"enabled": False}
    required = {
        "ingest_raw_frames", "ingest_parse_attempts", "ingest_errors",
        "ingest_frame_protocols", "observation_batches", "monitoring_quality_issues",
        "monitoring_normalization_retries", "station_ingest_current_state",
        "station_ingest_recent_receipts", "station_ingest_slot_summaries",
        "station_ingest_dedup_keys", "station_ingest_duplicate_links",
        "station_ingest_receipt_hourly", "station_ingest_error_hourly",
    }
    missing = sorted(required - tables)
    if missing:
        raise RebuildError("OFFLINE-R4 selection requires tables: " + ", ".join(missing))

    connection.executescript(
        """
        CREATE TEMP TABLE rebuild_retained_raw_ids(id INTEGER PRIMARY KEY);
        CREATE TEMP TABLE rebuild_retained_parse_ids(id INTEGER PRIMARY KEY);
        CREATE TEMP TABLE rebuild_retained_error_ids(id INTEGER PRIMARY KEY);
        CREATE TEMP TABLE rebuild_parse_evidence_raw_ids(id INTEGER PRIMARY KEY);
        """
    )

    error_rows = _insert_ids(
        connection,
        """INSERT OR IGNORE INTO rebuild_retained_error_ids(id)
             SELECT error.id
               FROM ingest_errors error
               JOIN ingest_raw_frames raw ON raw.id=error.raw_frame_id
               JOIN ingest_frame_protocols protocol ON protocol.raw_frame_id=raw.id
              WHERE error.status='open'
                AND error.error_type='unknown_endpoint'
                AND raw.endpoint_id IS NULL
                AND raw.authentication_status='unknown_endpoint'
                AND protocol.protocol_family='hj212'""",
    )

    parse_evidence_sources = (
        "SELECT raw_frame_id FROM observation_batches",
        """SELECT raw_frame_id FROM monitoring_normalization_retries
             WHERE state IN ('pending','retrying','processing')""",
        """SELECT error.raw_frame_id FROM ingest_errors error
             JOIN rebuild_retained_error_ids selected ON selected.id=error.id
             WHERE error.raw_frame_id IS NOT NULL""",
    )
    parse_evidence_rows = 0
    for query in parse_evidence_sources:
        parse_evidence_rows += _insert_ids(
            connection,
            "INSERT OR IGNORE INTO rebuild_parse_evidence_raw_ids(id) " + query,
        )

    parse_from = str(policy["parse_created_from"])
    parse_rows = _insert_ids(
        connection,
        """INSERT OR IGNORE INTO rebuild_retained_parse_ids(id)
             SELECT attempt.id
               FROM ingest_parse_attempts attempt
              WHERE julianday(attempt.created_at)>=julianday(?)
                 OR EXISTS (SELECT 1 FROM rebuild_parse_evidence_raw_ids evidence
                             WHERE evidence.id=attempt.raw_frame_id)""",
        (parse_from,),
    )

    raw_from = str(policy["raw_received_from"])
    raw_sources = [
        ("recent_or_business", """SELECT id FROM ingest_raw_frames
             WHERE julianday(received_at)>=julianday(?)
                OR disposition='accepted'
                OR persistence_state<>'persisted'""", (raw_from,)),
        ("selected_parse", """SELECT attempt.raw_frame_id FROM ingest_parse_attempts attempt
             JOIN rebuild_retained_parse_ids selected ON selected.id=attempt.id""", ()),
        ("selected_error", """SELECT error.raw_frame_id FROM ingest_errors error
             JOIN rebuild_retained_error_ids selected ON selected.id=error.id
             WHERE error.raw_frame_id IS NOT NULL""", ()),
        ("business_results", "SELECT raw_frame_id FROM observation_batches", ()),
        ("quality_evidence", "SELECT raw_frame_id FROM monitoring_quality_issues WHERE raw_frame_id IS NOT NULL", ()),
        ("normalization_retry", "SELECT raw_frame_id FROM monitoring_normalization_retries", ()),
        ("recent_receipts", "SELECT raw_frame_id FROM station_ingest_recent_receipts", ()),
        ("slot_canonical", "SELECT canonical_raw_frame_id FROM station_ingest_slot_summaries WHERE canonical_raw_frame_id IS NOT NULL", ()),
        ("dedup_canonical", "SELECT canonical_raw_frame_id FROM station_ingest_dedup_keys WHERE canonical_raw_frame_id IS NOT NULL", ()),
        ("current_latest", """SELECT latest_raw_frame_id FROM station_ingest_current_state WHERE latest_raw_frame_id IS NOT NULL
             UNION SELECT latest_authenticated_raw_frame_id FROM station_ingest_current_state WHERE latest_authenticated_raw_frame_id IS NOT NULL
             UNION SELECT latest_credential_failed_raw_frame_id FROM station_ingest_current_state WHERE latest_credential_failed_raw_frame_id IS NOT NULL""", ()),
        ("receipt_hour", "SELECT last_complete_raw_frame_id FROM station_ingest_receipt_hourly WHERE last_complete_raw_frame_id IS NOT NULL", ()),
        ("error_hour", "SELECT last_raw_frame_id FROM station_ingest_error_hourly WHERE last_raw_frame_id IS NOT NULL", ()),
    ]
    raw_additions: dict[str, int] = {}
    for label, query, parameters in raw_sources:
        raw_additions[label] = _insert_ids(
            connection,
            "INSERT OR IGNORE INTO rebuild_retained_raw_ids(id) " + query,
            parameters,
        )

    # Retained duplicates must keep their canonical raw chain unless a separate
    # evidence reference already replaces it.
    closure_rounds = 0
    while True:
        added = _insert_ids(
            connection,
            """INSERT OR IGNORE INTO rebuild_retained_raw_ids(id)
                 SELECT raw.duplicate_of_raw_frame_id
                   FROM ingest_raw_frames raw
                   JOIN rebuild_retained_raw_ids selected ON selected.id=raw.id
                  WHERE raw.duplicate_of_raw_frame_id IS NOT NULL""",
        )
        added += _insert_ids(
            connection,
            """INSERT OR IGNORE INTO rebuild_retained_raw_ids(id)
                 SELECT link.canonical_raw_frame_id
                   FROM station_ingest_duplicate_links link
                   JOIN rebuild_retained_raw_ids selected ON selected.id=link.raw_frame_id
                  WHERE link.canonical_raw_frame_id IS NOT NULL
                    AND link.canonical_evidence_ref_id IS NULL""",
        )
        closure_rounds += 1
        if not added:
            break
        if closure_rounds > 100:
            raise RebuildError("raw canonical closure did not converge")

    return {
        "enabled": True,
        "raw_received_from": raw_from,
        "parse_created_from": parse_from,
        "error_policy": policy["error_policy"],
        "retained_raw_rows": connection.execute(
            "SELECT COUNT(*) FROM rebuild_retained_raw_ids").fetchone()[0],
        "retained_parse_rows": connection.execute(
            "SELECT COUNT(*) FROM rebuild_retained_parse_ids").fetchone()[0],
        "retained_error_rows": connection.execute(
            "SELECT COUNT(*) FROM rebuild_retained_error_ids").fetchone()[0],
        "raw_additions": raw_additions,
        "canonical_closure_rounds": closure_rounds,
        "initial_parse_rows": parse_rows,
        "parse_evidence_raw_rows": parse_evidence_rows,
        "initial_error_rows": error_rows,
    }


def _transform_row(
    table: str, rule: dict[str, Any], columns: list[str], row: tuple[Any, ...],
    excluded_raw_ids: set[int],
) -> tuple[Any, ...] | None:
    action = str(rule["action"])
    by_name = {name: index for index, name in enumerate(columns)}
    if action in {
        "copy_all", "copy_retained_raw_rows", "copy_retained_parse_rows",
        "copy_retained_error_rows", "copy_rows_for_retained_raw",
    }:
        return row
    if action == "skip_excluded_raw_rows":
        if table != "ingest_raw_frames" or "id" not in by_name:
            raise RebuildError("skip_excluded_raw_rows is only valid for ingest_raw_frames")
        return None if int(row[by_name["id"]]) in excluded_raw_ids else row
    if action in {"skip_rows_for_excluded_raw", "skip_excluded_canonical_rows"}:
        key = str(rule.get("raw_reference_column") or "")
        if key not in by_name:
            raise RebuildError(f"raw reference column is missing in {table}: {key}")
        value = row[by_name[key]]
        return None if value is not None and int(value) in excluded_raw_ids else row
    if action == "null_excluded_raw_reference":
        mutable = list(row)
        references = rule.get("raw_reference_columns")
        if not isinstance(references, list) or not references:
            raise RebuildError(f"raw_reference_columns are required for {table}")
        for key in references:
            if key not in by_name:
                raise RebuildError(f"raw reference column is missing in {table}: {key}")
            value = mutable[by_name[key]]
            if value is not None and int(value) in excluded_raw_ids:
                mutable[by_name[key]] = None
        return tuple(mutable)
    raise RebuildError(f"unsupported action: {action}")


def _selected_table_query(
    table: str, rule: dict[str, Any], columns: list[str], order_sql: str
) -> str:
    action = str(rule["action"])
    column_sql = ",".join(f"source.{_quote_identifier(name)}" for name in columns)
    table_sql = _quote_identifier(table)
    qualified_order = (
        "source.rowid" if order_sql == "rowid"
        else ",".join(f"source.{item}" for item in order_sql.split(","))
    )
    if action == "copy_retained_raw_rows":
        if table != "ingest_raw_frames":
            raise RebuildError("copy_retained_raw_rows is only valid for ingest_raw_frames")
        join = "JOIN rebuild_retained_raw_ids selected ON selected.id=source.id"
    elif action == "copy_retained_parse_rows":
        if table != "ingest_parse_attempts":
            raise RebuildError("copy_retained_parse_rows is only valid for ingest_parse_attempts")
        join = "JOIN rebuild_retained_parse_ids selected ON selected.id=source.id"
    elif action == "copy_retained_error_rows":
        if table != "ingest_errors":
            raise RebuildError("copy_retained_error_rows is only valid for ingest_errors")
        join = "JOIN rebuild_retained_error_ids selected ON selected.id=source.id"
    elif action == "copy_rows_for_retained_raw":
        key = str(rule.get("raw_reference_column") or "")
        if key not in columns:
            raise RebuildError(f"raw reference column is missing in {table}: {key}")
        join = (
            "JOIN rebuild_retained_raw_ids selected ON selected.id="
            f"source.{_quote_identifier(key)}"
        )
    else:
        return f"SELECT {column_sql} FROM {table_sql} source ORDER BY {qualified_order}"
    return f"SELECT {column_sql} FROM {table_sql} source {join} ORDER BY {qualified_order}"


def _copy_table(
    source: sqlite3.Connection, target: sqlite3.Connection, table: str,
    rule: dict[str, Any], excluded_raw_ids: set[int],
) -> dict[str, Any]:
    column_info = _table_columns(source, table)
    columns = [str(item["name"]) for item in column_info]
    column_sql = ",".join(_quote_identifier(name) for name in columns)
    order_sql = _order_clause(column_info)
    selection_action = str(rule["action"]).startswith("copy_retained") or str(
        rule["action"]) == "copy_rows_for_retained_raw"
    source_total = source.execute(
        f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
    ).fetchone()[0] if selection_action else None
    select = source.execute(_selected_table_query(table, rule, columns, order_sql))
    insert = (
        f"INSERT INTO {_quote_identifier(table)} ({column_sql}) VALUES "
        f"({','.join('?' for _ in columns)})"
    )
    digest = hashlib.sha256()
    scanned_rows = copied_rows = excluded_rows = transformed_rows = 0
    while True:
        rows = select.fetchmany(CHUNK_SIZE)
        if not rows:
            break
        output: list[tuple[Any, ...]] = []
        for raw_row in rows:
            scanned_rows += 1
            original = tuple(raw_row)
            transformed = _transform_row(
                table, rule, columns, original, excluded_raw_ids
            )
            if transformed is None:
                excluded_rows += 1
                continue
            if transformed != original:
                transformed_rows += 1
            _hash_row(digest, transformed)
            output.append(transformed)
        if output:
            target.executemany(insert, output)
            copied_rows += len(output)
    source_rows = int(source_total) if source_total is not None else scanned_rows
    if source_total is not None:
        excluded_rows += source_rows - scanned_rows
    return {
        "source_rows": source_rows,
        "copied_rows": copied_rows,
        "excluded_rows": excluded_rows,
        "transformed_rows": transformed_rows,
        "selected_content_sha256": digest.hexdigest(),
        "action": rule["action"],
    }


def _target_table_digest(
    connection: sqlite3.Connection, table: str, columns: list[str] | None = None,
) -> tuple[int, str]:
    info = _table_columns(connection, table)
    selected_columns = columns or [str(item["name"]) for item in info]
    known_columns = {str(item["name"]) for item in info}
    if not set(selected_columns) <= known_columns:
        raise RebuildError(f"target columns are missing for table: {table}")
    query = (
        f"SELECT {','.join(_quote_identifier(name) for name in selected_columns)} "
        f"FROM {_quote_identifier(table)} ORDER BY {_order_clause(info)}"
    )
    digest = hashlib.sha256()
    count = 0
    cursor = connection.execute(query)
    while True:
        rows = cursor.fetchmany(CHUNK_SIZE)
        if not rows:
            return count, digest.hexdigest()
        for row in rows:
            _hash_row(digest, tuple(row))
            count += 1


def _apply_target_migrations(target: Path, requested: list[str]) -> dict[str, Any]:
    if not requested:
        return {"requested": [], "applied": False}
    migration_path = target.parents[2] / "backend" / "migrate_station_ingestion.py"
    if not migration_path.is_file():
        # target is normally outside the implementation tree; locate from this tool instead.
        migration_path = Path(__file__).resolve().parents[1] / "backend" / "migrate_station_ingestion.py"
    spec = importlib.util.spec_from_file_location("offline_rebuild_migrations", migration_path)
    if spec is None or spec.loader is None:
        raise RebuildError("cannot load target migration module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    known = [version for version, _path in module.MIGRATIONS]
    if requested != known[-len(requested):]:
        raise RebuildError("target migrations must be the final ordered migration suffix")
    applied, _backup = module.apply_migration(
        target,
        None,
        app_version="offline-rebuild-water-monitor-v2",
        external_recovery_confirmed=True,
    )
    with closing(sqlite3.connect(str(target))) as connection:
        present = {
            str(row[0]) for row in connection.execute(
                "SELECT version FROM schema_migrations WHERE version IN (%s)"
                % ",".join("?" for _ in requested),
                requested,
            )
        }
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode=DELETE")
    if present != set(requested):
        raise RebuildError("target migrations were not recorded completely")
    return {"requested": requested, "applied": bool(applied), "recorded": sorted(present)}


def _restore_sequences(source: sqlite3.Connection, target: sqlite3.Connection) -> int:
    has_sequence = source.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
    ).fetchone()
    if not has_sequence:
        return 0
    rows = source.execute("SELECT name,seq FROM sqlite_sequence ORDER BY name").fetchall()
    for row in rows:
        target.execute("DELETE FROM sqlite_sequence WHERE name=?", (row[0],))
        target.execute("INSERT INTO sqlite_sequence(name,seq) VALUES (?,?)", tuple(row))
    return len(rows)


def _create_archive(database: Path, archive: Path) -> None:
    if archive.exists():
        raise RebuildError(f"archive already exists: {archive}")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        bundle.write(database, arcname=database.name)


def rebuild(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    source = args.source.resolve()
    target = args.target.resolve()
    report_path = args.report.resolve()
    archive = args.archive.resolve()
    temp_argument = getattr(args, "temp_dir", None)
    temp_dir = (
        Path(temp_argument).resolve()
        if temp_argument is not None else (target.parent / ".sqlite-temp").resolve()
    )
    incomplete = report_path.with_suffix(report_path.suffix + ".incomplete.json")
    if not source.is_file():
        raise RebuildError(f"source database does not exist: {source}")
    if source == target:
        raise RebuildError("source and target must be different files")
    for path in (target, report_path, archive):
        if path.exists():
            raise RebuildError(f"output already exists: {path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest = _load_json(args.source_manifest)
    manifest_evidence = _manifest_entry(manifest, source, args.as_of)
    manifest_digest = _sha256_file(args.source_manifest)
    rules = _load_json(args.rules)
    if not isinstance(rules, dict):
        raise RebuildError("rules root must be an object")
    free = shutil.disk_usage(target.parent).free
    default_required = source.stat().st_size * 2 + 64 * 1024 * 1024
    explicit_required = getattr(args, "minimum_free_bytes", None)
    explicit_basis = getattr(args, "space_requirement_basis", None)
    if explicit_required is not None:
        required = int(explicit_required)
        requirement_source = "explicit_cli"
        requirement_basis = str(explicit_basis or "").strip()
        if not requirement_basis:
            raise RebuildError(
                "--space-requirement-basis is required with --minimum-free-bytes"
            )
    elif rules.get("local_rehearsal_minimum_free_bytes") is not None:
        required = int(rules["local_rehearsal_minimum_free_bytes"])
        requirement_source = "approved_rules_local_rehearsal"
        requirement_basis = str(
            rules.get("local_rehearsal_space_requirement_basis")
            or "approved local rehearsal rule"
        )
    else:
        required = default_required
        requirement_source = "local_default"
        requirement_basis = "source_bytes * 2 + 64 MiB"
    if explicit_required is None and explicit_basis is not None:
        raise RebuildError(
            "--space-requirement-basis requires --minimum-free-bytes"
        )
    if required <= 0:
        raise RebuildError("minimum free bytes must be positive")
    if free < required:
        raise RebuildError(f"insufficient target free space: need at least {required} bytes")
    rules_digest = _sha256_file(args.rules)
    source_stat = source.stat()
    report: dict[str, Any] = {
        "tool_version": TOOL_VERSION,
        "status": "running",
        "started_at": _utc_now(),
        "source": str(source),
        "target": str(target),
        "archive": str(archive),
        "sqlite_temp": {
            "path": str(temp_dir),
            "argument_source": "explicit_cli" if temp_argument is not None else "target_sibling_default",
        },
        "as_of": args.as_of,
        "as_of_utc": _selection_boundaries(args.as_of)["as_of_utc"],
        "rules_version": rules.get("version"),
        "rules_sha256": rules_digest,
        "source_manifest_sha256": manifest_digest,
        "source_manifest_evidence": manifest_evidence,
        "source_stat_before": {
            "size": source_stat.st_size, "mtime_ns": source_stat.st_mtime_ns,
        },
        "space_check": {
            "scope": "target_database_and_archive_preflight",
            "free_bytes_before": free,
            "required_free_bytes": required,
            "requirement_source": requirement_source,
            "requirement_basis": requirement_basis,
            "local_default_free_bytes": default_required,
        },
        "stages": {},
        "tables": {},
    }
    try:
        with closing(_source_connection(source)) as source_db:
            report["sqlite_temp"].update(_configure_sqlite_temp(source_db, temp_dir))
            objects = _schema_objects(source_db)
            _validate_rules(rules, objects, manifest_digest, args.as_of)
            tables = _tables(objects)
            table_set = set(tables)
            selection_started = time.monotonic()
            report["selection"] = _prepare_selection(source_db, rules, table_set)
            report["stages"]["selection_seconds"] = round(
                time.monotonic() - selection_started, 3
            )
            policy = rules.get("exact_duplicate_policy")
            excluded_raw_ids = (
                _excluded_exact_duplicate_ids(source_db, policy, table_set)
                if isinstance(policy, dict) and policy.get("enabled") else set()
            )
            report["excluded_exact_duplicate_raw_ids"] = {
                "count": len(excluded_raw_ids),
                "sha256": _sha256_json(sorted(excluded_raw_ids)),
            }
            schema_started = time.monotonic()
            with closing(sqlite3.connect(str(target), timeout=30)) as target_db:
                target_db.row_factory = sqlite3.Row
                target_db.execute("PRAGMA foreign_keys=OFF")
                target_db.execute("PRAGMA journal_mode=DELETE")
                target_db.execute("PRAGMA synchronous=FULL")
                for item in objects:
                    if item["type"] == "table":
                        target_db.execute(str(item["sql"]))
                report["stages"]["create_tables_seconds"] = round(
                    time.monotonic() - schema_started, 3
                )

                copy_started = time.monotonic()
                target_db.execute("BEGIN IMMEDIATE")
                for table in tables:
                    result = _copy_table(
                        source_db, target_db, table, rules["tables"][table], excluded_raw_ids
                    )
                    report["tables"][table] = result
                report["sequence_rows"] = _restore_sequences(source_db, target_db)
                target_db.commit()
                report["stages"]["copy_seconds"] = round(time.monotonic() - copy_started, 3)
                report["space_check"]["target_bytes_after_copy"] = target.stat().st_size

                index_started = time.monotonic()
                for item in objects:
                    if item["type"] in {"index", "view", "trigger"}:
                        target_db.execute(str(item["sql"]))
                target_db.commit()
                report["stages"]["indexes_views_triggers_seconds"] = round(
                    time.monotonic() - index_started, 3
                )
                report["space_check"]["target_bytes_after_indexes"] = target.stat().st_size

                verify_started = time.monotonic()
                for table in tables:
                    source_columns = [
                        str(item["name"]) for item in _table_columns(source_db, table)
                    ]
                    count, digest = _target_table_digest(
                        target_db, table, source_columns
                    )
                    expected = report["tables"][table]
                    if count != expected["copied_rows"] or digest != expected["selected_content_sha256"]:
                        raise RebuildError(f"target content mismatch for table: {table}")
                    expected["target_content_sha256"] = digest
                report["stages"]["selected_content_seconds"] = round(
                    time.monotonic() - verify_started, 3
                )

            migration_started = time.monotonic()
            report["target_migrations"] = _apply_target_migrations(
                target, list(rules.get("target_migrations") or [])
            )
            report["stages"]["target_migrations_seconds"] = round(
                time.monotonic() - migration_started, 3
            )
            report["space_check"]["target_bytes_after_migrations"] = target.stat().st_size

            verify_started = time.monotonic()
            with closing(sqlite3.connect(str(target))) as target_db:
                target_db.row_factory = sqlite3.Row
                integrity = str(target_db.execute("PRAGMA integrity_check").fetchone()[0])
                foreign_keys = [dict(row) for row in target_db.execute("PRAGMA foreign_key_check")]
            if integrity != "ok":
                raise RebuildError(f"target integrity_check failed: {integrity}")
            approved_foreign_keys = rules.get("allowed_source_foreign_key_violations", [])
            if not isinstance(approved_foreign_keys, list) or any(
                not isinstance(item, dict) for item in approved_foreign_keys
            ):
                raise RebuildError("allowed_source_foreign_key_violations must be a list of objects")
            foreign_key_key = lambda item: (
                str(item.get("table")), int(item.get("rowid")),
                str(item.get("parent")), int(item.get("fkid")),
            )
            actual_foreign_keys = {foreign_key_key(item) for item in foreign_keys}
            approved_foreign_key_set = {
                foreign_key_key(item) for item in approved_foreign_keys
            }
            new_foreign_keys = actual_foreign_keys - approved_foreign_key_set
            missing_foreign_keys = approved_foreign_key_set - actual_foreign_keys
            if new_foreign_keys or missing_foreign_keys:
                raise RebuildError(
                    "target foreign_key_check differs from approved source baseline: "
                    f"new={len(new_foreign_keys)} missing={len(missing_foreign_keys)}"
                )
            report["integrity_check"] = integrity
            report["foreign_key_violations"] = foreign_keys
            report["approved_source_foreign_key_violations"] = approved_foreign_keys
            report["new_foreign_key_violations"] = []
            report["stages"]["integrity_and_foreign_key_seconds"] = round(
                time.monotonic() - verify_started, 3
            )

        source_after = source.stat()
        report["source_stat_after"] = {
            "size": source_after.st_size, "mtime_ns": source_after.st_mtime_ns,
        }
        if report["source_stat_before"] != report["source_stat_after"]:
            raise RebuildError("source database changed during rebuild")
        report["target_bytes"] = target.stat().st_size
        archive_started = time.monotonic()
        _create_archive(target, archive)
        report["archive_bytes"] = archive.stat().st_size
        report["archive_sha256"] = _sha256_file(archive)
        report["stages"]["archive_seconds"] = round(time.monotonic() - archive_started, 3)
        report["space_check"]["free_bytes_after"] = shutil.disk_usage(target.parent).free
        report["sqlite_temp"]["free_bytes_after"] = shutil.disk_usage(temp_dir).free
        report["space_check"]["observed_target_plus_archive_bytes"] = (
            report["target_bytes"] + report["archive_bytes"]
        )
        report["status"] = "completed"
        report["completed_at"] = _utc_now()
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        _write_json(report_path, report)
        if incomplete.exists():
            incomplete.unlink()
        return report
    except Exception as exc:
        report["status"] = "incomplete"
        report["failed_at"] = _utc_now()
        report["error"] = str(exc)
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        _write_json(incomplete, report)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    inventory_parser = subparsers.add_parser("inventory")
    inventory_parser.add_argument("--source", type=Path, required=True)
    inventory_parser.add_argument("--source-manifest", type=Path, required=True)
    inventory_parser.add_argument("--as-of", required=True)
    inventory_parser.add_argument("--rules-version", required=True)
    inventory_parser.add_argument("--rules-output", type=Path, required=True)
    inventory_parser.add_argument("--plan-output", type=Path, required=True)
    inventory_parser.add_argument(
        "--code-root", type=Path, default=Path(__file__).resolve().parents[1] / "backend"
    )

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--source", type=Path, required=True)
    prepare_parser.add_argument("--template-rules", type=Path, required=True)
    prepare_parser.add_argument("--as-of", required=True)
    prepare_parser.add_argument("--rules-version", required=True)
    prepare_parser.add_argument("--manifest-output", type=Path, required=True)
    prepare_parser.add_argument("--rules-output", type=Path, required=True)
    prepare_parser.add_argument("--report", type=Path, required=True)
    prepare_parser.add_argument("--consistent-source-confirmed", action="store_true")

    rebuild_parser = subparsers.add_parser("rebuild")
    rebuild_parser.add_argument("--source", type=Path, required=True)
    rebuild_parser.add_argument("--source-manifest", type=Path, required=True)
    rebuild_parser.add_argument("--target", type=Path, required=True)
    rebuild_parser.add_argument("--rules", type=Path, required=True)
    rebuild_parser.add_argument("--as-of", required=True)
    rebuild_parser.add_argument("--report", type=Path, required=True)
    rebuild_parser.add_argument("--archive", type=Path, required=True)
    rebuild_parser.add_argument("--temp-dir", type=Path, required=True)
    rebuild_parser.add_argument("--minimum-free-bytes", type=int)
    rebuild_parser.add_argument("--space-requirement-basis")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "inventory":
            result = inventory(args)
        elif args.action == "prepare":
            result = prepare(args)
        else:
            result = rebuild(args)
    except RebuildError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
