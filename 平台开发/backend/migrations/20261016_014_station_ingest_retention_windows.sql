-- Bounded station-ingestion hot state and recoverable archive references.
-- Historical migrations remain immutable; index removal is an explicit maintenance action
-- after these directories have been backfilled and verified on an isolated copy.

CREATE TABLE IF NOT EXISTS station_ingest_current_state (
    source_key TEXT PRIMARY KEY,
    endpoint_id INTEGER,
    station_code TEXT,
    latest_received_at TEXT,
    latest_raw_frame_id INTEGER,
    latest_authenticated_at TEXT,
    latest_authenticated_raw_frame_id INTEGER,
    latest_credential_failed_at TEXT,
    latest_credential_failed_raw_frame_id INTEGER,
    latest_error_at TEXT,
    latest_error_type TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (endpoint_id) REFERENCES trusted_endpoints(id)
);

CREATE INDEX IF NOT EXISTS idx_station_ingest_current_endpoint
    ON station_ingest_current_state(endpoint_id, source_key);
CREATE INDEX IF NOT EXISTS idx_station_ingest_current_station
    ON station_ingest_current_state(station_code, source_key);

CREATE TABLE IF NOT EXISTS station_ingest_recent_receipts (
    raw_frame_id INTEGER PRIMARY KEY,
    source_key TEXT NOT NULL,
    endpoint_id INTEGER,
    station_code TEXT,
    received_at TEXT NOT NULL,
    authentication_status TEXT NOT NULL,
    FOREIGN KEY (endpoint_id) REFERENCES trusted_endpoints(id)
);

CREATE INDEX IF NOT EXISTS idx_station_ingest_recent_received
    ON station_ingest_recent_receipts(received_at, raw_frame_id);
CREATE INDEX IF NOT EXISTS idx_station_ingest_recent_endpoint_auth
    ON station_ingest_recent_receipts(endpoint_id, authentication_status, received_at DESC, raw_frame_id);
CREATE INDEX IF NOT EXISTS idx_station_ingest_recent_station_auth
    ON station_ingest_recent_receipts(station_code, authentication_status, received_at DESC, raw_frame_id);

CREATE TABLE IF NOT EXISTS station_ingest_receipt_hourly (
    source_key TEXT NOT NULL,
    endpoint_id INTEGER,
    station_code TEXT,
    authentication_status TEXT NOT NULL,
    bucket_hour TEXT NOT NULL,
    receipt_count INTEGER NOT NULL CHECK (receipt_count > 0),
    first_received_at TEXT NOT NULL,
    last_received_at TEXT NOT NULL,
    last_complete_raw_frame_id INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (source_key, authentication_status, bucket_hour),
    FOREIGN KEY (endpoint_id) REFERENCES trusted_endpoints(id)
);

CREATE INDEX IF NOT EXISTS idx_station_ingest_receipt_hour
    ON station_ingest_receipt_hourly(bucket_hour, authentication_status, source_key);

CREATE TABLE IF NOT EXISTS station_ingest_slot_summaries (
    endpoint_id INTEGER NOT NULL,
    station_code TEXT NOT NULL,
    command TEXT NOT NULL,
    slot_started_at TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    canonical_raw_frame_id INTEGER NOT NULL,
    canonical_frame_sha256 TEXT NOT NULL,
    receipt_count INTEGER NOT NULL CHECK (receipt_count > 0),
    first_received_at TEXT NOT NULL,
    last_received_at TEXT NOT NULL,
    last_authentication_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (endpoint_id, command, slot_started_at, content_sha256),
    FOREIGN KEY (endpoint_id) REFERENCES trusted_endpoints(id)
);

CREATE INDEX IF NOT EXISTS idx_station_ingest_slot_summary_retention
    ON station_ingest_slot_summaries(last_received_at, endpoint_id, slot_started_at);

CREATE TABLE IF NOT EXISTS monitoring_raw_archive_part_metadata (
    archive_part_id INTEGER PRIMARY KEY,
    format_version INTEGER NOT NULL CHECK (format_version >= 1),
    verified_at TEXT NOT NULL,
    first_raw_frame_id INTEGER NOT NULL,
    last_raw_frame_id INTEGER NOT NULL,
    first_received_at TEXT NOT NULL,
    last_received_at TEXT NOT NULL,
    record_count INTEGER NOT NULL CHECK (record_count > 0),
    mirror_path TEXT,
    mirror_verified_at TEXT,
    local_deleted_at TEXT,
    FOREIGN KEY (archive_part_id) REFERENCES monitoring_raw_archive_parts(id)
);

