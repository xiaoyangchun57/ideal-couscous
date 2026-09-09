-- Station ingestion evidence base. Additive only; no existing business table is altered.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    app_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trusted_endpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station_code TEXT NOT NULL UNIQUE,
    credential_hmac TEXT NOT NULL,
    business_site_id INTEGER,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    endpoint_state TEXT NOT NULL DEFAULT 'unbound' CHECK (endpoint_state IN ('unbound', 'bound', 'disabled')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (business_site_id) REFERENCES sites(id)
);

CREATE TABLE IF NOT EXISTS ingest_raw_frames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id INTEGER,
    station_code TEXT,
    received_at TEXT NOT NULL,
    frame_sha256 TEXT NOT NULL,
    logical_key_sha256 TEXT,
    raw_frame BLOB NOT NULL,
    body_length INTEGER,
    crc_status TEXT NOT NULL CHECK (crc_status IN ('valid', 'invalid', 'not_checked')),
    authentication_status TEXT NOT NULL CHECK (authentication_status IN ('authenticated', 'unbound_authenticated', 'unknown_endpoint', 'credential_failed', 'not_checked')),
    disposition TEXT NOT NULL CHECK (disposition IN ('accepted', 'duplicate', 'quarantined', 'pending_parse', 'pending_reparse')),
    duplicate_of_raw_frame_id INTEGER,
    persistence_state TEXT NOT NULL DEFAULT 'persisted' CHECK (persistence_state IN ('persisted', 'pending_parse', 'pending_reparse')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (endpoint_id) REFERENCES trusted_endpoints(id),
    FOREIGN KEY (duplicate_of_raw_frame_id) REFERENCES ingest_raw_frames(id)
);

CREATE TABLE IF NOT EXISTS ingest_parse_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_frame_id INTEGER NOT NULL,
    parser_version TEXT NOT NULL,
    parse_status TEXT NOT NULL CHECK (parse_status IN ('parsed_header', 'failed_header', 'pending_reparse')),
    error_code TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (raw_frame_id) REFERENCES ingest_raw_frames(id)
);

CREATE TABLE IF NOT EXISTS ingest_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_frame_id INTEGER,
    error_type TEXT NOT NULL,
    error_detail TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'suppressed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (raw_frame_id) REFERENCES ingest_raw_frames(id)
);

CREATE INDEX IF NOT EXISTS idx_ingest_raw_received ON ingest_raw_frames(received_at, id);
CREATE INDEX IF NOT EXISTS idx_ingest_raw_endpoint_received ON ingest_raw_frames(endpoint_id, received_at DESC, id);
CREATE INDEX IF NOT EXISTS idx_ingest_raw_frame_hash ON ingest_raw_frames(frame_sha256, received_at);
CREATE INDEX IF NOT EXISTS idx_ingest_raw_disposition_received ON ingest_raw_frames(disposition, received_at, id);
CREATE INDEX IF NOT EXISTS idx_ingest_raw_logical_key ON ingest_raw_frames(endpoint_id, logical_key_sha256, id);
CREATE INDEX IF NOT EXISTS idx_ingest_parse_pending ON ingest_parse_attempts(parse_status, parser_version, raw_frame_id);
CREATE INDEX IF NOT EXISTS idx_ingest_errors_open ON ingest_errors(error_type, status, created_at, id);
