-- Station monitoring normalization. Additive only; legacy sensor tables remain untouched.
CREATE TABLE IF NOT EXISTS monitoring_endpoint_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id INTEGER NOT NULL,
    business_site_id INTEGER NOT NULL,
    rtu_asset_code TEXT,
    instrument_asset_code TEXT,
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    expected_granularity TEXT,
    expected_interval_seconds INTEGER CHECK (expected_interval_seconds IS NULL OR expected_interval_seconds > 0),
    effective_from TEXT NOT NULL CHECK (substr(effective_from, -6) = '+00:00'),
    effective_to TEXT CHECK (effective_to IS NULL OR substr(effective_to, -6) = '+00:00'),
    FOREIGN KEY (endpoint_id) REFERENCES trusted_endpoints(id),
    FOREIGN KEY (business_site_id) REFERENCES sites(id),
    UNIQUE(endpoint_id, effective_from)
);

CREATE TABLE IF NOT EXISTS monitoring_factor_definitions (
    protocol_code TEXT PRIMARY KEY,
    protocol_factor TEXT NOT NULL UNIQUE,
    raw_unit TEXT,
    parse_format TEXT NOT NULL,
    business_metric TEXT,
    standard_unit TEXT,
    decimal_precision INTEGER NOT NULL DEFAULT 3 CHECK (decimal_precision BETWEEN 0 AND 8),
    is_published INTEGER NOT NULL DEFAULT 1 CHECK (is_published IN (0, 1))
);

CREATE TABLE IF NOT EXISTS monitoring_factor_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id INTEGER NOT NULL,
    protocol_code TEXT NOT NULL,
    business_metric TEXT,
    instrument_asset_code TEXT,
    expected_interval_seconds INTEGER CHECK (expected_interval_seconds IS NULL OR expected_interval_seconds > 0),
    tolerance_seconds INTEGER CHECK (tolerance_seconds IS NULL OR tolerance_seconds >= 0),
    effective_from TEXT NOT NULL CHECK (substr(effective_from, -6) = '+00:00'),
    effective_to TEXT CHECK (effective_to IS NULL OR substr(effective_to, -6) = '+00:00'),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    FOREIGN KEY (endpoint_id) REFERENCES trusted_endpoints(id),
    FOREIGN KEY (protocol_code) REFERENCES monitoring_factor_definitions(protocol_code)
);

CREATE TABLE IF NOT EXISTS observation_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_frame_id INTEGER NOT NULL,
    endpoint_id INTEGER NOT NULL,
    business_site_id INTEGER NOT NULL,
    function_code INTEGER NOT NULL,
    serial_number INTEGER NOT NULL,
    reported_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    granularity TEXT NOT NULL,
    aggregation_source TEXT NOT NULL CHECK (aggregation_source IN ('device_reported', 'server_aggregated')),
    idempotency_key TEXT NOT NULL,
    normalization_version TEXT NOT NULL,
    batch_status TEXT NOT NULL CHECK (batch_status IN ('accepted', 'partial', 'rejected')),
    projection_state TEXT NOT NULL CHECK (projection_state IN ('completed', 'failed_retryable', 'superseded')),
    error_code TEXT,
    replaces_batch_id INTEGER,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    normalized_at TEXT NOT NULL,
    FOREIGN KEY (raw_frame_id) REFERENCES ingest_raw_frames(id),
    FOREIGN KEY (endpoint_id) REFERENCES trusted_endpoints(id),
    FOREIGN KEY (business_site_id) REFERENCES sites(id),
    FOREIGN KEY (replaces_batch_id) REFERENCES observation_batches(id),
    UNIQUE(raw_frame_id, normalization_version),
    UNIQUE(endpoint_id, idempotency_key, normalization_version)
);

CREATE TABLE IF NOT EXISTS observation_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_batch_id INTEGER NOT NULL,
    protocol_code TEXT NOT NULL,
    business_metric TEXT,
    raw_value REAL,
    raw_unit TEXT,
    standard_value REAL,
    standard_unit TEXT,
    quality TEXT NOT NULL CHECK (quality IN ('valid', 'suspect', 'invalid', 'fault', 'unmapped')),
    instrument_asset_code TEXT,
    parser_version TEXT NOT NULL,
    is_published INTEGER NOT NULL DEFAULT 1 CHECK (is_published IN (0, 1)),
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    FOREIGN KEY (observation_batch_id) REFERENCES observation_batches(id),
    FOREIGN KEY (protocol_code) REFERENCES monitoring_factor_definitions(protocol_code),
    UNIQUE(observation_batch_id, protocol_code)
);

CREATE TABLE IF NOT EXISTS monitoring_status_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_batch_id INTEGER,
    endpoint_id INTEGER NOT NULL,
    business_site_id INTEGER NOT NULL,
    event_axis TEXT NOT NULL CHECK (event_axis IN ('communication', 'rtu', 'power', 'signal', 'instrument')),
    event_type TEXT NOT NULL,
    event_value TEXT,
    occurred_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    source TEXT NOT NULL,
    FOREIGN KEY (observation_batch_id) REFERENCES observation_batches(id),
    FOREIGN KEY (endpoint_id) REFERENCES trusted_endpoints(id),
    FOREIGN KEY (business_site_id) REFERENCES sites(id)
);

