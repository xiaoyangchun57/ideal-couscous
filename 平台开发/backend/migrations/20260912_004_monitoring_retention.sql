-- Retention metadata is additive. Archived evidence remains addressable by raw-frame id and digest.
CREATE TABLE IF NOT EXISTS monitoring_raw_archives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_date TEXT NOT NULL UNIQUE,
    archive_path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL,
    frame_count INTEGER NOT NULL CHECK (frame_count > 0),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS monitoring_raw_archive_frames (
    raw_frame_id INTEGER PRIMARY KEY,
    archive_id INTEGER NOT NULL,
    frame_sha256 TEXT NOT NULL,
    FOREIGN KEY (raw_frame_id) REFERENCES ingest_raw_frames(id),
    FOREIGN KEY (archive_id) REFERENCES monitoring_raw_archives(id)
);

CREATE TABLE IF NOT EXISTS monitoring_hourly_values (
    endpoint_id INTEGER NOT NULL,
    business_site_id INTEGER NOT NULL,
    protocol_code TEXT NOT NULL,
    business_metric TEXT NOT NULL,
    observed_hour TEXT NOT NULL,
    sample_count INTEGER NOT NULL CHECK (sample_count > 0),
    minimum_value REAL NOT NULL,
    maximum_value REAL NOT NULL,
    average_value REAL NOT NULL,
    standard_unit TEXT NOT NULL,
    source_last_batch_id INTEGER NOT NULL,
    aggregated_at TEXT NOT NULL,
    PRIMARY KEY (endpoint_id, protocol_code, observed_hour),
    FOREIGN KEY (endpoint_id) REFERENCES trusted_endpoints(id),
    FOREIGN KEY (business_site_id) REFERENCES sites(id),
    FOREIGN KEY (source_last_batch_id) REFERENCES observation_batches(id)
);

CREATE TABLE IF NOT EXISTS monitoring_storage_health (
    storage_key TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('healthy', 'degraded')),
    detail TEXT NOT NULL,
    checked_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_monitoring_archive_frame_archive
    ON monitoring_raw_archive_frames(archive_id, raw_frame_id);
CREATE INDEX IF NOT EXISTS idx_monitoring_hourly_site_metric_time
    ON monitoring_hourly_values(business_site_id, business_metric, observed_hour DESC);
