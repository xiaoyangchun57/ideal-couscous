-- Map the field-observed legacy HJ212 factor without rewriting retained evidence.
INSERT INTO monitoring_factor_definitions
    (protocol_code, protocol_factor, raw_unit, parse_format, business_metric, standard_unit, decimal_precision, is_published)
VALUES
    ('HJ212:005', 'hj212_legacy_005_dissolved_oxygen', 'mg/L', 'text-decimal', 'dissolved_oxygen', 'mg/L', 2, 1)
ON CONFLICT(protocol_code) DO UPDATE SET
    protocol_factor=excluded.protocol_factor,
    raw_unit=excluded.raw_unit,
    parse_format=excluded.parse_format,
    business_metric=excluded.business_metric,
    standard_unit=excluded.standard_unit,
    decimal_precision=excluded.decimal_precision,
    is_published=excluded.is_published;

-- Existing approved dissolved-oxygen periods remain authoritative. The legacy
-- vendor code receives the same binding without replacing the standard code.
INSERT INTO monitoring_factor_mappings
    (endpoint_id, protocol_code, business_metric, instrument_asset_code,
     expected_interval_seconds, tolerance_seconds, effective_from, effective_to, enabled)
SELECT
    source.endpoint_id, 'HJ212:005', source.business_metric, source.instrument_asset_code,
    source.expected_interval_seconds, source.tolerance_seconds,
    source.effective_from, source.effective_to, source.enabled
FROM monitoring_factor_mappings source
WHERE source.protocol_code='HJ212:w01009'
  AND NOT EXISTS (
      SELECT 1 FROM monitoring_factor_mappings existing
      WHERE existing.endpoint_id=source.endpoint_id AND existing.protocol_code='HJ212:005'
  );

CREATE TABLE IF NOT EXISTS monitoring_business_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id INTEGER NOT NULL,
    protocol_code TEXT,
    timezone TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL CHECK (interval_seconds IN (3600, 14400)),
    anchor_local_time TEXT NOT NULL CHECK (
        length(anchor_local_time)=8
        AND anchor_local_time GLOB '[0-2][0-9]:[0-5][0-9]:[0-5][0-9]'
        AND CAST(substr(anchor_local_time,1,2) AS INTEGER) BETWEEN 0 AND 23
    ),
    tolerance_seconds INTEGER NOT NULL DEFAULT 600 CHECK (tolerance_seconds BETWEEN 0 AND 3600),
    effective_from TEXT NOT NULL CHECK (substr(effective_from, -6) = '+00:00'),
    effective_to TEXT CHECK (effective_to IS NULL OR substr(effective_to, -6) = '+00:00'),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    FOREIGN KEY (endpoint_id) REFERENCES trusted_endpoints(id),
    FOREIGN KEY (protocol_code) REFERENCES monitoring_factor_definitions(protocol_code)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_monitoring_business_schedule_period
    ON monitoring_business_schedules(endpoint_id, COALESCE(protocol_code, ''), effective_from);
CREATE INDEX IF NOT EXISTS idx_monitoring_business_schedule_lookup
    ON monitoring_business_schedules(endpoint_id, protocol_code, enabled, effective_from, effective_to);

CREATE TRIGGER IF NOT EXISTS reject_overlapping_business_schedule_insert
BEFORE INSERT ON monitoring_business_schedules WHEN NEW.enabled=1
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM monitoring_business_schedules existing
        WHERE existing.endpoint_id=NEW.endpoint_id
          AND COALESCE(existing.protocol_code, '')=COALESCE(NEW.protocol_code, '')
          AND existing.enabled=1
          AND (existing.effective_to IS NULL OR existing.effective_to>NEW.effective_from)
          AND (NEW.effective_to IS NULL OR NEW.effective_to>existing.effective_from)
    ) THEN RAISE(ABORT, 'overlapping business schedule') END;
END;

