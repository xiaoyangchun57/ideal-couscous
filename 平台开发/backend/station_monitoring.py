"""Append-only normalization and read-only projections for station monitoring."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from .sl651_parser import FrameError, PARSER_VERSION, parse_frame, parse_water_quality_report
    from .hj212_parser import HJ212_PARSER_VERSION, ParsedHJ212Frame, parse_hj212_frame
except ImportError:  # pragma: no cover
    from sl651_parser import FrameError, PARSER_VERSION, parse_frame, parse_water_quality_report
    from hj212_parser import HJ212_PARSER_VERSION, ParsedHJ212Frame, parse_hj212_frame

NORMALIZATION_VERSION = "station-monitoring-normalizer-v4"
BUSINESS_PROJECTION_VERSION = "station-monitoring-business-v3"
HISTORICAL_REPROJECTION_VERSION = "station-monitoring-historical-business-v3"
_APPROVED_HJ212_2061_RTD_PROTOCOL_CODES = frozenset({"HJ212:w01019-Rtd"})


def is_formal_hj212_source(protocol_code: str, function_code: int | str | None,
                           source_value_kind: str | None) -> bool:
    """Keep the shared formal-source contract narrow and protocol-code aware."""
    return str(function_code) == "2061" and (
        source_value_kind == "avg"
        or (source_value_kind == "rtd"
            and str(protocol_code) in _APPROVED_HJ212_2061_RTD_PROTOCOL_CODES)
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _as_utc(value: datetime, timezone_name: str) -> str:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise FrameError("invalid_endpoint_timezone", "endpoint timezone is unavailable") from exc
    return value.replace(tzinfo=zone).astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _record_issue(connection, raw_id, batch_id, site_id, issue_type, summary, *, retryable=True):
    """One issue represents one raw business occurrence; retries never inflate it."""
    existing = connection.execute(
        """SELECT id FROM monitoring_quality_issues
           WHERE raw_frame_id IS ? AND issue_type=? AND object_summary=? AND status='open'""",
        (raw_id, issue_type, summary[:160]),
    ).fetchone()
    now = _utc_now()
    if existing:
        connection.execute("UPDATE monitoring_quality_issues SET last_seen_at=? WHERE id=?", (now, existing[0]))
    else:
        connection.execute(
            """INSERT INTO monitoring_quality_issues(raw_frame_id, observation_batch_id, business_site_id,
               issue_type, object_summary, first_seen_at, last_seen_at, reparse_allowed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (raw_id, batch_id, site_id, issue_type, summary[:160], now, now, int(retryable)),
        )


def _profile_for(connection, endpoint_id, at_time):
    return connection.execute(
        """SELECT * FROM monitoring_endpoint_profiles
           WHERE endpoint_id=? AND enabled=1 AND effective_from<=?
             AND (effective_to IS NULL OR effective_to>?)
           ORDER BY effective_from DESC LIMIT 1""",
        (endpoint_id, at_time, at_time),
    ).fetchone()


def _endpoint_timezone(connection, endpoint_id) -> str:
    """Resolve the endpoint's one unambiguous configured timezone before timestamps."""
    rows = connection.execute(
        "SELECT DISTINCT timezone FROM monitoring_endpoint_profiles WHERE endpoint_id=? AND enabled=1",
        (endpoint_id,),
    ).fetchall()
    if len(rows) != 1:
        raise FrameError("ambiguous_endpoint_timezone", "endpoint must have one configured timezone")
    return str(rows[0][0])


def _mapping_for(connection, endpoint_id, protocol_code, observed_at):
    rows = connection.execute(
        """SELECT * FROM monitoring_factor_mappings WHERE endpoint_id=? AND protocol_code=? AND enabled=1
           AND effective_from<=? AND (effective_to IS NULL OR effective_to>?)
           ORDER BY effective_from DESC""",
        (endpoint_id, protocol_code, observed_at, observed_at),
    ).fetchall()
    if len(rows) > 1:
        raise FrameError("ambiguous_factor_mapping", "more than one factor mapping is effective")
    return rows[0] if rows else None


def _business_schedule_for(connection, endpoint_id: int, protocol_code: str, at_time: str):
    """Resolve a factor override before the endpoint default for one observation."""
    rows = connection.execute(
        """SELECT * FROM monitoring_business_schedules
           WHERE endpoint_id=? AND enabled=1 AND (protocol_code=? OR protocol_code IS NULL)
             AND effective_from<=? AND (effective_to IS NULL OR effective_to>?)
           ORDER BY CASE WHEN protocol_code=? THEN 0 ELSE 1 END, effective_from DESC""",
        (endpoint_id, protocol_code, at_time, at_time, protocol_code),
    ).fetchall()
    return rows[0] if rows else None


def _schedule_integer(schedule, key: str, default: int = 0) -> int:
    try:
        return int(schedule[key]) if key in schedule.keys() else default
    except AttributeError:
        return int(schedule.get(key, default))


