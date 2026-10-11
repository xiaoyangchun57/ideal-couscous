"""Offline B1 HJ212 discovery and explicit B2 replay tools."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from .hj212_parser import parse_hj212_frame
    from .sl651_parser import FrameError
    from .sl651_server import credential_hmac
    from .station_monitoring import normalize_raw_frame
except ImportError:  # pragma: no cover - direct command execution
    from hj212_parser import parse_hj212_frame
    from sl651_parser import FrameError
    from sl651_server import credential_hmac
    from station_monitoring import normalize_raw_frame


class DiscoveryError(RuntimeError):
    pass


MAX_REPLAY_ROWS = 1000
MAX_REAUTH_ROWS = 1000


def _existing_database(database: Path) -> Path:
    database = Path(database)
    if not database.exists() or not database.is_file():
        raise DiscoveryError("database path must name an existing database file")
    return database


def _utc_bound(value: str, label: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DiscoveryError(f"{label} must be an ISO timestamp with timezone") from exc
    if parsed.tzinfo is None:
        raise DiscoveryError(f"{label} must include timezone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _selection(where: str, start_id: int | None, end_id: int | None,
               received_from: str | None, received_to: str | None) -> tuple[str, list[object], dict[str, object]]:
    ids_requested = start_id is not None or end_id is not None
    times_requested = received_from is not None or received_to is not None
    if ids_requested == times_requested or (ids_requested and (start_id is None or end_id is None)):
        raise DiscoveryError("specify either --start-id and --end-id, or --received-from and --received-to")
    if times_requested:
        if received_from is None or received_to is None:
            raise DiscoveryError("received time bounds must be supplied together")
        start = _utc_bound(received_from, "received-from")
        end = _utc_bound(received_to, "received-to")
        if end < start:
            raise DiscoveryError("received time range is invalid")
        return f"{where} AND raw.received_at>=? AND raw.received_at<=?", [start, end], {
            "received_from": start, "received_to": end,
        }
    if isinstance(start_id, bool) or isinstance(end_id, bool) or not isinstance(start_id, int) or not isinstance(end_id, int):
        raise DiscoveryError("raw ID bounds must be positive integers")
    if start_id <= 0 or end_id < start_id:
        raise DiscoveryError("raw ID range is invalid")
    return f"{where} AND raw.id>=? AND raw.id<=?", [start_id, end_id], {"start_id": start_id, "end_id": end_id}


def _parse_evidence(raw: bytes) -> tuple[str, list[str], list[str]]:
    try:
        frame = parse_hj212_frame(raw)
    except FrameError as exc:
        return exc.code, [], []
    if frame.command not in {"2011", "2061"} or frame.data_time is None:
        return "not_approved_data_command", [], []
    return (f"valid_cn{frame.command}", sorted({factor.protocol_code for factor in frame.factors}),
            sorted({factor.quality for factor in frame.factors}))


def discover_hj212(
    database: Path, *, start_id: int | None = None, end_id: int | None = None,
    received_from: str | None = None, received_to: str | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    """Read retained HJ212 facts only; never infer an approved profile or interval."""
    database = _existing_database(database)
    clause, values, selection = _selection(
        "protocol.protocol_family='hj212'", start_id, end_id, received_from, received_to,
    )
    with closing(sqlite3.connect(str(database))) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"""SELECT raw.id,raw.endpoint_id,raw.station_code AS raw_station_code,raw.received_at,
                       raw.raw_frame,raw.authentication_status,endpoint.station_code AS endpoint_station_code,
                       endpoint.business_site_id,site.name AS station_name
                FROM ingest_raw_frames raw
                JOIN ingest_frame_protocols protocol ON protocol.raw_frame_id=raw.id
                LEFT JOIN trusted_endpoints endpoint ON endpoint.id=raw.endpoint_id
                LEFT JOIN sites site ON site.id=endpoint.business_site_id
                WHERE {clause} ORDER BY raw.endpoint_id,raw.received_at,raw.id""",
            values,
        ).fetchall()
    grouped: dict[tuple[str, object], dict[str, Any]] = {}
    exception_categories: Counter[str] = Counter()
    for row in rows:
        endpoint_id = int(row["endpoint_id"]) if row["endpoint_id"] is not None else None
        station_code = str(row["endpoint_station_code"] or row["raw_station_code"] or f"unidentified-raw-{row['id']}")
        group_key = ("endpoint", endpoint_id) if endpoint_id is not None else ("station_code", station_code)
        item = grouped.setdefault(group_key, {
            "endpoint_id": endpoint_id, "business_site_id": row["business_site_id"],
            "station_name": row["station_name"], "station_code": station_code,
            "raw_frame_count": 0, "valid_cn2011_count": 0, "valid_cn2061_count": 0,
            "valid_observation_count": 0, "authentication_results": Counter(),
            "parse_results": Counter(), "protocol_codes": set(), "quality_flags": Counter(), "received_at": [],
        })
        item["raw_frame_count"] += 1
        item["authentication_results"][row["authentication_status"]] += 1
        parse_result, codes, qualities = _parse_evidence(bytes(row["raw_frame"] or b""))
        item["parse_results"][parse_result] += 1
        item["protocol_codes"].update(codes)
        item["quality_flags"].update(qualities)
        item["received_at"].append(str(row["received_at"]))
        if parse_result in {"valid_cn2011", "valid_cn2061"} and row["authentication_status"] == "authenticated":
            item[f"{parse_result}_count"] += 1
            item["valid_observation_count"] += 1
        if row["authentication_status"] != "authenticated":
            exception_categories[f"authentication:{row['authentication_status']}"] += 1
        if parse_result not in {"valid_cn2011", "valid_cn2061"}:
            exception_categories[f"parse:{parse_result}"] += 1
    stations = []
    for item in grouped.values():
        timestamps = [datetime.fromisoformat(value) for value in item.pop("received_at")]
        intervals = sorted(
            int((right - left).total_seconds()) for left, right in zip(timestamps, timestamps[1:])
            if (right - left).total_seconds() >= 0
        )
        station = {
            "endpoint_id": item["endpoint_id"], "business_site_id": item["business_site_id"],
            "station_name": item["station_name"], "station_code": item["station_code"],
            "communicating": bool(timestamps), "raw_frame_count": item["raw_frame_count"],
            "valid_cn2011_count": item["valid_cn2011_count"],
            "valid_cn2061_count": item["valid_cn2061_count"],
            "valid_observation_count": item["valid_observation_count"],
            "authentication_results": dict(sorted(item["authentication_results"].items())),
            "parse_results": dict(sorted(item["parse_results"].items())),
            "protocol_codes": sorted(item["protocol_codes"]),
            "quality_flags": dict(sorted(item["quality_flags"].items())),
            "first_received_at": timestamps[0].replace(microsecond=0).isoformat() if timestamps else None,
            "last_received_at": timestamps[-1].replace(microsecond=0).isoformat() if timestamps else None,
            # Observed receipt timing is evidence for B2, never an approved monitoring interval.
            "observed_receive_intervals_seconds": {
                "count": len(intervals), "minimum": intervals[0] if intervals else None,
                "median": intervals[len(intervals) // 2] if intervals else None,
                "maximum": intervals[-1] if intervals else None,
            },
        }
        stations.append(station)
    report = {"report_type": "hj212_b1_discovery", "selection": selection, "stations": stations}
    summary = {
        "result": "discovered", "station_count": len(stations), "raw_frame_count": len(rows),
        "exception_categories": dict(sorted(exception_categories.items())),
    }
    return report, summary


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def write_restricted_report(report: dict[str, object], output: Path) -> None:
    """Create one non-overwritable, owner-only report outside this repository."""
    output = Path(output).resolve()
    repository = Path(__file__).resolve().parents[1]
    if _is_within(output, repository):
        raise DiscoveryError("discovery report must be written outside the repository")
    if output.exists() or not output.parent.is_dir():
        raise DiscoveryError("discovery report path must be a new file in an existing directory")
    if os.name != "nt" and stat.S_IMODE(output.parent.stat().st_mode) & 0o022:
        raise DiscoveryError("discovery report directory has group or other write permission")
    descriptor = os.open(str(output), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            stream.write("\n")
        os.chmod(output, 0o600)
        if os.name == "nt":  # Windows does not expose DACLs through stat mode bits.
            account = os.environ.get("USERNAME")
            if not account:
                raise DiscoveryError("discovery report owner is unavailable")
            result = subprocess.run(
                ["icacls", str(output), "/inheritance:r", "/grant:r", f"{account}:(R,W)"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
            if result.returncode != 0:
                raise DiscoveryError("discovery report ACL could not be restricted")
        elif stat.S_IMODE(output.stat().st_mode) & 0o077:
            raise DiscoveryError("discovery report path has group or other permissions")
    except Exception:
        try:
            output.unlink()
        except OSError:
            pass
        raise


def replay_hj212(
    database: Path, *, endpoint_id: int, start_id: int | None = None, end_id: int | None = None,
    received_from: str | None = None, received_to: str | None = None,
) -> dict[str, object]:
    """Explicitly replay a bounded B1 range after B2 configuration is approved."""
    if isinstance(endpoint_id, bool) or not isinstance(endpoint_id, int) or endpoint_id <= 0:
        raise DiscoveryError("endpoint ID must be a positive integer")
    database = _existing_database(database)
    clause, values, selection = _selection(
        "protocol.protocol_family='hj212' AND raw.authentication_status IN ('authenticated','unbound_authenticated') AND raw.endpoint_id=?",
        start_id, end_id, received_from, received_to,
    )
    with closing(sqlite3.connect(str(database))) as connection:
        connection.row_factory = sqlite3.Row
        endpoint = connection.execute(
            "SELECT id,business_site_id,enabled,endpoint_state FROM trusted_endpoints WHERE id=?", (endpoint_id,)
        ).fetchone()
        if (endpoint is None or not endpoint["enabled"] or endpoint["endpoint_state"] != "bound"
                or endpoint["business_site_id"] is None):
            raise DiscoveryError("replay endpoint is unavailable")
        rows = connection.execute(
            f"""SELECT raw.id,raw.raw_frame FROM ingest_raw_frames raw
                JOIN ingest_frame_protocols protocol ON protocol.raw_frame_id=raw.id
                WHERE {clause} ORDER BY raw.id LIMIT ?""", [endpoint_id, *values, MAX_REPLAY_ROWS + 1],
        ).fetchall()
        profiles = connection.execute(
            """SELECT timezone,effective_from,effective_to FROM monitoring_endpoint_profiles
               WHERE endpoint_id=? AND enabled=1 ORDER BY effective_from""", (endpoint_id,)
        ).fetchall()
        mappings = connection.execute(
            """SELECT protocol_code,effective_from,effective_to FROM monitoring_factor_mappings
               WHERE endpoint_id=? AND enabled=1""", (endpoint_id,)
        ).fetchall()
    if len(rows) > MAX_REPLAY_ROWS:
        raise DiscoveryError("replay range exceeds the maximum row count")
    if not profiles:
        raise DiscoveryError("replay endpoint has no active monitoring profile")
    raw_ids: list[int] = []
    has_effective_mapping = False
    for row in rows:
        raw_id = int(row["id"])
        raw_frame = bytes(row["raw_frame"] or b"")
        parse_result, _, _ = _parse_evidence(bytes(raw_frame or b""))
        if parse_result not in {"valid_cn2011", "valid_cn2061"}:
            continue
        frame = parse_hj212_frame(raw_frame)
        effective_profiles = []
        for profile in profiles:
            try:
                observed_at = frame.data_time.replace(tzinfo=ZoneInfo(str(profile["timezone"]))).astimezone(timezone.utc).replace(microsecond=0).isoformat()
            except ZoneInfoNotFoundError as exc:
                raise DiscoveryError("replay endpoint profile has an invalid timezone") from exc
            if profile["effective_from"] <= observed_at and (profile["effective_to"] is None or profile["effective_to"] > observed_at):
                effective_profiles.append(observed_at)
        if len(effective_profiles) != 1:
            raise DiscoveryError("replay records are not covered by exactly one effective monitoring profile")
        observed_at = effective_profiles[0]
        if any(
            mapping["protocol_code"] == factor.protocol_code and mapping["effective_from"] <= observed_at
            and (mapping["effective_to"] is None or mapping["effective_to"] > observed_at)
            for factor in frame.factors for mapping in mappings
        ):
            has_effective_mapping = True
        raw_ids.append(raw_id)
    if not raw_ids:
        raise DiscoveryError("replay range has no authenticated approved HJ212 data records")
    if not has_effective_mapping:
        raise DiscoveryError("replay range has no effective factor mapping")
    outcomes: Counter[str] = Counter()
    for raw_id in raw_ids:
        try:
            result = normalize_raw_frame(database, raw_id, allow_unbound_replay=True)
        except Exception as exc:
            outcomes[f"failed:{type(exc).__name__}"] += 1
        else:
            outcomes[result] += 1
    return {
        "result": "replayed" if not any(key.startswith("failed:") or key == "waiting_reparse" for key in outcomes) else "attention_required",
        "endpoint_id": endpoint_id, "selection": selection, "selected_raw_frames": len(raw_ids),
        "outcomes": dict(sorted(outcomes.items())),
    }


def _private_pepper() -> str:
    pepper = os.environ.get("SL651_CREDENTIAL_PEPPER")
    if not pepper:
        raise DiscoveryError("SL651_CREDENTIAL_PEPPER is required in the private runtime environment")
    return pepper


def _unknown_hj212_plan(
    connection: sqlite3.Connection, *, endpoint_id: int, start_id: int | None, end_id: int | None,
    received_from: str | None, received_to: str | None,
) -> dict[str, object]:
    endpoint = connection.execute(
        """SELECT id,station_code,credential_hmac,business_site_id,enabled,endpoint_state
           FROM trusted_endpoints WHERE id=?""", (endpoint_id,)
    ).fetchone()
    if (endpoint is None or not endpoint["enabled"] or endpoint["endpoint_state"] != "bound"
            or endpoint["business_site_id"] is None):
        raise DiscoveryError("reauthentication endpoint is unavailable")
    clause, values, selection = _selection(
        """protocol.protocol_family='hj212' AND raw.authentication_status='unknown_endpoint'
           AND raw.endpoint_id IS NULL AND raw.station_code=?""",
        start_id, end_id, received_from, received_to,
    )
    rows = connection.execute(
        f"""SELECT raw.id,raw.frame_sha256,raw.logical_key_sha256,raw.raw_frame
            FROM ingest_raw_frames raw
            JOIN ingest_frame_protocols protocol ON protocol.raw_frame_id=raw.id
            WHERE {clause} ORDER BY raw.id LIMIT ?""",
        [endpoint["station_code"], *values, MAX_REAUTH_ROWS + 1],
    ).fetchall()
    if len(rows) > MAX_REAUTH_ROWS:
        raise DiscoveryError("reauthentication range exceeds the maximum row count")
    eligible = []
    rejected = Counter()
    for row in rows:
        try:
            frame = parse_hj212_frame(bytes(row["raw_frame"] or b""))
        except FrameError as exc:
            rejected[exc.code] += 1
            continue
        if frame.station_code != endpoint["station_code"]:
            rejected["station_code_mismatch"] += 1
            continue
        eligible.append({
            "raw_id": int(row["id"]), "frame_sha256": str(row["frame_sha256"]),
            "logical_key_sha256": row["logical_key_sha256"],
        })
    lock = {
        "endpoint_id": endpoint_id, "selection": selection,
        "eligible": eligible, "rejected": dict(sorted(rejected.items())),
    }
    encoded = json.dumps(lock, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "action": "reauth-plan",
        "result": "ready" if eligible and not rejected else "conflicted" if rejected else "not_ready",
        "endpoint_id": endpoint_id, "selection": selection,
        "eligible_count": len(eligible), "rejected_count": sum(rejected.values()),
        "rejected_categories": dict(sorted(rejected.items())),
        "fingerprint": "sha256:" + hashlib.sha256(encoded).hexdigest()[:16],
        "_eligible": eligible, "_credential_hmac": endpoint["credential_hmac"],
    }


def _reauth_public(plan: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in plan.items() if not key.startswith("_")}


def preview_unknown_hj212_reauthentication(
    database: Path, *, endpoint_id: int, start_id: int | None = None, end_id: int | None = None,
    received_from: str | None = None, received_to: str | None = None,
) -> dict[str, object]:
    database = _existing_database(database)
    with closing(sqlite3.connect(str(database))) as connection:
        connection.row_factory = sqlite3.Row
        return _reauth_public(_unknown_hj212_plan(
            connection, endpoint_id=endpoint_id, start_id=start_id, end_id=end_id,
            received_from=received_from, received_to=received_to,
        ))


def apply_unknown_hj212_reauthentication(
    database: Path, *, endpoint_id: int, credential: bytes, expected_fingerprint: str,
    offline_confirmed: bool, start_id: int | None = None, end_id: int | None = None,
    received_from: str | None = None, received_to: str | None = None,
) -> dict[str, object]:
    if not offline_confirmed or not expected_fingerprint:
        raise DiscoveryError("reauthentication apply requires offline confirmation and preview fingerprint")
    if not credential or len(credential) > 128 or not credential.isascii():
        raise DiscoveryError("HJ212 credential must be non-empty ASCII text")
    database = _existing_database(database)
    with closing(sqlite3.connect(str(database), timeout=5, isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            connection.execute("BEGIN IMMEDIATE")
            plan = _unknown_hj212_plan(
                connection, endpoint_id=endpoint_id, start_id=start_id, end_id=end_id,
                received_from=received_from, received_to=received_to,
            )
            if plan["fingerprint"] != expected_fingerprint:
                raise DiscoveryError("reauthentication selection changed after preview")
            if plan["result"] != "ready":
                raise DiscoveryError("reauthentication selection is not ready")
            expected_hmac = credential_hmac(credential, _private_pepper())
            if not hmac.compare_digest(str(plan["_credential_hmac"]), expected_hmac):
                raise DiscoveryError("reauthentication credential differs from the endpoint")
            raw_rows = []
            for item in plan["_eligible"]:
                row = connection.execute(
                    "SELECT id,raw_frame,logical_key_sha256 FROM ingest_raw_frames WHERE id=?",
                    (item["raw_id"],),
                ).fetchone()
                frame = parse_hj212_frame(bytes(row["raw_frame"] or b""))
                if not hmac.compare_digest(credential_hmac(frame.password, _private_pepper()), expected_hmac):
                    raise DiscoveryError("reauthentication range contains a credential mismatch")
                raw_rows.append(row)
            duplicate_count = 0
            for row in raw_rows:
                original = connection.execute(
                    """SELECT id FROM ingest_raw_frames
                       WHERE endpoint_id=? AND logical_key_sha256=? AND duplicate_of_raw_frame_id IS NULL
                       ORDER BY id LIMIT 1""",
                    (endpoint_id, row["logical_key_sha256"]),
                ).fetchone()
                duplicate_of = int(original["id"]) if original else None
                connection.execute(
                    """UPDATE ingest_raw_frames
                       SET endpoint_id=?,authentication_status='authenticated',
                           disposition=?,duplicate_of_raw_frame_id=?
                       WHERE id=? AND endpoint_id IS NULL AND authentication_status='unknown_endpoint'""",
                    (endpoint_id, "duplicate" if duplicate_of else "quarantined", duplicate_of, row["id"]),
                )
                if connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise DiscoveryError("reauthentication row changed while applying")
                duplicate_count += int(duplicate_of is not None)
                connection.execute(
                    """UPDATE ingest_errors SET status='resolved'
                       WHERE raw_frame_id=? AND error_type='unknown_endpoint' AND status='open'""",
                    (row["id"],),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    outcome = _reauth_public(plan)
    outcome.update({
        "action": "reauth-apply", "result": "applied",
        "authenticated_count": len(raw_rows), "duplicate_count": duplicate_count,
    })
    return outcome


def verify_unknown_hj212_reauthentication(
    database: Path, *, endpoint_id: int, start_id: int | None = None, end_id: int | None = None,
    received_from: str | None = None, received_to: str | None = None,
) -> dict[str, object]:
    database = _existing_database(database)
    with closing(sqlite3.connect(str(database))) as connection:
        endpoint = connection.execute(
            "SELECT station_code FROM trusted_endpoints WHERE id=?", (endpoint_id,)
        ).fetchone()
    if endpoint is None:
        raise DiscoveryError("reauthentication endpoint is unavailable during verification")
    clause, values, selection = _selection(
        "protocol.protocol_family='hj212' AND raw.station_code=?",
        start_id, end_id, received_from, received_to,
    )
    with closing(sqlite3.connect(str(database))) as connection:
        rows = connection.execute(
            f"""SELECT raw.endpoint_id,raw.authentication_status,COUNT(*)
                FROM ingest_raw_frames raw
                JOIN ingest_frame_protocols protocol ON protocol.raw_frame_id=raw.id
                WHERE {clause} GROUP BY raw.endpoint_id,raw.authentication_status""",
            [endpoint[0], *values],
        ).fetchall()
    if any(row[0] != endpoint_id or row[1] != "authenticated" for row in rows):
        raise DiscoveryError("reauthentication verification found non-authenticated rows")
    return {
        "action": "reauth-verify", "result": "verified", "endpoint_id": endpoint_id,
        "selection": selection, "authenticated_count": sum(row[2] for row in rows),
    }


def _read_hj212_credential() -> bytes:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    value = stream.readline()
    if isinstance(value, str):
        value = value.encode("utf-8")
    credential = bytes(value).rstrip(b"\r\n")
    if not credential or len(credential) > 128 or not credential.isascii():
        raise DiscoveryError("credential stdin must contain non-empty ASCII text")
    return credential


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline HJ212 B1 discovery and B2 replay")
    parser.add_argument("action", choices=("discover", "replay", "reauth-plan", "reauth-apply", "reauth-verify"))
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--endpoint-id", type=int)
    parser.add_argument("--start-id", type=int)
    parser.add_argument("--end-id", type=int)
    parser.add_argument("--received-from")
    parser.add_argument("--received-to")
    parser.add_argument("--expected-fingerprint")
    parser.add_argument("--offline-confirmation", action="store_true")
    parser.add_argument("--credential-stdin", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        if arguments.action == "discover":
            if arguments.output is None or arguments.endpoint_id is not None:
                raise DiscoveryError("discover requires --output and does not accept --endpoint-id")
            report, summary = discover_hj212(
                arguments.database, start_id=arguments.start_id, end_id=arguments.end_id,
                received_from=arguments.received_from, received_to=arguments.received_to,
            )
            write_restricted_report(report, arguments.output)
            print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
        elif arguments.action == "replay":
            if arguments.output is not None or arguments.endpoint_id is None:
                raise DiscoveryError("replay requires --endpoint-id and does not accept --output")
            outcome = replay_hj212(
                arguments.database, endpoint_id=arguments.endpoint_id, start_id=arguments.start_id,
                end_id=arguments.end_id, received_from=arguments.received_from, received_to=arguments.received_to,
            )
            print(json.dumps(outcome, ensure_ascii=True, sort_keys=True))
            if outcome["result"] != "replayed":
                return 2
        elif arguments.action == "reauth-plan":
            if arguments.output is not None or arguments.endpoint_id is None:
                raise DiscoveryError("reauth-plan requires --endpoint-id and does not accept --output")
            outcome = preview_unknown_hj212_reauthentication(
                arguments.database, endpoint_id=arguments.endpoint_id,
                start_id=arguments.start_id, end_id=arguments.end_id,
                received_from=arguments.received_from, received_to=arguments.received_to,
            )
            print(json.dumps(outcome, ensure_ascii=True, sort_keys=True))
            if outcome["result"] != "ready":
                return 2
        elif arguments.action == "reauth-apply":
            if (arguments.output is not None or arguments.endpoint_id is None
                    or not arguments.offline_confirmation or not arguments.credential_stdin
                    or not arguments.expected_fingerprint):
                raise DiscoveryError(
                    "reauth-apply requires endpoint, offline confirmation, credential stdin, and preview fingerprint")
            outcome = apply_unknown_hj212_reauthentication(
                arguments.database, endpoint_id=arguments.endpoint_id,
                credential=_read_hj212_credential(), expected_fingerprint=arguments.expected_fingerprint,
                offline_confirmed=True, start_id=arguments.start_id, end_id=arguments.end_id,
                received_from=arguments.received_from, received_to=arguments.received_to,
            )
            print(json.dumps(outcome, ensure_ascii=True, sort_keys=True))
        else:
            if arguments.output is not None or arguments.endpoint_id is None:
                raise DiscoveryError("reauth-verify requires --endpoint-id and does not accept --output")
            outcome = verify_unknown_hj212_reauthentication(
                arguments.database, endpoint_id=arguments.endpoint_id,
                start_id=arguments.start_id, end_id=arguments.end_id,
                received_from=arguments.received_from, received_to=arguments.received_to,
            )
            print(json.dumps(outcome, ensure_ascii=True, sort_keys=True))
    except DiscoveryError as exc:
        print(f"station ingestion discovery failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
