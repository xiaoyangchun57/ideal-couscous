-- Versioned station-level expected-factor selections.
-- Protocol mappings remain ingestion facts; these tables define the business denominator.
CREATE TABLE IF NOT EXISTS station_expected_factor_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL REFERENCES sites(id),
    version INTEGER NOT NULL CHECK (version > 0),
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    previous_metrics_json TEXT NOT NULL DEFAULT '[]',
    selected_metrics_json TEXT NOT NULL,
    changed_by_user_id INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (effective_to IS NULL OR effective_to > effective_from),
    UNIQUE (site_id, version),
    UNIQUE (site_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS station_expected_factors (
    config_id INTEGER NOT NULL REFERENCES station_expected_factor_configs(id),
    site_id INTEGER NOT NULL REFERENCES sites(id),
    business_metric TEXT NOT NULL,
    display_order INTEGER NOT NULL CHECK (display_order >= 0),
    effective_from TEXT NOT NULL,
    PRIMARY KEY (config_id, business_metric)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_station_expected_factor_current
    ON station_expected_factor_configs(site_id)
    WHERE effective_to IS NULL;

CREATE INDEX IF NOT EXISTS idx_station_expected_factor_effective
    ON station_expected_factor_configs(site_id, effective_from DESC, effective_to, id DESC);

CREATE INDEX IF NOT EXISTS idx_station_expected_factor_metric
    ON station_expected_factors(site_id, business_metric, config_id);