def _scheduled_at(schedule, observed_at: str, function_code: int | None = None) -> str | None:
    """Resolve sample time from an exact sample timestamp or a delayed result window."""
    try:
        zone = ZoneInfo(schedule["timezone"])
    except ZoneInfoNotFoundError as exc:
        raise FrameError("invalid_business_schedule_timezone", "business schedule timezone is unavailable") from exc
    observed = datetime.fromisoformat(observed_at).astimezone(zone)
    anchor = time.fromisoformat(schedule["anchor_local_time"])
    observed_seconds = observed.hour * 3600 + observed.minute * 60 + observed.second
    anchor_seconds = anchor.hour * 3600 + anchor.minute * 60 + anchor.second
    interval = int(schedule["interval_seconds"])
    result_delay = _schedule_integer(schedule, "result_delay_seconds")
    if function_code == 2011 and result_delay:
        reference = observed - timedelta(seconds=result_delay)
        local_anchor = datetime.combine(reference.date(), anchor, zone)
        if reference < local_anchor:
            local_anchor -= timedelta(days=1)
        elapsed = int((reference - local_anchor).total_seconds())
        slot = local_anchor + timedelta(seconds=(elapsed // interval) * interval)
        return slot.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    if observed.microsecond or (observed_seconds - anchor_seconds) % interval:
        return None
    return observed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def business_slot_deadline(configuration: dict, scheduled_at: str) -> str:
    """Return when an empty slot is finally missing rather than still awaiting analysis."""
    scheduled = datetime.fromisoformat(scheduled_at)
    delay = int(configuration.get("result_delay_seconds") or 0)
    if delay:
        seconds = delay + int(configuration.get("tolerance_seconds") or 0)
    else:
        seconds = int(configuration.get("tolerance_seconds") or 0)
    return (scheduled + timedelta(seconds=seconds)).isoformat()


def _protocol_business_priority(protocol_code: str) -> tuple[int, str]:
    if protocol_code == "HJ212:w01009":
        return (0, protocol_code)
    if protocol_code == "HJ212:005":
        return (1, protocol_code)
    return (2, protocol_code)


def _reconcile_business_slot(connection, endpoint_id: int, business_metric: str,
                             instrument_asset_code: str | None,
                             scheduled_at: str) -> None:
    rows = connection.execute(
        """SELECT id,protocol_code,standard_value,standard_unit,quality
           FROM monitoring_business_observations
           WHERE endpoint_id=? AND business_metric=?
             AND instrument_asset_code IS ? AND scheduled_at=? AND is_current=1
           ORDER BY id""",
        (endpoint_id, business_metric, instrument_asset_code, scheduled_at),
    ).fetchall()
    valid_rows = [row for row in rows if row["quality"] == "valid"]
    candidates = valid_rows or rows
    distinct_values = {(row["standard_value"], row["standard_unit"]) for row in candidates}
    if rows:
        connection.execute(
            "UPDATE monitoring_business_observations SET slot_state='duplicate' WHERE id IN (%s)" %
            ",".join("?" * len(rows)), [row["id"] for row in rows])
    if len(distinct_values) > 1:
        connection.execute(
            "UPDATE monitoring_business_observations SET slot_state='conflict' WHERE id IN (%s)" %
            ",".join("?" * len(candidates)), [row["id"] for row in candidates])
    elif candidates:
        selected = min(candidates, key=lambda row: (*_protocol_business_priority(row["protocol_code"]), row["id"]))
        connection.execute(
            "UPDATE monitoring_business_observations SET slot_state='selected' WHERE id=?", (selected["id"],))


def _slot_rows(connection, site_id: int, configuration: dict, scheduled_at: str):
    protocol_codes = configuration.get("protocol_codes") or [configuration["protocol_code"]]
    marks = ",".join("?" * len(protocol_codes))
    return connection.execute(
        f"""SELECT business.business_metric,business.protocol_code,business.standard_value,
                   business.standard_unit,business.quality,business.instrument_asset_code,
                   business.endpoint_id,business.scheduled_at,business.observed_at,
                   business.received_at,business.timeliness,business.slot_state,
                   business.projection_version,
                   batch.function_code
            FROM monitoring_business_observations business
            JOIN observation_batches batch ON batch.id=business.observation_batch_id
            WHERE business.business_site_id=? AND business.endpoint_id=?
              AND business.is_current=1 AND batch.is_current=1
              AND business.protocol_code IN ({marks}) AND business.business_metric=?
              AND business.instrument_asset_code IS ? AND business.scheduled_at=?
            ORDER BY business.id""",
        (site_id, configuration["endpoint_id"], *protocol_codes,
         configuration["business_metric"], configuration["instrument_asset_code"], scheduled_at),
    ).fetchall()


def _deactivate_business_observations(connection, batch_id: int) -> None:
    slots = connection.execute(
        """SELECT DISTINCT endpoint_id,protocol_code,business_metric,instrument_asset_code,scheduled_at
           FROM monitoring_business_observations WHERE observation_batch_id=? AND is_current=1""",
        (batch_id,),
    ).fetchall()
    connection.execute(
        "UPDATE monitoring_business_observations SET is_current=0 WHERE observation_batch_id=?", (batch_id,))
    for slot in slots:
        _reconcile_business_slot(connection, slot["endpoint_id"], slot["business_metric"],
                                 slot["instrument_asset_code"], slot["scheduled_at"])


def _project_business_observation(connection, *, value_id: int, batch_id: int, endpoint_id: int,
                                  site_id: int, protocol_code: str, business_metric: str,
                                  instrument_asset_code: str | None, observed_at: str,
                                  received_at: str, standard_value: float, standard_unit: str,
                                  quality: str, function_code: int | None = None,
                                  source_value_kind: str | None = None) -> bool:
    if (str(protocol_code).startswith("HJ212:") and function_code == 2061
            and not is_formal_hj212_source(
                protocol_code, function_code, source_value_kind)):
        return False
    schedule = _business_schedule_for(connection, endpoint_id, protocol_code, observed_at)
    if not schedule:
        return False
    scheduled_at = _scheduled_at(schedule, observed_at, function_code)
    if scheduled_at is None:
        return False
    received = datetime.fromisoformat(received_at)
    observed = datetime.fromisoformat(observed_at)
    result_delay = _schedule_integer(schedule, "result_delay_seconds")
    if function_code in {2011, 2061} and result_delay:
        grace = (int(schedule["tolerance_seconds"]) if function_code == 2061
                 else int(schedule["interval_seconds"]))
        deadline = datetime.fromisoformat(scheduled_at) + timedelta(
            seconds=result_delay + grace)
        timeliness = "late" if received >= deadline else "on_time"
    else:
        timeliness = "late" if received - observed > timedelta(seconds=int(schedule["tolerance_seconds"])) else "on_time"
    connection.execute(
        """INSERT INTO monitoring_business_observations(
               source_observation_value_id,observation_batch_id,schedule_id,endpoint_id,business_site_id,
               protocol_code,business_metric,instrument_asset_code,scheduled_at,observed_at,received_at,
               standard_value,standard_unit,quality,timeliness,slot_state,projection_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (value_id, batch_id, schedule["id"], endpoint_id, site_id, protocol_code, business_metric,
         instrument_asset_code, scheduled_at, observed_at, received_at, standard_value, standard_unit,
         quality, timeliness, "duplicate", BUSINESS_PROJECTION_VERSION),
    )
    _reconcile_business_slot(connection, endpoint_id, business_metric, instrument_asset_code, scheduled_at)
    return True


def _project_missing_business_observations(database: Path) -> tuple[int, int]:
    """Project current normalized values that became eligible after schedule configuration."""
    with closing(sqlite3.connect(str(database), timeout=5)) as connection:
        value_ids = [row[0] for row in connection.execute(
            """SELECT value.id
               FROM observation_values value
               JOIN observation_batches batch ON batch.id=value.observation_batch_id
               LEFT JOIN monitoring_business_observations business
                 ON business.source_observation_value_id=value.id
               WHERE value.is_current=1 AND value.is_published=1
                 AND value.quality IN ('valid','suspect') AND batch.is_current=1
                 AND batch.projection_state='completed' AND business.id IS NULL
               ORDER BY value.id"""
        )]
    projected = 0
    deferred = 0
    for value_id in value_ids:
        try:
            with closing(sqlite3.connect(str(database), timeout=5, isolation_level=None)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=5000")
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """SELECT value.id AS value_id,value.observation_batch_id,value.protocol_code,
                               value.business_metric,value.instrument_asset_code,value.standard_value,
                               value.standard_unit,value.quality,value.parser_version,
                               batch.raw_frame_id,batch.endpoint_id,
                               batch.business_site_id,batch.observed_at,batch.received_at,batch.function_code
                       FROM observation_values value
                       JOIN observation_batches batch ON batch.id=value.observation_batch_id
                       LEFT JOIN monitoring_business_observations business
                         ON business.source_observation_value_id=value.id
                       WHERE value.id=? AND value.is_current=1 AND value.is_published=1
                         AND value.quality IN ('valid','suspect') AND batch.is_current=1
                         AND batch.projection_state='completed' AND business.id IS NULL""",
                    (value_id,),
                ).fetchone()
                if row:
                    source_value_kind = None
                    if str(row["protocol_code"]).startswith("HJ212:"):
                        prefix = f"{HJ212_PARSER_VERSION}:"
                        if str(row["parser_version"]).startswith(prefix):
                            candidate_kind = str(row["parser_version"])[len(prefix):]
                            if candidate_kind in {"avg", "rtd"}:
                                source_value_kind = candidate_kind
                    was_projected = _project_business_observation(
                        connection, value_id=row["value_id"], batch_id=row["observation_batch_id"],
                        endpoint_id=row["endpoint_id"], site_id=row["business_site_id"],
                        protocol_code=row["protocol_code"], business_metric=row["business_metric"],
                        instrument_asset_code=row["instrument_asset_code"], observed_at=row["observed_at"],
                        received_at=row["received_at"], standard_value=row["standard_value"],
                        standard_unit=row["standard_unit"], quality=row["quality"],
                        function_code=row["function_code"], source_value_kind=source_value_kind)
                    if was_projected:
                        projected += 1
                    elif (is_formal_hj212_source(
                            row["protocol_code"], row["function_code"], source_value_kind)
                          and _business_schedule_for(
                              connection, row["endpoint_id"], row["protocol_code"],
                              row["observed_at"])):
                        _record_issue(
                            connection, row["raw_frame_id"], row["observation_batch_id"],
                            row["business_site_id"], "business_time_outside_schedule", row["protocol_code"],
                            retryable=False,
                        )
                connection.commit()
        except (sqlite3.Error, OSError):
            deferred += 1
    return projected, deferred


def _mark_waiting(connection, raw_id, site_id, issue_type, summary):
    _record_issue(connection, raw_id, None, site_id, issue_type, summary, retryable=True)
    connection.execute(
        "UPDATE ingest_raw_frames SET disposition='quarantined', persistence_state='persisted' WHERE id=?", (raw_id,)
    )
    connection.execute("DELETE FROM monitoring_normalization_retries WHERE raw_frame_id=?", (raw_id,))
    connection.commit()
    return "waiting_reparse"


def normalize_raw_frame(
    database: Path, raw_id: int, *, normalization_version: str = NORMALIZATION_VERSION,
    allow_unbound_replay: bool = False,
) -> str:
    """Normalize one raw receipt and leave mapping/parser waits diagnostically stable."""
    with closing(sqlite3.connect(str(database), timeout=5, isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("BEGIN IMMEDIATE")
        raw = connection.execute("SELECT * FROM ingest_raw_frames WHERE id=?", (raw_id,)).fetchone()
        if not raw:
            connection.rollback()
            return "missing"
        existing = connection.execute(
            "SELECT projection_state FROM observation_batches WHERE raw_frame_id=? AND normalization_version=?",
            (raw_id, normalization_version),
        ).fetchone()
        if existing:
            if existing["projection_state"] == "failed_retryable":
                return _mark_waiting(connection, raw_id, None, "normalization_reparse_required", "existing retryable result needs a new parser version")
            connection.rollback()
            return "already_normalized"
        replayable_unbound = allow_unbound_replay and raw["authentication_status"] == "unbound_authenticated"
        if (raw["authentication_status"] != "authenticated" and not replayable_unbound) or raw["disposition"] == "duplicate":
            connection.rollback()
            return "not_projectable"
        try:
            protocol = connection.execute(
                "SELECT protocol_family FROM ingest_frame_protocols WHERE raw_frame_id=?", (raw_id,)
            ).fetchone()
            family = protocol[0] if protocol else "sl651"
            endpoint_timezone = _endpoint_timezone(connection, raw["endpoint_id"])
            if family == "hj212":
                frame = parse_hj212_frame(raw["raw_frame"])
                if frame.command not in {"2011", "2061"} or frame.data_time is None:
                    return _mark_waiting(connection, raw_id, None, "hj212_not_projectable", "HJ212 command has no projectable observation")
                report_factors = frame.factors
                reported_at = _as_utc(frame.data_time, endpoint_timezone)
                observed_at = _as_utc(frame.data_time, endpoint_timezone)
                function_code = int(frame.command)
                serial_number = 0
                parser_version = HJ212_PARSER_VERSION
            elif family == "sl651":
                frame = parse_frame(raw["raw_frame"])
                report = parse_water_quality_report(frame.payload)
                if report.station_code != frame.station_code:
                    return _mark_waiting(connection, raw_id, None, "payload_station_mismatch", "32H body station does not match frame header")
                report_factors = report.factors
                reported_at = _as_utc(frame.sent_at, endpoint_timezone)
                observed_at = _as_utc(report.observed_at, endpoint_timezone)
                function_code = frame.function_code
                serial_number = frame.serial_number
                parser_version = PARSER_VERSION
            else:
                return _mark_waiting(connection, raw_id, None, "unsupported_protocol_family", "raw receipt protocol family is unsupported")
        except FrameError as exc:
            return _mark_waiting(connection, raw_id, None, exc.code, "station body cannot be normalized by its protocol parser")
        profile = _profile_for(connection, raw["endpoint_id"], observed_at)
        if not profile:
            return _mark_waiting(connection, raw_id, None, "unmapped_endpoint", "endpoint profile is missing at observation time")
        site_id = profile["business_site_id"]
        previous = connection.execute("SELECT id FROM observation_batches WHERE raw_frame_id=? AND is_current=1", (raw_id,)).fetchone()
        if previous:
            connection.execute("UPDATE observation_batches SET is_current=0, projection_state='superseded' WHERE id=?", (previous[0],))
            connection.execute("UPDATE observation_values SET is_current=0 WHERE observation_batch_id=?", (previous[0],))
            _deactivate_business_observations(connection, previous[0])
        cursor = connection.execute(
            """INSERT INTO observation_batches(raw_frame_id, endpoint_id, business_site_id, function_code,
               serial_number, reported_at, observed_at, received_at, granularity, aggregation_source,
               idempotency_key, normalization_version, batch_status, projection_state, error_code,
               replaces_batch_id, normalized_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'device_reported', ?, ?, 'accepted', 'completed', NULL, ?, ?)""",
            (raw_id, raw["endpoint_id"], site_id, function_code, serial_number, reported_at, observed_at,
             raw["received_at"], profile["expected_granularity"] or "realtime", frame.logical_key,
             normalization_version, previous[0] if previous else None, _utc_now()),
        )
        batch_id = int(cursor.lastrowid)
        connection.execute(
            """INSERT INTO monitoring_status_events(observation_batch_id, endpoint_id, business_site_id,
               event_axis, event_type, event_value, occurred_at, received_at, source)
               VALUES (?, ?, ?, 'communication', 'frame_received', 'received', ?, ?, 'device_reported')""",
            (batch_id, raw["endpoint_id"], site_id, raw["received_at"], raw["received_at"]),
        )
        batch_status = "accepted"
        for factor in report_factors:
            source_value_kind = (getattr(factor, "value_kind", None)
                                 if family == "hj212" else None)
            value_parser_version = (f"{parser_version}:{source_value_kind}"
                                    if source_value_kind else parser_version)
            if factor.quality == "fault":
                batch_status = "partial"
                _record_issue(
                    connection, raw_id, batch_id, site_id,
                    getattr(factor, "issue_code", None) or "invalid_factor_value", factor.protocol_code,
                )
                continue
            definition = connection.execute("SELECT * FROM monitoring_factor_definitions WHERE protocol_code=?", (factor.protocol_code,)).fetchone()
            if not definition:
                batch_status = "partial"
                _record_issue(connection, raw_id, batch_id, site_id, "unmapped_factor", factor.protocol_code)
                continue
            connection.execute(
                """UPDATE monitoring_quality_issues SET status='resolved', last_seen_at=?
                   WHERE raw_frame_id=? AND issue_type='unmapped_factor' AND object_summary=? AND status='open'""",
                (_utc_now(), raw_id, factor.protocol_code),
            )
            try:
                mapping = _mapping_for(connection, raw["endpoint_id"], factor.protocol_code, observed_at)
            except FrameError as exc:
                batch_status = "partial"
                _record_issue(connection, raw_id, batch_id, site_id, exc.code, factor.protocol_code)
                continue
            if factor.factor in {"rtu_status", "power_voltage", "signal_strength"}:
                connection.execute(
                    """INSERT INTO monitoring_status_events(observation_batch_id, endpoint_id, business_site_id,
                       event_axis, event_type, event_value, occurred_at, received_at, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unconfirmed_protocol')""",
                    (batch_id, raw["endpoint_id"], site_id,
                     "rtu" if factor.factor == "rtu_status" else "power" if factor.factor == "power_voltage" else "signal",
                     factor.factor, str(factor.raw_value), observed_at, raw["received_at"]),
                )
                continue
            if not mapping:
                batch_status = "partial"
                _record_issue(connection, raw_id, batch_id, site_id, "unmapped_factor", factor.protocol_code)
                connection.execute(
                    """INSERT INTO observation_values(observation_batch_id, protocol_code, quality, parser_version, is_published)
                       VALUES (?, ?, 'unmapped', ?, 0)""",
                    (batch_id, factor.protocol_code, value_parser_version),
                )
                continue
            metric = mapping["business_metric"] or definition["business_metric"]
            published = bool(definition["is_published"] and metric)
            if factor.quality != "valid":
                batch_status = "partial"
                _record_issue(
                    connection, raw_id, batch_id, site_id,
                    getattr(factor, "issue_code", None) or "invalid_factor_value", factor.protocol_code,
                )
            value_cursor = connection.execute(
                """INSERT INTO observation_values(observation_batch_id, protocol_code, business_metric, raw_value,
                   raw_unit, standard_value, standard_unit, quality, instrument_asset_code, parser_version, is_published)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (batch_id, factor.protocol_code, metric, factor.raw_value, definition["raw_unit"], factor.raw_value,
                 definition["standard_unit"], factor.quality, mapping["instrument_asset_code"] or profile["instrument_asset_code"],
                  value_parser_version, int(published and factor.quality in {"valid", "suspect"})),
            )
            if published and factor.quality in {"valid", "suspect"}:
                projected = _project_business_observation(
                    connection, value_id=int(value_cursor.lastrowid), batch_id=batch_id,
                    endpoint_id=raw["endpoint_id"], site_id=site_id, protocol_code=factor.protocol_code,
                    business_metric=metric,
                    instrument_asset_code=mapping["instrument_asset_code"] or profile["instrument_asset_code"],
                    observed_at=observed_at, received_at=raw["received_at"],
                    standard_value=factor.raw_value, standard_unit=definition["standard_unit"],
                    quality=factor.quality, function_code=function_code,
                    source_value_kind=source_value_kind,
                )
                if (not projected and is_formal_hj212_source(
                        factor.protocol_code, function_code, source_value_kind)
                        and _business_schedule_for(
                            connection, raw["endpoint_id"], factor.protocol_code, observed_at)):
                    batch_status = "partial"
                    _record_issue(
                        connection, raw_id, batch_id, site_id,
                        "business_time_outside_schedule", factor.protocol_code, retryable=False,
                    )
        connection.execute("UPDATE observation_batches SET batch_status=? WHERE id=?", (batch_status, batch_id))
        connection.execute("UPDATE ingest_raw_frames SET disposition='accepted', persistence_state='persisted' WHERE id=?", (raw_id,))
        connection.execute("DELETE FROM monitoring_normalization_retries WHERE raw_frame_id=?", (raw_id,))
        connection.commit()
        return batch_status


def reproject_historical_business_observations(
    database: Path, *, source_normalization_version: str = NORMALIZATION_VERSION,
    target_normalization_version: str = HISTORICAL_REPROJECTION_VERSION,
) -> dict[str, int]:
    """Replay retained frames one transaction at a time; reruns resume idempotently."""
    with closing(sqlite3.connect(str(database), timeout=5)) as connection:
        raw_ids = [row[0] for row in connection.execute(
            """SELECT DISTINCT raw_frame_id FROM observation_batches
               WHERE normalization_version=? AND projection_state IN ('completed','superseded')
               ORDER BY raw_frame_id""",
            (source_normalization_version,),
        )]
    results: dict[str, int] = {"eligible": len(raw_ids), "reprojected": 0, "already_reprojected": 0, "deferred": 0}
    for raw_id in raw_ids:
        try:
            status = normalize_raw_frame(
                database, raw_id, normalization_version=target_normalization_version)
        except (sqlite3.Error, OSError):
            results["deferred"] += 1
            continue
        if status == "already_normalized":
            results["already_reprojected"] += 1
        elif status in {"accepted", "partial"}:
            results["reprojected"] += 1
        else:
            results["deferred"] += 1
    results["business_projected"], results["business_deferred"] = (
        _project_missing_business_observations(database))
    return results


def _business_schedule_from_rows(schedule_rows, endpoint_id: int, protocol_code: str, at_time: str):
    rows = [row for row in schedule_rows
            if row["endpoint_id"] == endpoint_id and row["enabled"] == 1
            and row["protocol_code"] in {None, protocol_code}
            and row["effective_from"] <= at_time
            and (row["effective_to"] is None or row["effective_to"] > at_time)]
    rows.sort(key=lambda row: (
        row["protocol_code"] == protocol_code, row["effective_from"], row["id"]), reverse=True)
    return rows[0] if rows else None


def _factor_configurations_from_rows(rows, schedule_rows, at_time: str):
    grouped = {}
    for row in rows:
        item = dict(row)
        item["_schedule_rows"] = schedule_rows
        key = (item["business_site_id"], item["endpoint_id"], item["business_metric"],
               item["instrument_asset_code"])
        grouped.setdefault(key, []).append(item)
    result = {}
    at_datetime = datetime.fromisoformat(at_time).astimezone(timezone.utc)
    for (site_id, _endpoint_id, _metric, _instrument), configurations in grouped.items():
        configurations.sort(key=lambda item: _protocol_business_priority(item["protocol_code"]))
        item = dict(configurations[0])
        item["protocol_codes"] = [entry["protocol_code"] for entry in configurations]
        item["protocol_configurations"] = configurations
        scheduled = [
            (entry, _business_schedule_from_rows(
                schedule_rows, entry["endpoint_id"], entry["protocol_code"], at_time))
            for entry in configurations
        ]
        scheduled = [(entry, schedule) for entry, schedule in scheduled if schedule]
        schedule = scheduled[0][1] if scheduled else None
        item.update({
            "schedule_id": schedule["id"] if schedule else None,
            "schedule_timezone": schedule["timezone"] if schedule else None,
            "schedule_anchor_local_time": schedule["anchor_local_time"] if schedule else None,
            "expected_interval_seconds": schedule["interval_seconds"] if schedule else None,
            "tolerance_seconds": schedule["tolerance_seconds"] if schedule else None,
            "result_delay_seconds": _schedule_integer(schedule, "result_delay_seconds") if schedule else None,
        })
        if schedule:
            future_slots = expected_business_slots(
                None, item, at_datetime.isoformat(),
                (at_datetime + timedelta(days=2)).isoformat(),
            )
            item["next_expected_at"] = next(
                (slot["scheduled_at"] for slot in future_slots
                 if datetime.fromisoformat(slot["scheduled_at"]) > at_datetime), None)
            interval = int(item["expected_interval_seconds"] or 0)
            delay = int(item["result_delay_seconds"] or 0)
            recent_slots = expected_business_slots(
                None, item,
                (at_datetime - timedelta(seconds=max(interval + delay, interval * 2))).isoformat(),
                (at_datetime + timedelta(seconds=max(interval, 1))).isoformat(),
            )
            due_slots = [slot for slot in recent_slots
                         if datetime.fromisoformat(slot["result_window_end_at"]) <= at_datetime]
            pending_slots = [slot for slot in recent_slots
                             if datetime.fromisoformat(slot["scheduled_at"]) <= at_datetime
                             < datetime.fromisoformat(slot["result_window_end_at"])]
            item["has_due_slot"] = bool(due_slots)
            item["pending_until"] = min(
                (slot["result_window_end_at"] for slot in pending_slots), default=None)
        else:
            item["next_expected_at"] = None
            item["has_due_slot"] = False
            item["pending_until"] = None
        result.setdefault(site_id, []).append(item)
    return result


def current_expected_factor_selections_batch(connection, site_ids, *, at_time: str | None = None):
    """Return the effective station-level business denominator and its version."""
    site_ids = list(dict.fromkeys(int(site_id) for site_id in site_ids))
    if not site_ids:
        return {}
    at_time = at_time or _utc_now()
    marks = ",".join("?" * len(site_ids))
    rows = connection.execute(
        f"""SELECT config.id AS config_id,config.site_id,config.version,
                   config.effective_from AS config_effective_from,
                   config.effective_to AS config_effective_to,
                   factor.business_metric,factor.display_order,
                   factor.effective_from AS factor_effective_from
            FROM station_expected_factor_configs config
            JOIN station_expected_factors factor ON factor.config_id=config.id
            WHERE config.site_id IN ({marks}) AND config.effective_from<=?
              AND (config.effective_to IS NULL OR config.effective_to>?)
            ORDER BY config.site_id,config.version DESC,factor.display_order,factor.business_metric""",
        [*site_ids, at_time, at_time],
    ).fetchall()
    result = {site_id: None for site_id in site_ids}
    for row in rows:
        selection = result.get(row["site_id"])
        if selection is None:
            selection = {
                "config_id": row["config_id"], "version": row["version"],
                "effective_from": row["config_effective_from"],
                "effective_to": row["config_effective_to"], "items": [],
            }
            result[row["site_id"]] = selection
        if selection["config_id"] != row["config_id"]:
            continue
        selection["items"].append({
            "business_metric": row["business_metric"],
            "display_order": row["display_order"],
            "effective_from": row["factor_effective_from"],
        })
    return result


def current_factor_configurations_batch(connection, site_ids, *, at_time: str | None = None,
                                        expected_selections=None):
    """Read mapped facts while keeping expected factors as the completeness contract.

    An unconfigured station must not lose already-normalized formal values.  In that
    case mappings remain readable, but none of them is marked as an expected factor,
    so cycle completeness stays unconfigured instead of treating every mapping as the
    denominator.
    """
    site_ids = list(dict.fromkeys(int(site_id) for site_id in site_ids))
    if not site_ids:
        return {}
    at_time = at_time or _utc_now()
    selections = (expected_selections if expected_selections is not None
                  else current_expected_factor_selections_batch(
                      connection, site_ids, at_time=at_time))
    marks = ",".join("?" * len(site_ids))
    profile_rows = connection.execute(
        f"""SELECT endpoint_id,business_site_id,instrument_asset_code,
                   effective_from AS profile_effective_from,
                   effective_to AS profile_effective_to
            FROM monitoring_endpoint_profiles
            WHERE business_site_id IN ({marks}) AND enabled=1
              AND effective_from<=? AND (effective_to IS NULL OR effective_to>?)
            ORDER BY business_site_id,effective_from DESC,endpoint_id""",
        [*site_ids, at_time, at_time],
    ).fetchall()
    rows = connection.execute(
        f"""SELECT profile.endpoint_id, profile.business_site_id, mapping.protocol_code,
                   COALESCE(mapping.business_metric, definition.business_metric) AS business_metric,
                   COALESCE(mapping.instrument_asset_code, profile.instrument_asset_code) AS instrument_asset_code,
                   definition.standard_unit, definition.decimal_precision,
                   mapping.expected_interval_seconds AS ingestion_interval_seconds,
                   mapping.tolerance_seconds AS ingestion_tolerance_seconds,
                   profile.effective_from AS profile_effective_from, profile.effective_to AS profile_effective_to,
                   mapping.effective_from AS mapping_effective_from, mapping.effective_to AS mapping_effective_to
            FROM monitoring_endpoint_profiles profile
            JOIN monitoring_factor_mappings mapping ON mapping.endpoint_id=profile.endpoint_id
            JOIN monitoring_factor_definitions definition ON definition.protocol_code=mapping.protocol_code
            WHERE profile.business_site_id IN ({marks}) AND profile.enabled=1
              AND profile.effective_from<=? AND (profile.effective_to IS NULL OR profile.effective_to>?)
              AND mapping.enabled=1 AND mapping.effective_from<=? AND (mapping.effective_to IS NULL OR mapping.effective_to>?)
              AND definition.is_published=1
              AND COALESCE(mapping.business_metric, definition.business_metric) IS NOT NULL
            ORDER BY profile.business_site_id,profile.endpoint_id,mapping.protocol_code,
                     mapping.effective_from DESC""",
        [*site_ids, at_time, at_time, at_time, at_time],
    ).fetchall()
    endpoint_ids = list(dict.fromkeys(int(row["endpoint_id"]) for row in profile_rows))
    if not endpoint_ids:
        return {site_id: [] for site_id in site_ids}
    endpoint_marks = ",".join("?" * len(endpoint_ids))
    schedule_rows = [dict(row) for row in connection.execute(
        f"""SELECT * FROM monitoring_business_schedules
            WHERE enabled=1 AND endpoint_id IN ({endpoint_marks})
            ORDER BY endpoint_id,effective_from,id""",
        endpoint_ids,
    ).fetchall()]
    mapped = _factor_configurations_from_rows(rows, schedule_rows, at_time)
    selected_metrics = sorted({
        item["business_metric"]
        for selection in selections.values() if selection
        for item in selection["items"]
    })
    definition_by_metric = {}
    if selected_metrics:
        metric_marks = ",".join("?" * len(selected_metrics))
        definition_rows = connection.execute(
            f"""SELECT business_metric,standard_unit,decimal_precision,protocol_code
                FROM monitoring_factor_definitions
                WHERE is_published=1 AND business_metric IN ({metric_marks})
                ORDER BY business_metric,protocol_code""",
            selected_metrics,
        ).fetchall()
        for row in definition_rows:
            definition_by_metric.setdefault(row["business_metric"], dict(row))
    profiles_by_site = {}
    for profile in profile_rows:
        profiles_by_site.setdefault(profile["business_site_id"], []).append(profile)
    evaluated_at = datetime.fromisoformat(at_time).astimezone(timezone.utc)
    result = {site_id: [] for site_id in site_ids}
    for site_id in site_ids:
        selection = selections.get(site_id)
        if not selection:
            for profile in profiles_by_site.get(site_id, []):
                existing = [
                    dict(item) for item in mapped.get(site_id, [])
                    if item["endpoint_id"] == profile["endpoint_id"]
                ]
                for item in existing:
                    item["expected_factor_config_id"] = None
                    item["expected_factor_version"] = 0
                result[site_id].extend(existing)
            continue
        expected_items = selection["items"]
        expected_by_metric = {item["business_metric"]: item for item in expected_items}
        order_by_metric = {item["business_metric"]: item["display_order"] for item in expected_items}
        for profile in profiles_by_site.get(site_id, []):
            endpoint_id = profile["endpoint_id"]
            existing = [
                dict(item) for item in mapped.get(site_id, [])
                if item["endpoint_id"] == endpoint_id
                and item["business_metric"] in expected_by_metric
            ]
            existing_metrics = {item["business_metric"] for item in existing}
            for item in existing:
                factor_selection = expected_by_metric[item["business_metric"]]
                item["mapping_effective_from"] = max(
                    item["mapping_effective_from"], factor_selection["effective_from"])
                if selection["effective_to"]:
                    current_to = item.get("mapping_effective_to")
                    item["mapping_effective_to"] = min(
                        value for value in (current_to, selection["effective_to"]) if value)
                item["expected_factor_config_id"] = selection["config_id"]
                item["expected_factor_version"] = selection["version"]
            for expected in expected_items:
                metric = expected["business_metric"]
                if metric in existing_metrics:
                    continue
                definition = definition_by_metric.get(metric, {})
                schedule = _business_schedule_from_rows(
                    schedule_rows, endpoint_id, None, at_time)
                placeholder = {
                    "endpoint_id": endpoint_id, "business_site_id": site_id,
                    "protocol_code": None, "protocol_codes": [],
                    "protocol_configurations": [], "business_metric": metric,
                    "instrument_asset_code": profile["instrument_asset_code"],
                    "standard_unit": definition.get("standard_unit"),
                    "decimal_precision": definition.get("decimal_precision"),
                    "ingestion_interval_seconds": None,
                    "ingestion_tolerance_seconds": None,
                    "profile_effective_from": profile["profile_effective_from"],
                    "profile_effective_to": profile["profile_effective_to"],
                    "mapping_effective_from": expected["effective_from"],
                    "mapping_effective_to": selection["effective_to"],
                    "_schedule_rows": schedule_rows,
                    "schedule_id": schedule["id"] if schedule else None,
                    "schedule_timezone": schedule["timezone"] if schedule else None,
                    "schedule_anchor_local_time": schedule["anchor_local_time"] if schedule else None,
                    "expected_interval_seconds": schedule["interval_seconds"] if schedule else None,
                    "tolerance_seconds": schedule["tolerance_seconds"] if schedule else None,
                    "result_delay_seconds": _schedule_integer(
                        schedule, "result_delay_seconds") if schedule else None,
                    "expected_factor_config_id": selection["config_id"],
                    "expected_factor_version": selection["version"],
                }
                if schedule:
                    interval = int(placeholder["expected_interval_seconds"] or 0)
                    delay = int(placeholder["result_delay_seconds"] or 0)
                    future_slots = expected_business_slots(
                        None, placeholder, evaluated_at.isoformat(),
                        (evaluated_at + timedelta(days=2)).isoformat())
                    placeholder["next_expected_at"] = next(
                        (slot["scheduled_at"] for slot in future_slots
                         if datetime.fromisoformat(slot["scheduled_at"]) > evaluated_at), None)
                    recent_slots = expected_business_slots(
                        None, placeholder,
                        (evaluated_at - timedelta(seconds=max(interval + delay, interval * 2))).isoformat(),
                        (evaluated_at + timedelta(seconds=max(interval, 1))).isoformat())
                    due_slots = [slot for slot in recent_slots
                                 if datetime.fromisoformat(slot["result_window_end_at"]) <= evaluated_at]
                    pending_slots = [slot for slot in recent_slots
                                     if datetime.fromisoformat(slot["scheduled_at"]) <= evaluated_at
                                     < datetime.fromisoformat(slot["result_window_end_at"])]
                    placeholder["has_due_slot"] = bool(due_slots)
                    placeholder["pending_until"] = min(
                        (slot["result_window_end_at"] for slot in pending_slots), default=None)
                else:
                    placeholder.update(next_expected_at=None, has_due_slot=False, pending_until=None)
                existing.append(placeholder)
            existing.sort(key=lambda item: (
                order_by_metric.get(item["business_metric"], 999),
                item["business_metric"], item.get("instrument_asset_code") or ""))
            result[site_id].extend(existing)
    return result


def current_factor_configurations(connection, site_id: int, *, at_time: str | None = None):
    """Return every currently published factor, including factors that never reported."""
    return current_factor_configurations_batch(
        connection, [site_id], at_time=at_time).get(site_id, [])


def expected_business_slots(connection, configuration: dict, start: str, end: str):
    """Generate effective exact business slots, including schedule period changes."""
    protocol_configurations = configuration.get("protocol_configurations") or []
    if protocol_configurations:
        slots = {}
        for protocol_configuration in protocol_configurations:
            for slot in expected_business_slots(connection, protocol_configuration, start, end):
                slots.setdefault(slot["scheduled_at"], slot)
        return [slots[key] for key in sorted(slots)]
    start_at = datetime.fromisoformat(start).astimezone(timezone.utc)
    end_at = datetime.fromisoformat(end).astimezone(timezone.utc)
    prefetched = configuration.get("_schedule_rows")
    if prefetched is None:
        rows = connection.execute(
            """SELECT * FROM monitoring_business_schedules
               WHERE endpoint_id=? AND enabled=1 AND (protocol_code=? OR protocol_code IS NULL)
                 AND effective_from<=? AND (effective_to IS NULL OR effective_to>?)
               ORDER BY effective_from,id""",
            (configuration["endpoint_id"], configuration["protocol_code"],
             end_at.isoformat(), start_at.isoformat()),
        ).fetchall()
    else:
        rows = [row for row in prefetched
                if row["endpoint_id"] == configuration["endpoint_id"] and row["enabled"] == 1
                and row["protocol_code"] in {None, configuration["protocol_code"]}
                and row["effective_from"] <= end_at.isoformat()
                and (row["effective_to"] is None or row["effective_to"] > start_at.isoformat())]
    slots = {}
    for schedule in rows:
        try:
            zone = ZoneInfo(schedule["timezone"])
        except ZoneInfoNotFoundError as exc:
            raise FrameError("invalid_business_schedule_timezone", "business schedule timezone is unavailable") from exc
        first_day = start_at.astimezone(zone).date() - timedelta(days=1)
        last_day = end_at.astimezone(zone).date() + timedelta(days=1)
        anchor = time.fromisoformat(schedule["anchor_local_time"])
        anchor_seconds = anchor.hour * 3600 + anchor.minute * 60 + anchor.second
        interval = int(schedule["interval_seconds"])
        local_day = first_day
        while local_day <= last_day:
            for second in range(anchor_seconds % interval, 24 * 3600, interval):
                local_slot = datetime.combine(local_day, time(), zone) + timedelta(seconds=second)
                utc_slot = local_slot.astimezone(timezone.utc).replace(microsecond=0)
                utc_text = utc_slot.isoformat()
                if not start_at <= utc_slot < end_at:
                    continue
                if utc_text < configuration["mapping_effective_from"] or (
                        configuration["mapping_effective_to"] and utc_text >= configuration["mapping_effective_to"]):
                    continue
                if utc_text < configuration["profile_effective_from"] or (
                        configuration["profile_effective_to"] and utc_text >= configuration["profile_effective_to"]):
                    continue
                effective = (_business_schedule_from_rows(
                    prefetched, configuration["endpoint_id"], configuration["protocol_code"], utc_text)
                    if prefetched is not None else _business_schedule_for(
                        connection, configuration["endpoint_id"], configuration["protocol_code"], utc_text))
                if effective and effective["id"] == schedule["id"]:
                    slots[utc_text] = {
                        "scheduled_at": utc_text, "schedule_id": schedule["id"],
                        "interval_seconds": interval, "tolerance_seconds": int(schedule["tolerance_seconds"]),
                        "result_delay_seconds": _schedule_integer(schedule, "result_delay_seconds"),
                        "timezone": schedule["timezone"],
                    }
                    slots[utc_text]["result_window_start_at"] = (
                        utc_slot + timedelta(seconds=slots[utc_text]["result_delay_seconds"])
                    ).isoformat()
                    slots[utc_text]["result_window_end_at"] = business_slot_deadline(
                        {
                            "expected_interval_seconds": interval,
                            "tolerance_seconds": slots[utc_text]["tolerance_seconds"],
                            "result_delay_seconds": slots[utc_text]["result_delay_seconds"],
                        }, utc_text)
            local_day += timedelta(days=1)
    return [slots[key] for key in sorted(slots)]


def next_business_slot(connection, configuration: dict, after: str) -> str | None:
    after_at = datetime.fromisoformat(after).astimezone(timezone.utc)
    slots = expected_business_slots(
        connection, configuration, after_at.isoformat(),
        (after_at + timedelta(days=2)).isoformat(),
    )
    return next(
        (slot["scheduled_at"] for slot in slots
         if datetime.fromisoformat(slot["scheduled_at"]) > after_at), None)


def _formal_source_type(row) -> bool:
    return (
        not str(row["protocol_code"]).startswith("HJ212:")
        or (row["function_code"] == 2061
            and row["projection_version"] == BUSINESS_PROJECTION_VERSION)
    )


def _formal_source(row) -> bool:
    return row["quality"] == "valid" and _formal_source_type(row)


def _cycle_factor(connection, site_id: int, configuration: dict, scheduled_at: str,
                  evaluated_at: datetime) -> dict:
    rows = _slot_rows(connection, site_id, configuration, scheduled_at)
    formal_rows = [row for row in rows if _formal_source(row)]
    distinct_formal_values = {
        (row["standard_value"], row["standard_unit"]) for row in formal_rows
    }
    selected = formal_rows[0] if len(distinct_formal_values) == 1 else None
    deadline_at = business_slot_deadline(configuration, scheduled_at)
    base = {
        "business_metric": configuration["business_metric"],
        "instrument_asset_code": configuration["instrument_asset_code"],
        "standard_unit": configuration.get("standard_unit") or "",
        "decimal_precision": int(configuration.get("decimal_precision") or 0),
        "scheduled_at": scheduled_at,
        "available_at": (datetime.fromisoformat(scheduled_at) + timedelta(
            seconds=int(configuration.get("result_delay_seconds") or 0))).isoformat(),
        "deadline_at": deadline_at,
        "process_candidate_count": sum(
            row["function_code"] == 2011 and str(row["protocol_code"]).startswith("HJ212:")
            for row in rows
        ),
        "formal_candidate_count": len(formal_rows),
        "duplicate_count": max(0, len(formal_rows) - 1),
    }
    if len(distinct_formal_values) > 1:
        return dict(base, status="review_required", source_type="hj212_2061_avg",
                    value=None, observed_at=None, received_at=None,
                    reason_code="formal_source_conflict",
                    reason="同一检测周期存在多个不同的正式来源值，需核对")
    if selected:
        return dict(base, status="value",
                    source_type=("hj212_2061_avg" if str(selected["protocol_code"]).startswith("HJ212:")
                                 else "scheduled_protocol_observation"),
                    protocol_code=selected["protocol_code"], value=selected["standard_value"],
                    value_display=format_business_value(
                        selected["standard_value"], configuration.get("decimal_precision")),
                    standard_unit=selected["standard_unit"], observed_at=selected["observed_at"],
                    received_at=selected["received_at"], timeliness=selected["timeliness"],
                    reason_code=None, reason=None)
    pending = evaluated_at < datetime.fromisoformat(deadline_at)
    return dict(base, status="pending" if pending else "missing", source_type=None,
                value=None, observed_at=None, received_at=None,
                reason_code="analysis_pending" if pending else "missing_formal_result",
                reason=("正式结果仍在分析与宽限窗口内" if pending
                        else "截止时间后仍无唯一有效的正式结果"))


def _business_rows_for_configuration(rows, configuration: dict, *, scheduled_at: str | None = None):
    protocol_codes = set(configuration.get("protocol_codes") or [configuration["protocol_code"]])
    return [row for row in rows
            if row["endpoint_id"] == configuration["endpoint_id"]
            and row["business_metric"] == configuration["business_metric"]
            and row["instrument_asset_code"] == configuration["instrument_asset_code"]
            and row["protocol_code"] in protocol_codes
            and (scheduled_at is None or row["scheduled_at"] == scheduled_at)]


def _cycle_factor_from_rows(rows, configuration: dict, scheduled_at: str,
                            evaluated_at: datetime) -> dict:
    rows = _business_rows_for_configuration(rows, configuration, scheduled_at=scheduled_at)
    formal_rows = [row for row in rows if _formal_source(row)]
    distinct_formal_values = {
        (row["standard_value"], row["standard_unit"]) for row in formal_rows
    }
    selected = formal_rows[0] if len(distinct_formal_values) == 1 else None
    deadline_at = business_slot_deadline(configuration, scheduled_at)
    base = {
        "business_metric": configuration["business_metric"],
        "instrument_asset_code": configuration["instrument_asset_code"],
        "standard_unit": configuration.get("standard_unit") or "",
        "decimal_precision": int(configuration.get("decimal_precision") or 0),
        "scheduled_at": scheduled_at,
        "available_at": (datetime.fromisoformat(scheduled_at) + timedelta(
            seconds=int(configuration.get("result_delay_seconds") or 0))).isoformat(),
        "deadline_at": deadline_at,
        "process_candidate_count": sum(
            row["function_code"] == 2011 and str(row["protocol_code"]).startswith("HJ212:")
            for row in rows
        ),
        "formal_candidate_count": len(formal_rows),
        "duplicate_count": max(0, len(formal_rows) - 1),
    }
    if len(distinct_formal_values) > 1:
        return dict(base, status="review_required", source_type="hj212_2061_avg",
                    value=None, observed_at=None, received_at=None,
                    reason_code="formal_source_conflict",
                    reason="同一检测周期存在多个不同的正式来源值，需核对")
    if selected:
        return dict(base, status="value",
                    source_type=("hj212_2061_avg" if str(selected["protocol_code"]).startswith("HJ212:")
                                 else "scheduled_protocol_observation"),
                    protocol_code=selected["protocol_code"], value=selected["standard_value"],
                    value_display=format_business_value(
                        selected["standard_value"], configuration.get("decimal_precision")),
                    standard_unit=selected["standard_unit"], observed_at=selected["observed_at"],
                    received_at=selected["received_at"], timeliness=selected["timeliness"],
                    reason_code=None, reason=None)
    pending = evaluated_at < datetime.fromisoformat(deadline_at)
    return dict(base, status="pending" if pending else "missing", source_type=None,
                value=None, observed_at=None, received_at=None,
                reason_code="analysis_pending" if pending else "missing_formal_result",
                reason=("正式结果仍在分析与宽限窗口内" if pending
                        else "截止时间后仍无唯一有效的正式结果"))


def latest_cycle(connection, site_id: int, *, at_time: str | None = None,
                  configurations: list[dict] | None = None,
                  include_latest_complete: bool = True,
                  business_rows: list[dict] | None = None):
    """Build the authoritative latest four-hour cycle without promoting minute facts."""
    at_time = at_time or _utc_now()
    evaluated_at = datetime.fromisoformat(at_time).astimezone(timezone.utc)
    configurations = (configurations if configurations is not None
                      else current_factor_configurations(connection, site_id, at_time=at_time))
    if configurations and not any(
            item.get("expected_factor_config_id") for item in configurations):
        return {
            "scheduled_at": None, "available_at": None, "deadline_at": None,
            "status": "not_configured", "expected_count": 0,
            "formed_count": 0, "missing_count": 0, "pending_count": 0,
            "review_required_count": 0, "sufficient_for_business": False,
            "latest_complete_cycle_at": None, "factors": [],
        }
    configured = [item for item in configurations if item.get("schedule_id")]
    if not configurations or len(configured) != len(configurations):
        return {
            "scheduled_at": None, "available_at": None, "deadline_at": None,
            "status": "not_configured", "expected_count": len(configurations),
            "formed_count": 0, "missing_count": 0, "pending_count": 0,
            "review_required_count": 0, "sufficient_for_business": False,
            "latest_complete_cycle_at": None, "factors": [],
        }
    candidate_slots = set()
    for configuration in configured:
        interval = int(configuration.get("expected_interval_seconds") or 14400)
        delay = int(configuration.get("result_delay_seconds") or 0)
        tolerance = int(configuration.get("tolerance_seconds") or 0)
        start = evaluated_at - timedelta(seconds=max(interval * 2, interval + delay + tolerance))
        candidate_slots.update(
            slot["scheduled_at"] for slot in expected_business_slots(
                connection, configuration, start.isoformat(),
                (evaluated_at + timedelta(seconds=1)).isoformat())
            if datetime.fromisoformat(slot["scheduled_at"]) <= evaluated_at
        )
    scheduled_at = max(candidate_slots, default=None)
    if scheduled_at is None:
        return {
            "scheduled_at": None, "available_at": None, "deadline_at": None,
            "status": "not_configured", "expected_count": len(configured),
            "formed_count": 0, "missing_count": 0, "pending_count": 0,
            "review_required_count": 0, "sufficient_for_business": False,
            "latest_complete_cycle_at": None, "factors": [],
        }
    factors = [
        (_cycle_factor_from_rows(business_rows, item, scheduled_at, evaluated_at)
         if business_rows is not None else
         _cycle_factor(connection, site_id, item, scheduled_at, evaluated_at))
        for item in configured
    ]
    formed_count = sum(item["status"] == "value" for item in factors)
    review_count = sum(item["status"] == "review_required" for item in factors)
    pending_count = sum(item["status"] == "pending" for item in factors)
    missing_count = sum(item["status"] == "missing" for item in factors)
    status = ("review_required" if review_count else "pending" if pending_count
              else "complete" if formed_count == len(factors) else "incomplete")
    available_at = max(item["available_at"] for item in factors)
    deadline_at = max(item["deadline_at"] for item in factors)
    return {
        "scheduled_at": scheduled_at, "available_at": available_at, "deadline_at": deadline_at,
        "status": status, "expected_count": len(factors), "formed_count": formed_count,
        "missing_count": missing_count, "pending_count": pending_count,
        "review_required_count": review_count, "sufficient_for_business": status == "complete",
        "latest_complete_cycle_at": (_latest_complete_cycle_at(
            connection, site_id, configured, scheduled_at)
            if include_latest_complete else None),
        "factors": factors,
    }


def _latest_complete_cycle_at(connection, site_id: int, configurations: list[dict],
                              before_or_at: str) -> str | None:
    if not configurations:
        return None
    key_clauses = []
    params = [site_id, before_or_at, BUSINESS_PROJECTION_VERSION]
    for item in configurations:
        key_clauses.append(
            "(business.endpoint_id=? AND business.business_metric=? "
            "AND business.instrument_asset_code IS ?)"
        )
        params.extend((item["endpoint_id"], item["business_metric"], item["instrument_asset_code"]))
    params.append(len(configurations))
    row = connection.execute(
        f"""WITH unique_factor_results AS (
                SELECT business.scheduled_at,business.endpoint_id,business.business_metric,
                       business.instrument_asset_code
                FROM monitoring_business_observations business
                JOIN observation_batches batch ON batch.id=business.observation_batch_id
                WHERE business.business_site_id=? AND business.scheduled_at<=?
                  AND business.is_current=1 AND batch.is_current=1 AND business.quality='valid'
                  AND (business.protocol_code NOT LIKE 'HJ212:%'
                       OR (batch.function_code=2061 AND business.projection_version=?))
                  AND ({' OR '.join(key_clauses)})
                GROUP BY business.scheduled_at,business.endpoint_id,business.business_metric,
                         business.instrument_asset_code
                HAVING COUNT(DISTINCT printf('%.17g',business.standard_value)||char(31)||business.standard_unit)=1
            )
            SELECT scheduled_at FROM unique_factor_results
            GROUP BY scheduled_at HAVING COUNT(*)=?
            ORDER BY scheduled_at DESC LIMIT 1""",
        params,
    ).fetchone()
    return row[0] if row else None


def _latest_value_for_configuration(connection, site_id: int, configuration: dict):
    protocol_codes = configuration.get('protocol_codes') or [configuration['protocol_code']]
    marks = ','.join('?' * len(protocol_codes))
    rows = connection.execute(
        f"""SELECT business.business_metric,business.protocol_code,business.standard_value,
                   business.standard_unit,business.quality,business.instrument_asset_code,
                   business.endpoint_id,business.scheduled_at,business.observed_at,
                   business.received_at,business.timeliness,business.slot_state,
                   'business_schedule' AS aggregation_source,
                   business.projection_version,
                   business.projection_version AS normalization_version,batch.function_code,
                   batch.normalized_at
            FROM monitoring_business_observations business
            JOIN observation_batches batch ON batch.id=business.observation_batch_id
            WHERE business.business_site_id=? AND business.endpoint_id=?
              AND business.is_current=1 AND batch.is_current=1 AND business.quality='valid'
              AND business.protocol_code IN ({marks}) AND business.business_metric=?
              AND business.instrument_asset_code IS ?
            ORDER BY business.scheduled_at DESC,business.id DESC LIMIT 1000""",
        (site_id, configuration['endpoint_id'], *protocol_codes, configuration['business_metric'],
         configuration['instrument_asset_code']),
    ).fetchall()
    by_slot = {}
    for row in rows:
        if _formal_source(row):
            by_slot.setdefault(row['scheduled_at'], []).append(row)
    for slot_rows in by_slot.values():
        if len({(row['standard_value'], row['standard_unit']) for row in slot_rows}) == 1:
            return dict(slot_rows[0])
    return None


def latest_values(connection, site_id: int, *, at_time: str | None = None):
    result = []
    for configuration in current_factor_configurations(connection, site_id, at_time=at_time):
        row = _latest_value_for_configuration(connection, site_id, configuration)
        if row:
            item = dict(row)
            item['expected_interval_seconds'] = configuration['expected_interval_seconds']
            item['tolerance_seconds'] = configuration['tolerance_seconds']
            item['decimal_precision'] = int(configuration.get('decimal_precision') or 0)
            item['standard_value_display'] = format_business_value(
                item.get('standard_value'), configuration.get('decimal_precision'))
            result.append(item)
    return result


def _latest_values_from_rows(configurations: list[dict], rows) -> list[dict]:
    result = []
    for configuration in configurations:
        matching = [row for row in _business_rows_for_configuration(rows, configuration)
                    if _formal_source(row)]
        by_slot = {}
        for row in matching:
            by_slot.setdefault(row["scheduled_at"], []).append(row)
        for slot_rows in by_slot.values():
            if len({(row["standard_value"], row["standard_unit"]) for row in slot_rows}) != 1:
                continue
            row = slot_rows[0]
            item = {
                "business_metric": row["business_metric"],
                "protocol_code": row["protocol_code"],
                "standard_value": row["standard_value"],
                "standard_value_display": format_business_value(
                    row["standard_value"], configuration.get("decimal_precision")),
                "standard_unit": row["standard_unit"],
                "decimal_precision": int(configuration.get("decimal_precision") or 0),
                "quality": row["quality"],
                "instrument_asset_code": row["instrument_asset_code"],
                "endpoint_id": row["endpoint_id"],
                "scheduled_at": row["scheduled_at"],
                "observed_at": row["observed_at"],
                "received_at": row["received_at"],
                "normalized_at": (row["normalized_at"]
                                  if "normalized_at" in row.keys() else None),
                "timeliness": row["timeliness"],
                "slot_state": row["slot_state"],
                "aggregation_source": "business_schedule",
                "projection_version": row["projection_version"],
                "normalization_version": row["projection_version"],
                "function_code": row["function_code"],
                "expected_interval_seconds": configuration["expected_interval_seconds"],
                "tolerance_seconds": configuration["tolerance_seconds"],
            }
            result.append(item)
            break
    return result


def format_business_value(value, decimal_precision) -> str | None:
    """Pad to the configured precision without hiding decimals present in the formal value."""
    if value is None:
        return None
    try:
        precision = max(0, min(8, int(decimal_precision)))
        raw = str(value).strip() if isinstance(value, str) else format(float(value), '.15g')
        decimal_value = Decimal(raw)
        actual_precision = max(0, -decimal_value.normalize().as_tuple().exponent)
        display_precision = max(precision, actual_precision)
        return f"{decimal_value:.{display_precision}f}"
    except (TypeError, ValueError, OverflowError, InvalidOperation):
        return str(value)


def latest_values_and_cycles_batch(connection, configurations_by_site, *, at_time: str | None = None):
    """Read current business rows once, then project every list item in memory."""
    site_ids = list(configurations_by_site)
    if not site_ids:
        return {}, {}
    at_time = at_time or _utc_now()
    marks = ",".join("?" * len(site_ids))
    rows = [dict(row) for row in connection.execute(
        f"""WITH ranked AS (
                SELECT business.id,business.business_site_id,business.business_metric,
                       business.protocol_code,business.standard_value,business.standard_unit,
                       business.quality,business.instrument_asset_code,business.endpoint_id,
                       business.scheduled_at,business.observed_at,business.received_at,
                       business.timeliness,business.slot_state,business.projection_version,
                       batch.function_code,batch.normalized_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY business.business_site_id,business.endpoint_id,
                                        business.business_metric,COALESCE(business.instrument_asset_code,'')
                           ORDER BY business.scheduled_at DESC,business.id DESC
                       ) AS factor_rank
                FROM monitoring_business_observations business
                JOIN observation_batches batch ON batch.id=business.observation_batch_id
                WHERE business.business_site_id IN ({marks})
                  AND business.is_current=1 AND batch.is_current=1
            )
            SELECT * FROM ranked WHERE factor_rank<=1000
            ORDER BY business_site_id,endpoint_id,business_metric,scheduled_at DESC,id DESC""",
        site_ids,
    ).fetchall()]
    rows_by_site = {}
    for row in rows:
        rows_by_site.setdefault(row["business_site_id"], []).append(row)
    values_by_site = {}
    cycles_by_site = {}
    for site_id, configurations in configurations_by_site.items():
        site_rows = rows_by_site.get(site_id, [])
        values_by_site[site_id] = _latest_values_from_rows(configurations, site_rows)
        cycles_by_site[site_id] = latest_cycle(
            connection, site_id, at_time=at_time, configurations=configurations,
            include_latest_complete=False, business_rows=site_rows)
    return values_by_site, cycles_by_site


def trend(connection, site_id: int, metric: str, start: str, end: str, limit: int):
    configurations = [
        item for item in current_factor_configurations(connection, site_id)
        if item['business_metric'] == metric
    ]
    points = []
    for configuration in configurations:
        protocol_codes = configuration.get('protocol_codes') or [configuration['protocol_code']]
        marks = ','.join('?' * len(protocol_codes))
        rows = connection.execute(
            f"""SELECT business.business_metric,business.protocol_code,
                      business.standard_value AS value,business.standard_unit AS unit,business.quality,
                      business.scheduled_at,business.observed_at,business.received_at,
                      business.timeliness,business.slot_state,business.projection_version,
                      'business_schedule' AS aggregation_source,
                      business.projection_version AS normalization_version,batch.function_code
               FROM monitoring_business_observations business
               JOIN observation_batches batch ON batch.id=business.observation_batch_id
               WHERE business.business_site_id=? AND business.endpoint_id=?
                 AND business.is_current=1 AND batch.is_current=1
                 AND business.protocol_code IN ({marks}) AND business.business_metric=?
                 AND business.instrument_asset_code IS ?
                 AND business.scheduled_at>=? AND business.scheduled_at<?
               ORDER BY business.scheduled_at,business.id""",
            (site_id, configuration['endpoint_id'], *protocol_codes, configuration['business_metric'],
             configuration['instrument_asset_code'], start, end),
        ).fetchall()
        by_slot = {}
        for row in rows:
            if _formal_source(row):
                by_slot.setdefault(row['scheduled_at'], []).append(row)
        for slot_rows in by_slot.values():
            if len({(row['value'], row['unit']) for row in slot_rows}) == 1:
                points.append(dict(slot_rows[0]))
    return sorted(points, key=lambda item: (item['observed_at'], item['business_metric']))[:limit]


def trend_slots(connection, site_id: int, metric: str, start: str, end: str,
                *, at_time: str | None = None):
    """Return every configured slot, including explicit null-valued gaps."""
    configurations = [
        item for item in current_factor_configurations(connection, site_id, at_time=end)
        if item['business_metric'] == metric
    ]
    evaluated_at = datetime.fromisoformat(at_time or _utc_now()).astimezone(timezone.utc)
    slot_map = {}
    for configuration in configurations:
        for slot in expected_business_slots(connection, configuration, start, end):
            slot_map.setdefault(slot['scheduled_at'], []).append((configuration, slot))
    result = []
    for scheduled_at in sorted(slot_map):
        selected = None
        review_required = False
        deadline_at = None
        for configuration, slot in slot_map[scheduled_at]:
            rows = _slot_rows(connection, site_id, configuration, scheduled_at)
            formal_rows = [row for row in rows if _formal_source(row)]
            distinct_formal_values = {
                (row['standard_value'], row['standard_unit']) for row in formal_rows
            }
            if len(distinct_formal_values) > 1:
                review_required = True
            if len(distinct_formal_values) == 1:
                selected = selected or formal_rows[0]
            deadline_at = max(deadline_at or slot['result_window_end_at'], slot['result_window_end_at'])
        if review_required:
            status, value = 'review_required', None
        elif selected:
            status, value = 'value', selected['standard_value']
        elif deadline_at and evaluated_at < datetime.fromisoformat(deadline_at):
            status, value = 'pending', None
        else:
            status, value = 'missing', None
        result.append({
            'business_metric': metric, 'scheduled_at': scheduled_at, 'status': status,
            'value': value, 'unit': selected['standard_unit'] if selected else '',
            'quality': selected['quality'] if selected else None,
            'observed_at': selected['observed_at'] if selected else None,
            'received_at': selected['received_at'] if selected else None,
            'deadline_at': deadline_at,
        })
    return result


def business_slot_diagnostics(connection, site_id: int, metric: str, start: str, end: str):
    configurations = [
        item for item in current_factor_configurations(connection, site_id)
        if item['business_metric'] == metric
    ]
    conflicts = set()
    late = set()
    suspect = set()
    duplicate_records = 0
    for configuration in configurations:
        protocol_codes = configuration.get('protocol_codes') or [configuration['protocol_code']]
        marks = ','.join('?' * len(protocol_codes))
        rows = connection.execute(
            f"""SELECT business.scheduled_at,business.timeliness,business.quality,
                       business.protocol_code,business.standard_value,business.standard_unit,
                       business.projection_version,
                       batch.function_code
                FROM monitoring_business_observations business
                JOIN observation_batches batch ON batch.id=business.observation_batch_id
                WHERE business.business_site_id=? AND business.endpoint_id=?
                  AND business.protocol_code IN ({marks}) AND business.business_metric=?
                  AND business.instrument_asset_code IS ? AND business.is_current=1
                  AND batch.is_current=1 AND business.scheduled_at>=? AND business.scheduled_at<?""",
            (site_id, configuration['endpoint_id'], *protocol_codes, metric,
             configuration['instrument_asset_code'], start, end),
        ).fetchall()
        source_rows = [row for row in rows if _formal_source_type(row)]
        formal_rows = [row for row in source_rows if _formal_source(row)]
        late.update(row['scheduled_at'] for row in source_rows if row['timeliness'] == 'late')
        suspect.update(row['scheduled_at'] for row in source_rows if row['quality'] == 'suspect')
        formal_by_slot = {}
        for row in formal_rows:
            formal_by_slot.setdefault(row['scheduled_at'], []).append(row)
        for scheduled_at, formal_rows in formal_by_slot.items():
            values = {(row['standard_value'], row['standard_unit']) for row in formal_rows}
            if len(values) > 1:
                conflicts.add(scheduled_at)
            duplicate_records += max(0, len(formal_rows) - len(values))
    return {
        'late_points': len(late),
        'suspect_points': len(suspect),
        'duplicate_records': duplicate_records,
        'conflict_slots': len(conflicts),
    }
