-- Keep station history addressable while separating the active master catalogue.
-- The CREATE is a no-op for business databases and keeps explicit isolated empty-db tests supported.
CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL
);

ALTER TABLE sites
    ADD COLUMN master_status TEXT NOT NULL DEFAULT 'active'
    CHECK (master_status IN ('active', 'retired'));

CREATE TABLE IF NOT EXISTS site_name_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL,
    alias_name TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'station_master_refresh',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (site_id) REFERENCES sites(id),
    UNIQUE(site_id, normalized_alias)
);

CREATE INDEX IF NOT EXISTS idx_site_name_aliases_normalized
    ON site_name_aliases(normalized_alias, site_id);

CREATE TABLE IF NOT EXISTS station_master_refresh_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_fingerprint TEXT NOT NULL UNIQUE,
    preview_fingerprint TEXT NOT NULL UNIQUE,
    accepted_rows INTEGER NOT NULL,
    exact_mn_count INTEGER NOT NULL,
    alias_match_count INTEGER NOT NULL,
    created_count INTEGER NOT NULL,
    retired_count INTEGER NOT NULL,
    rebound_endpoint_count INTEGER NOT NULL,
    disabled_endpoint_count INTEGER NOT NULL,
    applied_at TEXT NOT NULL
);
