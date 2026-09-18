"""Append-only normalization and read-only projections for station monitoring."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from .sl651_parser import FrameError, PARSER_VERSION, parse_frame, parse_water_quality_report
    from .hj212_parser import HJ212_PARSER_VERSION, ParsedHJ212Frame, parse_hj212_frame
except ImportError:  # pragma: no cover
    from sl651_parser import FrameError, PARSER_VERSION, parse_frame, parse_water_quality_report
    from hj212_parser import HJ212_PARSER_VERSION, ParsedHJ212Frame, parse_hj212_frame

NORMALIZATION_VERSION = "station-monitoring-normalizer-v2"
BUSINESS_PROJECTION_VERSION = "station-monitoring-business-v1"
HISTORICAL_REPROJECTION_VERSION = "station-monitoring-historical-business-v1"


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
        "SELECT id FROM monitoring_quality_issues WHERE raw_frame_id IS ? AND issue_type=? AND status='open'",
        (raw_id, issue_type),
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


def _scheduled_at(schedule, observed_at: str) -> str | None:
    """Return the UTC slot only when device DataTime is an exact configured point."""
    try:
        zone = ZoneInfo(schedule["timezone"])
    except ZoneInfoNotFoundError as exc:
        raise FrameError("invalid_business_schedule_timezone", "business schedule timezone is unavailable") from exc
    observed = datetime.fromisoformat(observed_at).astimezone(zone)
    anchor = time.fromisoformat(schedule["anchor_local_time"])
    observed_seconds = observed.hour * 3600 + observed.minute * 60 + observed.second
    anchor_seconds = anchor.hour * 3600 + anchor.minute * 60 + anchor.second
    interval = int(schedule["interval_seconds"])
    if observed.microsecond or (observed_seconds - anchor_seconds) % interval:
        return None
    return observed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


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
                                  quality: str) -> bool:
    schedule = _business_schedule_for(connection, endpoint_id, protocol_code, observed_at)
    if not schedule:
        return False
    scheduled_at = _scheduled_at(schedule, observed_at)
    if scheduled_at is None:
        return False
    received = datetime.fromisoformat(received_at)
    observed = datetime.fromisoformat(observed_at)
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
                              value.standard_unit,value.quality,batch.raw_frame_id,batch.endpoint_id,
                              batch.business_site_id,batch.observed_at,batch.received_at
                       FROM observation_values value
                       JOIN observation_batches batch ON batch.id=value.observation_batch_id
                       LEFT JOIN monitoring_business_observations business
                         ON business.source_observation_value_id=value.id
                       WHERE value.id=? AND value.is_current=1 AND value.is_published=1
                         AND value.quality IN ('valid','suspect') AND batch.is_current=1
                         AND batch.projection_state='completed' AND business.id IS NULL""",
                    (value_id,),
                ).fetchone()
                if row and _project_business_observation(
                        connection, value_id=row["value_id"], batch_id=row["observation_batch_id"],
                        endpoint_id=row["endpoint_id"], site_id=row["business_site_id"],
                        protocol_code=row["protocol_code"], business_metric=row["business_metric"],
                        instrument_asset_code=row["instrument_asset_code"], observed_at=row["observed_at"],
                        received_at=row["received_at"], standard_value=row["standard_value"],
                        standard_unit=row["standard_unit"], quality=row["quality"]):
                    projected += 1
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
                if frame.command != "2011" or frame.data_time is None:
                    return _mark_waiting(connection, raw_id, None, "hj212_not_projectable", "HJ212 command has no projectable observation")
                report_factors = frame.factors
                reported_at = _as_utc(frame.data_time, endpoint_timezone)
                observed_at = _as_utc(frame.data_time, endpoint_timezone)
                function_code = 2011
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
                       VALUES (?, ?, 'unmapped', ?, 0)""", (batch_id, factor.protocol_code, parser_version),
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
                 parser_version, int(published and factor.quality in {"valid", "suspect"})),
            )
            if published and factor.quality in {"valid", "suspect"}:
                _project_business_observation(
                    connection, value_id=int(value_cursor.lastrowid), batch_id=batch_id,
                    endpoint_id=raw["endpoint_id"], site_id=site_id, protocol_code=factor.protocol_code,
                    business_metric=metric,
                    instrument_asset_code=mapping["instrument_asset_code"] or profile["instrument_asset_code"],
                    observed_at=observed_at, received_at=raw["received_at"],
                    standard_value=factor.raw_value, standard_unit=definition["standard_unit"],
                    quality=factor.quality,
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


def current_factor_configurations(connection, site_id: int, *, at_time: str | None = None):
    """Return every currently published factor, including factors that never reported."""
    at_time = at_time or _utc_now()
    rows = connection.execute(
        """SELECT profile.endpoint_id, mapping.protocol_code,
                  COALESCE(mapping.business_metric, definition.business_metric) AS business_metric,
                  COALESCE(mapping.instrument_asset_code, profile.instrument_asset_code) AS instrument_asset_code,
                  definition.standard_unit, mapping.expected_interval_seconds AS ingestion_interval_seconds,
                  mapping.tolerance_seconds AS ingestion_tolerance_seconds,
                  profile.effective_from AS profile_effective_from, profile.effective_to AS profile_effective_to,
                  mapping.effective_from AS mapping_effective_from, mapping.effective_to AS mapping_effective_to
           FROM monitoring_endpoint_profiles profile
           JOIN monitoring_factor_mappings mapping ON mapping.endpoint_id=profile.endpoint_id
           JOIN monitoring_factor_definitions definition ON definition.protocol_code=mapping.protocol_code
           WHERE profile.business_site_id=? AND profile.enabled=1
             AND profile.effective_from<=? AND (profile.effective_to IS NULL OR profile.effective_to>?)
             AND mapping.enabled=1 AND mapping.effective_from<=? AND (mapping.effective_to IS NULL OR mapping.effective_to>?)
             AND definition.is_published=1 AND COALESCE(mapping.business_metric, definition.business_metric) IS NOT NULL
           ORDER BY profile.endpoint_id, mapping.protocol_code, mapping.effective_from DESC""",
        (site_id, at_time, at_time, at_time, at_time),
    ).fetchall()
    grouped = {}
    for row in rows:
        item = dict(row)
        key = (item["endpoint_id"], item["business_metric"], item["instrument_asset_code"])
        grouped.setdefault(key, []).append(item)
    result = []
    for configurations in grouped.values():
        configurations.sort(key=lambda item: _protocol_business_priority(item["protocol_code"]))
        item = dict(configurations[0])
        item["protocol_codes"] = [entry["protocol_code"] for entry in configurations]
        item["protocol_configurations"] = configurations
        scheduled = [
            (entry, _business_schedule_for(connection, entry["endpoint_id"], entry["protocol_code"], at_time))
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
        })
        if schedule:
            at_datetime = datetime.fromisoformat(at_time).astimezone(timezone.utc)
            future_slots = expected_business_slots(
                connection, item, at_datetime.isoformat(),
                (at_datetime + timedelta(days=2)).isoformat(),
            )
            item["next_expected_at"] = next(
                (slot["scheduled_at"] for slot in future_slots
                 if datetime.fromisoformat(slot["scheduled_at"]) > at_datetime), None)
        else:
            item["next_expected_at"] = None
        result.append(item)
    return result


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
    rows = connection.execute(
        """SELECT * FROM monitoring_business_schedules
           WHERE endpoint_id=? AND enabled=1 AND (protocol_code=? OR protocol_code IS NULL)
             AND effective_from<=? AND (effective_to IS NULL OR effective_to>?)
           ORDER BY effective_from,id""",
        (configuration["endpoint_id"], configuration["protocol_code"], end_at.isoformat(), start_at.isoformat()),
    ).fetchall()
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
                effective = _business_schedule_for(
                    connection, configuration["endpoint_id"], configuration["protocol_code"], utc_text)
                if effective and effective["id"] == schedule["id"]:
                    slots[utc_text] = {
                        "scheduled_at": utc_text, "schedule_id": schedule["id"],
                        "interval_seconds": interval, "tolerance_seconds": int(schedule["tolerance_seconds"]),
                        "timezone": schedule["timezone"],
                    }
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


def _latest_value_for_configuration(connection, site_id: int, configuration: dict):
    protocol_codes = configuration.get('protocol_codes') or [configuration['protocol_code']]
    marks = ','.join('?' * len(protocol_codes))
    return connection.execute(
        f"""SELECT business_metric,protocol_code,standard_value,standard_unit,quality,
                  instrument_asset_code,endpoint_id,scheduled_at,observed_at,received_at,
                  timeliness,slot_state,'business_schedule' AS aggregation_source,
                  projection_version AS normalization_version
           FROM monitoring_business_observations
           WHERE business_site_id=? AND endpoint_id=? AND is_current=1 AND slot_state='selected'
             AND quality='valid'
             AND protocol_code IN ({marks}) AND business_metric=? AND instrument_asset_code IS ?
           ORDER BY scheduled_at DESC,id DESC LIMIT 1""",
        (site_id, configuration['endpoint_id'], *protocol_codes, configuration['business_metric'],
         configuration['instrument_asset_code']),
    ).fetchone()


def latest_values(connection, site_id: int, *, at_time: str | None = None):
    result = []
    for configuration in current_factor_configurations(connection, site_id, at_time=at_time):
        row = _latest_value_for_configuration(connection, site_id, configuration)
        if row:
            item = dict(row)
            item['expected_interval_seconds'] = configuration['expected_interval_seconds']
            item['tolerance_seconds'] = configuration['tolerance_seconds']
            result.append(item)
    return result


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
            f"""SELECT business_metric,standard_value AS value,standard_unit AS unit,quality,
                      scheduled_at,observed_at,received_at,timeliness,slot_state,
                      'business_schedule' AS aggregation_source,projection_version AS normalization_version
               FROM monitoring_business_observations
               WHERE business_site_id=? AND endpoint_id=? AND is_current=1 AND slot_state='selected'
                 AND protocol_code IN ({marks}) AND business_metric=? AND instrument_asset_code IS ?
                 AND scheduled_at>=? AND scheduled_at<?
               ORDER BY scheduled_at,id LIMIT ?""",
            (site_id, configuration['endpoint_id'], *protocol_codes, configuration['business_metric'],
             configuration['instrument_asset_code'], start, end, limit),
        ).fetchall()
        points.extend(dict(row) for row in rows)
    return sorted(points, key=lambda item: (item['observed_at'], item['business_metric']))[:limit]


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
            f"""SELECT scheduled_at,slot_state,timeliness,quality
               FROM monitoring_business_observations
               WHERE business_site_id=? AND endpoint_id=? AND protocol_code IN ({marks}) AND business_metric=?
                 AND instrument_asset_code IS ? AND is_current=1 AND scheduled_at>=? AND scheduled_at<?""",
            (site_id, configuration['endpoint_id'], *protocol_codes, metric,
             configuration['instrument_asset_code'], start, end),
        ).fetchall()
        conflicts.update(row['scheduled_at'] for row in rows if row['slot_state'] == 'conflict')
        late.update(row['scheduled_at'] for row in rows if row['timeliness'] == 'late')
        suspect.update(row['scheduled_at'] for row in rows if row['quality'] == 'suspect')
        duplicate_records += sum(row['slot_state'] == 'duplicate' for row in rows)
    return {
        'late_points': len(late),
        'suspect_points': len(suspect),
        'duplicate_records': duplicate_records,
        'conflict_slots': len(conflicts),
    }
