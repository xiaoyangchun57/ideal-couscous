CREATE TABLE IF NOT EXISTS monitoring_hourly_last_values (
    endpoint_id INTEGER NOT NULL,
    business_site_id INTEGER NOT NULL,
    protocol_code TEXT NOT NULL,
    business_metric TEXT NOT NULL,
    instrument_asset_code TEXT NOT NULL DEFAULT '',
    sample_hour TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    normalized_at TEXT NOT NULL,
    raw_value_text TEXT,
    raw_unit TEXT,
    standard_value REAL NOT NULL,
    standard_unit TEXT NOT NULL,
    quality TEXT NOT NULL CHECK (quality = 'valid'),
    parser_version TEXT NOT NULL,
    function_code INTEGER,
    normalization_version TEXT NOT NULL,
    source_observation_value_id INTEGER NOT NULL,
    source_raw_frame_id INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (
        endpoint_id, protocol_code, business_metric,
        instrument_asset_code, standard_unit, sample_hour
    ),
    FOREIGN KEY (endpoint_id) REFERENCES trusted_endpoints(id),
    FOREIGN KEY (business_site_id) REFERENCES sites(id),
    FOREIGN KEY (protocol_code) REFERENCES monitoring_factor_definitions(protocol_code)
);

CREATE INDEX IF NOT EXISTS idx_monitoring_hourly_last_site_metric_time
    ON monitoring_hourly_last_values(
        business_site_id, business_metric, sample_hour DESC, observed_at DESC
    );

CREATE INDEX IF NOT EXISTS idx_monitoring_hourly_last_latest
    ON monitoring_hourly_last_values(
        business_site_id, endpoint_id, business_metric,
        instrument_asset_code, observed_at DESC
    );
