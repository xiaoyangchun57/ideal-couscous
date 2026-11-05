CREATE TABLE station_ingest_retention_runs_next (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cutoff_time TEXT NOT NULL,
    archived_parse_attempts INTEGER NOT NULL DEFAULT 0,
    archived_errors INTEGER NOT NULL DEFAULT 0,
    archived_error_summaries INTEGER NOT NULL DEFAULT 0,
    deleted_raw_frames INTEGER NOT NULL DEFAULT 0,
    retained_protected_frames INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('completed', 'partial', 'failed')),
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);

INSERT INTO station_ingest_retention_runs_next(
    id,cutoff_time,archived_parse_attempts,archived_errors,
    archived_error_summaries,deleted_raw_frames,retained_protected_frames,
    status,detail,created_at
)
SELECT id,cutoff_time,archived_parse_attempts,archived_errors,
       archived_error_summaries,deleted_raw_frames,retained_protected_frames,
       CASE
           WHEN status='completed' AND json_extract(detail, '$.status')='partial' THEN 'partial'
           ELSE status
       END,
       detail,created_at
FROM station_ingest_retention_runs;

DROP TABLE station_ingest_retention_runs;
ALTER TABLE station_ingest_retention_runs_next RENAME TO station_ingest_retention_runs;
