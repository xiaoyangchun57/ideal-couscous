"""Append-only normalization and read-only projections for station monitoring."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from .sl651_parser import FrameError, PARSER_VERSION, parse_frame, parse_water_quality_report
    from .hj212_parser import HJ212_PARSER_VERSION, ParsedHJ212Frame, parse_hj212_frame
except ImportError:  # pragma: no cover
    from sl651_parser import FrameError, PARSER_VERSION, parse_frame, parse_water_quality_report
    from hj212_parser import HJ212_PARSER_VERSION, ParsedHJ212Frame, parse_hj212_frame

NORMALIZATION_VERSION = "station-monitoring-normalizer-v1"


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
            connection.execute(
                """INSERT INTO observation_values(observation_batch_id, protocol_code, business_metric, raw_value,
                   raw_unit, standard_value, standard_unit, quality, instrument_asset_code, parser_version, is_published)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (batch_id, factor.protocol_code, metric, factor.raw_value, definition["raw_unit"], factor.raw_value,
                 definition["standard_unit"], factor.quality, mapping["instrument_asset_code"] or profile["instrument_asset_code"],
                 parser_version, int(published and factor.quality in {"valid", "suspect"})),
            )
        connection.execute("UPDATE observation_batches SET batch_status=? WHERE id=?", (batch_status, batch_id))
        connection.execute("UPDATE ingest_raw_frames SET disposition='accepted', persistence_state='persisted' WHERE id=?", (raw_id,))
        connection.execute("DELETE FROM monitoring_normalization_retries WHERE raw_frame_id=?", (raw_id,))
        connection.commit()
        return batch_status


def current_factor_configurations(connection, site_id: int, *, at_time: str | None = None):
    """Return every currently published factor, including factors that never reported."""
    at_time = at_time or _utc_now()
    rows = connection.execute(
        """SELECT profile.endpoint_id, mapping.protocol_code,
                  COALESCE(mapping.business_metric, definition.business_metric) AS business_metric,
                  COALESCE(mapping.instrument_asset_code, profile.instrument_asset_code) AS instrument_asset_code,
                  mapping.expected_interval_seconds, mapping.tolerance_seconds,
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
    return [dict(row) for row in rows]


def _latest_value_for_configuration(connection, site_id: int, configuration: dict):
    return connection.execute(
        """SELECT v.business_metric, v.protocol_code, v.standard_value, v.standard_unit, v.quality,
                  v.instrument_asset_code, b.endpoint_id, b.observed_at, b.received_at, b.granularity,
                  b.aggregation_source, b.normalization_version
           FROM observation_values v JOIN observation_batches b ON b.id=v.observation_batch_id
           WHERE b.business_site_id=? AND b.endpoint_id=? AND b.is_current=1 AND v.is_current=1
             AND v.is_published=1 AND v.quality IN ('valid', 'suspect') AND v.protocol_code=?
             AND v.business_metric=? AND b.observed_at>=?
             AND (? IS NULL OR b.observed_at<?) AND b.observed_at>=?
             AND (? IS NULL OR b.observed_at<?)
             AND v.instrument_asset_code IS ?
             AND EXISTS (
                 SELECT 1 FROM monitoring_factor_mappings historical
                 WHERE historical.endpoint_id=b.endpoint_id AND historical.protocol_code=v.protocol_code
                   AND historical.enabled=1 AND historical.effective_from<=b.observed_at
                   AND (historical.effective_to IS NULL OR historical.effective_to>b.observed_at)
                   AND COALESCE(historical.business_metric, (
                       SELECT historical_definition.business_metric FROM monitoring_factor_definitions historical_definition
                       WHERE historical_definition.protocol_code=historical.protocol_code
                   ))=v.business_metric
                   AND COALESCE(historical.instrument_asset_code, (
                       SELECT historical_profile.instrument_asset_code FROM monitoring_endpoint_profiles historical_profile
                       WHERE historical_profile.endpoint_id=b.endpoint_id AND historical_profile.enabled=1
                         AND historical_profile.effective_from<=b.observed_at
                         AND (historical_profile.effective_to IS NULL OR historical_profile.effective_to>b.observed_at)
                       ORDER BY historical_profile.effective_from DESC LIMIT 1
                   )) IS v.instrument_asset_code
             ) ORDER BY b.observed_at DESC, v.id DESC LIMIT 1""",
        (site_id, configuration['endpoint_id'], configuration['protocol_code'], configuration['business_metric'],
         configuration['mapping_effective_from'], configuration['mapping_effective_to'], configuration['mapping_effective_to'],
         configuration['profile_effective_from'], configuration['profile_effective_to'], configuration['profile_effective_to'],
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
        rows = connection.execute(
            """SELECT v.business_metric, v.standard_value AS value, v.standard_unit AS unit, v.quality,
                      b.observed_at, b.received_at, b.granularity, b.aggregation_source, b.normalization_version
               FROM observation_values v JOIN observation_batches b ON b.id=v.observation_batch_id
               WHERE b.business_site_id=? AND b.endpoint_id=? AND b.is_current=1 AND v.is_current=1
                 AND v.is_published=1 AND v.quality IN ('valid', 'suspect') AND v.protocol_code=?
                 AND v.business_metric=? AND b.observed_at>=?
                 AND (? IS NULL OR b.observed_at<?) AND b.observed_at>=?
                 AND (? IS NULL OR b.observed_at<?) AND v.instrument_asset_code IS ?
                 AND b.observed_at>=? AND b.observed_at<=?
                 AND EXISTS (
                     SELECT 1 FROM monitoring_factor_mappings historical
                     WHERE historical.endpoint_id=b.endpoint_id AND historical.protocol_code=v.protocol_code
                       AND historical.enabled=1 AND historical.effective_from<=b.observed_at
                       AND (historical.effective_to IS NULL OR historical.effective_to>b.observed_at)
                       AND COALESCE(historical.business_metric, (
                           SELECT historical_definition.business_metric FROM monitoring_factor_definitions historical_definition
                           WHERE historical_definition.protocol_code=historical.protocol_code
                       ))=v.business_metric
                       AND COALESCE(historical.instrument_asset_code, (
                           SELECT historical_profile.instrument_asset_code FROM monitoring_endpoint_profiles historical_profile
                           WHERE historical_profile.endpoint_id=b.endpoint_id AND historical_profile.enabled=1
                             AND historical_profile.effective_from<=b.observed_at
                             AND (historical_profile.effective_to IS NULL OR historical_profile.effective_to>b.observed_at)
                           ORDER BY historical_profile.effective_from DESC LIMIT 1
                       )) IS v.instrument_asset_code
                 ) ORDER BY b.observed_at, v.id LIMIT ?""",
            (site_id, configuration['endpoint_id'], configuration['protocol_code'], configuration['business_metric'],
             configuration['mapping_effective_from'], configuration['mapping_effective_to'], configuration['mapping_effective_to'],
             configuration['profile_effective_from'], configuration['profile_effective_to'], configuration['profile_effective_to'],
             configuration['instrument_asset_code'], start, end, limit),
        ).fetchall()
        points.extend(dict(row) for row in rows)
    return sorted(points, key=lambda item: (item['observed_at'], item['business_metric']))[:limit]