CREATE TABLE IF NOT EXISTS monitoring_archive_evidence_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_raw_frame_id INTEGER NOT NULL UNIQUE,
    archive_part_id INTEGER NOT NULL,
    archive_record_index INTEGER NOT NULL CHECK (archive_record_index >= 0),
    record_sha256 TEXT NOT NULL,
    frame_sha256 TEXT NOT NULL,
    received_at TEXT NOT NULL,
    endpoint_id INTEGER,
    station_code TEXT,
    protocol_family TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    logical_key_sha256 TEXT,
    crc_status TEXT NOT NULL,
    authentication_status TEXT NOT NULL,
    disposition TEXT NOT NULL,
    body_length INTEGER,
    context_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (archive_part_id) REFERENCES monitoring_raw_archive_parts(id),
    FOREIGN KEY (endpoint_id) REFERENCES trusted_endpoints(id),
    UNIQUE(archive_part_id, archive_record_index)
);

CREATE INDEX IF NOT EXISTS idx_monitoring_evidence_archive_record
    ON monitoring_archive_evidence_refs(archive_part_id, archive_record_index);
CREATE INDEX IF NOT EXISTS idx_monitoring_evidence_received
    ON monitoring_archive_evidence_refs(received_at, source_raw_frame_id);

CREATE TABLE IF NOT EXISTS station_ingest_dedup_keys (
    endpoint_id INTEGER NOT NULL,
    logical_key_sha256 TEXT NOT NULL,
    canonical_raw_frame_id INTEGER NOT NULL,
    canonical_evidence_ref_id INTEGER,
    first_received_at TEXT NOT NULL,
    last_received_at TEXT NOT NULL,
    expires_at TEXT,
    protected INTEGER NOT NULL DEFAULT 0 CHECK (protected IN (0, 1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (endpoint_id, logical_key_sha256),
    FOREIGN KEY (endpoint_id) REFERENCES trusted_endpoints(id),
    FOREIGN KEY (canonical_evidence_ref_id) REFERENCES monitoring_archive_evidence_refs(id)
);

CREATE INDEX IF NOT EXISTS idx_station_ingest_dedup_expiry
    ON station_ingest_dedup_keys(protected, expires_at, endpoint_id, logical_key_sha256);

CREATE TABLE IF NOT EXISTS station_ingest_duplicate_links (
    raw_frame_id INTEGER PRIMARY KEY,
    canonical_raw_frame_id INTEGER NOT NULL,
    canonical_evidence_ref_id INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (raw_frame_id) REFERENCES ingest_raw_frames(id),
    FOREIGN KEY (canonical_evidence_ref_id) REFERENCES monitoring_archive_evidence_refs(id)
);

CREATE TABLE IF NOT EXISTS station_ingest_error_hourly (
    source_key TEXT NOT NULL,
    endpoint_id INTEGER,
    station_code TEXT,
    error_type TEXT NOT NULL,
    bucket_hour TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL CHECK (occurrence_count > 0),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_raw_frame_id INTEGER,
    PRIMARY KEY (source_key, error_type, bucket_hour),
    FOREIGN KEY (endpoint_id) REFERENCES trusted_endpoints(id)
);

CREATE INDEX IF NOT EXISTS idx_station_ingest_error_hour
    ON station_ingest_error_hourly(bucket_hour, error_type, source_key);

CREATE TABLE IF NOT EXISTS station_ingest_detail_archive_parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detail_kind TEXT NOT NULL CHECK (detail_kind IN ('parse_attempt', 'ingest_error', 'error_summary')),
    archive_path TEXT NOT NULL UNIQUE,
    content_sha256 TEXT NOT NULL UNIQUE,
    first_source_id INTEGER NOT NULL,
    last_source_id INTEGER NOT NULL,
    record_count INTEGER NOT NULL CHECK (record_count > 0),
    range_start TEXT NOT NULL,
    range_end TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    mirror_path TEXT,
    mirror_verified_at TEXT,
    local_deleted_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_station_ingest_detail_range
    ON station_ingest_detail_archive_parts(detail_kind, range_end, id);

CREATE TABLE IF NOT EXISTS station_ingest_retention_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cutoff_time TEXT NOT NULL,
    archived_parse_attempts INTEGER NOT NULL DEFAULT 0,
    archived_errors INTEGER NOT NULL DEFAULT 0,
    archived_error_summaries INTEGER NOT NULL DEFAULT 0,
    deleted_raw_frames INTEGER NOT NULL DEFAULT 0,
    retained_protected_frames INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS station_ingest_cutover_state (
    cutover_name TEXT PRIMARY KEY,
    migration_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    raw_frame_count INTEGER,
    raw_max_id INTEGER,
    current_state_count INTEGER,
    authenticated_source_count INTEGER,
    recent_receipt_count INTEGER,
    receipt_hour_count INTEGER,
    detail TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
