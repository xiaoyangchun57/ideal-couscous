-- Bound the due-work scan to rows that can still require normalization.
CREATE INDEX IF NOT EXISTS idx_ingest_raw_pending_normalization
ON ingest_raw_frames(id)
WHERE persistence_state IN ('pending_parse', 'pending_reparse');