CREATE TABLE IF NOT EXISTS monitoring_quality_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_frame_id INTEGER,
    observation_batch_id INTEGER,
    business_site_id INTEGER,
    issue_type TEXT NOT NULL,
    object_summary TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'suppressed')),
    reparse_allowed INTEGER NOT NULL DEFAULT 1 CHECK (reparse_allowed IN (0, 1)),
    FOREIGN KEY (raw_frame_id) REFERENCES ingest_raw_frames(id),
    FOREIGN KEY (observation_batch_id) REFERENCES observation_batches(id),
    FOREIGN KEY (business_site_id) REFERENCES sites(id)
);

CREATE TABLE IF NOT EXISTS monitoring_normalization_retries (
    raw_frame_id INTEGER NOT NULL,
    normalization_version TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 3),
    next_attempt_at TEXT,
    last_error TEXT,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'retrying', 'exhausted')),
    PRIMARY KEY (raw_frame_id, normalization_version),
    FOREIGN KEY (raw_frame_id) REFERENCES ingest_raw_frames(id)
);

INSERT OR IGNORE INTO monitoring_factor_definitions
    (protocol_code, protocol_factor, raw_unit, parse_format, business_metric, standard_unit, decimal_precision, is_published)
VALUES
    ('0311', 'water_temperature', 'degC', 'N(3,1)', 'water_temp', 'degC', 1, 1),
    ('4612', 'ph', 'pH', 'N(4,2)', 'ph', 'pH', 2, 1),
    ('4711', 'dissolved_oxygen', 'mg/L', 'N(4,1)', 'dissolved_oxygen', 'mg/L', 1, 1),
    ('4818', 'conductivity', 'uS/cm', 'N(5)', 'conductivity', 'uS/cm', 0, 1),
    ('4910', 'turbidity', 'NTU', 'N(3)', 'turbidity', 'NTU', 0, 1),
    ('4A11', 'permanganate_index', 'mg/L', 'N(4,1)', 'codmn', 'mg/L', 1, 1),
    ('4B19', 'oxidation_reduction_potential', 'mV', 'N(5,1)', NULL, 'mV', 1, 0),
    ('4C1A', 'ammonia_nitrogen', 'mg/L', 'N(6,2)', 'ammonia', 'mg/L', 2, 1),
    ('4D1B', 'total_phosphorus', 'mg/L', 'N(5,3)', 'total_phosphorus', 'mg/L', 3, 1),
    ('4E1A', 'total_nitrogen', 'mg/L', 'N(5,2)', 'total_nitrogen', 'mg/L', 2, 1),
    ('4F12', 'total_organic_carbon', 'mg/L', 'N(4,2)', 'total_organic_carbon', 'mg/L', 2, 1),
    ('4520', 'rtu_status', NULL, 'X(4)', NULL, NULL, 0, 0),
    ('3812', 'power_voltage', 'V', 'N(4,2)', NULL, 'V', 2, 0),
    ('FF0108', 'signal_strength', NULL, 'N(2)', NULL, NULL, 0, 0);

CREATE INDEX IF NOT EXISTS idx_monitoring_mapping_effective
    ON monitoring_factor_mappings(endpoint_id, protocol_code, effective_from, effective_to, enabled);
CREATE TRIGGER IF NOT EXISTS reject_overlapping_monitoring_factor_mapping_insert
BEFORE INSERT ON monitoring_factor_mappings
WHEN NEW.enabled = 1
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM monitoring_factor_mappings existing
        WHERE existing.endpoint_id = NEW.endpoint_id
          AND existing.protocol_code = NEW.protocol_code
          AND existing.enabled = 1
          AND NEW.effective_from < COALESCE(existing.effective_to, '9999-12-31T23:59:59+00:00')
          AND existing.effective_from < COALESCE(NEW.effective_to, '9999-12-31T23:59:59+00:00')
    ) THEN RAISE(ABORT, 'overlapping factor mapping') END;
END;
CREATE TRIGGER IF NOT EXISTS reject_overlapping_monitoring_factor_mapping_update
BEFORE UPDATE OF endpoint_id, protocol_code, effective_from, effective_to, enabled ON monitoring_factor_mappings
WHEN NEW.enabled = 1
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM monitoring_factor_mappings existing
        WHERE existing.id <> NEW.id
          AND existing.endpoint_id = NEW.endpoint_id
          AND existing.protocol_code = NEW.protocol_code
          AND existing.enabled = 1
          AND NEW.effective_from < COALESCE(existing.effective_to, '9999-12-31T23:59:59+00:00')
          AND existing.effective_from < COALESCE(NEW.effective_to, '9999-12-31T23:59:59+00:00')
    ) THEN RAISE(ABORT, 'overlapping factor mapping') END;
END;
CREATE INDEX IF NOT EXISTS idx_monitoring_profile_effective
    ON monitoring_endpoint_profiles(endpoint_id, effective_from, effective_to, enabled);
CREATE INDEX IF NOT EXISTS idx_observation_batch_site_observed
    ON observation_batches(business_site_id, observed_at DESC, id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_observation_current_raw
    ON observation_batches(raw_frame_id) WHERE is_current = 1;
CREATE INDEX IF NOT EXISTS idx_observation_values_metric_current
    ON observation_values(business_metric, is_current, observation_batch_id);
CREATE INDEX IF NOT EXISTS idx_status_site_axis_occurred
    ON monitoring_status_events(business_site_id, event_axis, occurred_at DESC, id);
CREATE INDEX IF NOT EXISTS idx_quality_site_status_recent
    ON monitoring_quality_issues(business_site_id, status, last_seen_at DESC, id);
CREATE INDEX IF NOT EXISTS idx_normalization_retries_due
    ON monitoring_normalization_retries(state, next_attempt_at, raw_frame_id);
