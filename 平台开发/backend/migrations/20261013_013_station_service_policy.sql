CREATE TABLE station_service_policy_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    previous_service_status TEXT NOT NULL
        CHECK (previous_service_status IN ('active', 'ended')),
    service_status TEXT NOT NULL
        CHECK (service_status IN ('active', 'ended')),
    previous_reporting_condition TEXT NOT NULL
        CHECK (previous_reporting_condition IN ('normal', 'temporary_stop', 'no_upload_condition')),
    reporting_condition TEXT NOT NULL
        CHECK (reporting_condition IN ('normal', 'temporary_stop', 'no_upload_condition')),
    effective_from TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    changed_by_user_id INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (site_id) REFERENCES sites(id),
    FOREIGN KEY (changed_by_user_id) REFERENCES users(id),
    UNIQUE (site_id, version),
    UNIQUE (site_id, idempotency_key)
);

CREATE INDEX idx_station_service_policy_site_version
    ON station_service_policy_events(site_id, version DESC);

CREATE INDEX idx_station_service_policy_site_effective
    ON station_service_policy_events(site_id, effective_from, version);
