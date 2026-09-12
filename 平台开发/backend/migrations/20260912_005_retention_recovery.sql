-- Recovery-safe retention uses append-only archive parts. content_sha256 is the
-- SHA-256 of the decompressed canonical JSONL records, never a gzip byte digest.
CREATE TABLE IF NOT EXISTS monitoring_raw_archive_parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_date TEXT NOT NULL,
    archive_path TEXT NOT NULL UNIQUE,
    content_sha256 TEXT NOT NULL UNIQUE,
    frame_count INTEGER NOT NULL CHECK (frame_count > 0),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS monitoring_raw_archive_part_frames (
    raw_frame_id INTEGER PRIMARY KEY,
    archive_part_id INTEGER NOT NULL,
    frame_sha256 TEXT NOT NULL,
    FOREIGN KEY (raw_frame_id) REFERENCES ingest_raw_frames(id),
    FOREIGN KEY (archive_part_id) REFERENCES monitoring_raw_archive_parts(id)
);

CREATE INDEX IF NOT EXISTS idx_monitoring_archive_part_day
    ON monitoring_raw_archive_parts(archive_date, id);
CREATE INDEX IF NOT EXISTS idx_monitoring_archive_part_frame
    ON monitoring_raw_archive_part_frames(archive_part_id, raw_frame_id);

-- v1 retained a narrower primary key. Do not mutate it in place: existing
-- migration checks deliberately reject schema rewrites. New maintenance writes
-- only this complete-series table.
CREATE TABLE IF NOT EXISTS monitoring_hourly_value_series (
    endpoint_id INTEGER NOT NULL,
    business_site_id INTEGER NOT NULL,
    protocol_code TEXT NOT NULL,
    business_metric TEXT NOT NULL,
    instrument_asset_code TEXT NOT NULL DEFAULT '',
    observed_hour TEXT NOT NULL,
    sample_count INTEGER NOT NULL CHECK (sample_count > 0),
    minimum_value REAL NOT NULL,
    maximum_value REAL NOT NULL,
    average_value REAL NOT NULL,
    standard_unit TEXT NOT NULL,
    source_last_batch_id INTEGER NOT NULL,
    aggregated_at TEXT NOT NULL,
    PRIMARY KEY (endpoint_id, protocol_code, business_metric, instrument_asset_code, standard_unit, observed_hour),
    FOREIGN KEY (endpoint_id) REFERENCES trusted_endpoints(id),
    FOREIGN KEY (business_site_id) REFERENCES sites(id),
    FOREIGN KEY (source_last_batch_id) REFERENCES observation_batches(id)
);

CREATE INDEX IF NOT EXISTS idx_monitoring_hourly_series_site_metric_time
    ON monitoring_hourly_value_series(business_site_id, business_metric, observed_hour DESC);