CREATE TRIGGER IF NOT EXISTS reject_overlapping_business_schedule_update
BEFORE UPDATE OF endpoint_id, protocol_code, effective_from, effective_to, enabled
ON monitoring_business_schedules WHEN NEW.enabled=1
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM monitoring_business_schedules existing
        WHERE existing.id!=OLD.id AND existing.endpoint_id=NEW.endpoint_id
          AND COALESCE(existing.protocol_code, '')=COALESCE(NEW.protocol_code, '')
          AND existing.enabled=1
          AND (existing.effective_to IS NULL OR existing.effective_to>NEW.effective_from)
          AND (NEW.effective_to IS NULL OR NEW.effective_to>existing.effective_from)
    ) THEN RAISE(ABORT, 'overlapping business schedule') END;
END;

-- Only an explicitly archived one-hour or four-hour factor period is a
-- confirmed business schedule. Minute transport cadence is not business cadence.
INSERT INTO monitoring_business_schedules
    (endpoint_id, protocol_code, timezone, interval_seconds, anchor_local_time,
     tolerance_seconds, effective_from, effective_to, enabled)
SELECT mapping.endpoint_id, mapping.protocol_code, profile.timezone,
       mapping.expected_interval_seconds, '00:00:00', 600,
       mapping.effective_from, mapping.effective_to, 1
FROM monitoring_factor_mappings mapping
JOIN monitoring_endpoint_profiles profile
  ON profile.endpoint_id=mapping.endpoint_id
 AND profile.business_site_id=(
     SELECT endpoint.business_site_id FROM trusted_endpoints endpoint WHERE endpoint.id=mapping.endpoint_id
 )
 AND profile.enabled=1
 AND profile.effective_from<=mapping.effective_from
 AND (profile.effective_to IS NULL OR profile.effective_to>mapping.effective_from)
WHERE mapping.protocol_code LIKE 'HJ212:%'
  AND mapping.enabled=1
  AND mapping.expected_interval_seconds IN (3600,14400)
  AND NOT EXISTS (
    SELECT 1 FROM monitoring_business_schedules existing
    WHERE existing.endpoint_id=mapping.endpoint_id
      AND existing.protocol_code=mapping.protocol_code
      AND existing.effective_from=mapping.effective_from
);

CREATE TABLE IF NOT EXISTS monitoring_business_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_observation_value_id INTEGER NOT NULL UNIQUE,
    observation_batch_id INTEGER NOT NULL,
    schedule_id INTEGER NOT NULL,
    endpoint_id INTEGER NOT NULL,
    business_site_id INTEGER NOT NULL,
    protocol_code TEXT NOT NULL,
    business_metric TEXT NOT NULL,
    instrument_asset_code TEXT,
    scheduled_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    standard_value REAL NOT NULL,
    standard_unit TEXT NOT NULL,
    quality TEXT NOT NULL CHECK (quality IN ('valid', 'suspect')),
    timeliness TEXT NOT NULL CHECK (timeliness IN ('on_time', 'late')),
    slot_state TEXT NOT NULL CHECK (slot_state IN ('selected', 'duplicate', 'conflict')),
    projection_version TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (source_observation_value_id) REFERENCES observation_values(id),
    FOREIGN KEY (observation_batch_id) REFERENCES observation_batches(id),
    FOREIGN KEY (schedule_id) REFERENCES monitoring_business_schedules(id),
    FOREIGN KEY (endpoint_id) REFERENCES trusted_endpoints(id),
    FOREIGN KEY (business_site_id) REFERENCES sites(id),
    FOREIGN KEY (protocol_code) REFERENCES monitoring_factor_definitions(protocol_code)
);

CREATE INDEX IF NOT EXISTS idx_monitoring_business_latest
    ON monitoring_business_observations(
        business_site_id, endpoint_id, business_metric, slot_state, is_current, scheduled_at DESC
    );
CREATE INDEX IF NOT EXISTS idx_monitoring_business_slot
    ON monitoring_business_observations(
        endpoint_id, protocol_code, scheduled_at, instrument_asset_code, is_current
    );
