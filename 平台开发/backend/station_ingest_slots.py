"""Shared HJ212 four-hour slot identity rules."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from .hj212_parser import ParsedHJ212Frame
except ImportError:  # pragma: no cover - direct script execution
    from hj212_parser import ParsedHJ212Frame


class HJ212SlotIdentityResolver:
    """Resolve the same normalized slot identity for live and historical frames."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self._profiles: dict[int, list[sqlite3.Row]] = {}
        self._mappings: dict[int, list[sqlite3.Row]] = {}
        self._schedules: dict[int, list[sqlite3.Row]] = {}

    def _endpoint_profiles(self, endpoint_id: int) -> list[sqlite3.Row]:
        if endpoint_id not in self._profiles:
            self._profiles[endpoint_id] = self.connection.execute(
                """SELECT timezone,instrument_asset_code,effective_from,effective_to
                   FROM monitoring_endpoint_profiles
                   WHERE endpoint_id=? AND enabled=1 ORDER BY effective_from DESC""",
                (endpoint_id,),
            ).fetchall()
        return self._profiles[endpoint_id]

    def _endpoint_mappings(self, endpoint_id: int) -> list[sqlite3.Row]:
        if endpoint_id not in self._mappings:
            self._mappings[endpoint_id] = self.connection.execute(
                """SELECT mapping.protocol_code,
                          COALESCE(mapping.business_metric,definition.business_metric) AS business_metric,
                          mapping.instrument_asset_code,definition.standard_unit,
                          mapping.effective_from,mapping.effective_to
                   FROM monitoring_factor_mappings mapping
                   JOIN monitoring_factor_definitions definition
                     ON definition.protocol_code=mapping.protocol_code
                   WHERE mapping.endpoint_id=? AND mapping.enabled=1""",
                (endpoint_id,),
            ).fetchall()
        return self._mappings[endpoint_id]

    def _endpoint_schedules(self, endpoint_id: int) -> list[sqlite3.Row]:
        if endpoint_id not in self._schedules:
            self._schedules[endpoint_id] = self.connection.execute(
                """SELECT * FROM monitoring_business_schedules
                   WHERE endpoint_id=? AND enabled=1
                   ORDER BY (protocol_code IS NOT NULL) DESC,effective_from DESC,id DESC""",
                (endpoint_id,),
            ).fetchall()
        return self._schedules[endpoint_id]

    def _schedule_for(self, endpoint_id: int, protocol_code: str, observed_at: str):
        return next((schedule for schedule in self._endpoint_schedules(endpoint_id)
                     if schedule["protocol_code"] in {None, protocol_code}
                     and schedule["effective_from"] <= observed_at
                     and (schedule["effective_to"] is None
                          or schedule["effective_to"] > observed_at)), None)

    @staticmethod
    def _slot_started_at(schedule, observed_at: datetime, fallback_timezone: str) -> str:
        zone = ZoneInfo(schedule["timezone"] if schedule is not None else fallback_timezone)
        observed = observed_at.astimezone(zone)
        interval = int(schedule["interval_seconds"]) if schedule is not None else 4 * 60 * 60
        anchor = time.fromisoformat(
            schedule["anchor_local_time"] if schedule is not None else "00:00:00")
        local_anchor = datetime.combine(observed.date(), anchor, zone)
        if observed < local_anchor:
            local_anchor -= timedelta(days=1)
        elapsed = int((observed - local_anchor).total_seconds())
        return (local_anchor + timedelta(seconds=(elapsed // interval) * interval)).astimezone(
            timezone.utc).replace(microsecond=0).isoformat()

    def resolve(self, frame: ParsedHJ212Frame, endpoint_id: int) -> tuple[str, str] | None:
        if frame.command != "2011" or frame.data_time is None or not frame.factors:
            return None
        if any(
            factor.quality != "valid" or factor.issue_code is not None
            or factor.raw_value is None or not math.isfinite(factor.raw_value)
            for factor in frame.factors
        ):
            return None

        profile = None
        observed_at = None
        for candidate in self._endpoint_profiles(endpoint_id):
            try:
                candidate_observed = frame.data_time.replace(
                    tzinfo=ZoneInfo(candidate["timezone"]),
                ).astimezone(ZoneInfo("UTC")).replace(microsecond=0)
            except ZoneInfoNotFoundError:
                return None
            candidate_text = candidate_observed.isoformat()
            if (candidate["effective_from"] <= candidate_text
                    and (candidate["effective_to"] is None
                         or candidate["effective_to"] > candidate_text)):
                profile = candidate
                observed_at = candidate_observed
                break
        if profile is None:
            try:
                profile_timezone = "Asia/Shanghai"
                observed_at = frame.data_time.replace(
                    tzinfo=ZoneInfo(profile_timezone),
                ).astimezone(timezone.utc).replace(microsecond=0)
            except ZoneInfoNotFoundError:
                return None
            profile_instrument_asset_code = ""
        else:
            profile_timezone = profile["timezone"]
            profile_instrument_asset_code = profile["instrument_asset_code"] or ""

        protocol_codes = {factor.protocol_code for factor in frame.factors}
        mapped_by_code = {}
        for mapping in self._endpoint_mappings(endpoint_id):
            if (mapping["protocol_code"] in protocol_codes
                    and mapping["effective_from"] <= observed_at.isoformat()
                    and (mapping["effective_to"] is None
                         or mapping["effective_to"] > observed_at.isoformat())):
                mapped_by_code[mapping["protocol_code"]] = mapping
        for protocol_code in protocol_codes - set(mapped_by_code):
            definition = self.connection.execute(
                """SELECT business_metric,standard_unit
                   FROM monitoring_factor_definitions
                   WHERE protocol_code=? AND is_published=1
                     AND business_metric IS NOT NULL""",
                (protocol_code,),
            ).fetchone()
            if definition is not None:
                mapped_by_code[protocol_code] = {
                    "business_metric": definition["business_metric"],
                    "instrument_asset_code": None,
                    "standard_unit": definition["standard_unit"],
                }
        if set(mapped_by_code) != protocol_codes or observed_at is None:
            return None

        schedules = [
            self._schedule_for(endpoint_id, protocol_code, observed_at.isoformat())
            for protocol_code in protocol_codes
        ]
        schedule_ids = {schedule["id"] if schedule is not None else None
                        for schedule in schedules}
        if len(schedule_ids) != 1:
            return None
        schedule = schedules[0]
        try:
            slot_started_at = self._slot_started_at(schedule, observed_at, profile_timezone)
        except ZoneInfoNotFoundError:
            return None
        normalized_factors: dict[tuple[str, str, str, str], dict[str, object]] = {}
        for factor in frame.factors:
            mapping = mapped_by_code[factor.protocol_code]
            identity = (
                mapping["business_metric"] or factor.protocol_code,
                mapping["instrument_asset_code"] or profile_instrument_asset_code,
                mapping["standard_unit"] or "",
                factor.value_kind or "",
            )
            normalized = {
                "factor": identity[0],
                "instrument_asset_code": identity[1],
                "standard_unit": identity[2],
                "value_kind": identity[3],
                "value": 0.0 if factor.raw_value == 0 else factor.raw_value,
                "quality": factor.quality,
                "issue_code": factor.issue_code,
            }
            previous = normalized_factors.get(identity)
            if previous is not None and previous != normalized:
                return None
            normalized_factors[identity] = normalized
        content = json.dumps(
            {"factors": [normalized_factors[key] for key in sorted(normalized_factors)]},
            ensure_ascii=True, separators=(",", ":"), sort_keys=True,
        )
        return slot_started_at, hashlib.sha256(content.encode("ascii")).hexdigest()


def hj212_realtime_summary_identity(
    connection: sqlite3.Connection, frame: ParsedHJ212Frame, endpoint_id: int,
) -> tuple[str, str] | None:
    return HJ212SlotIdentityResolver(connection).resolve(frame, endpoint_id)
