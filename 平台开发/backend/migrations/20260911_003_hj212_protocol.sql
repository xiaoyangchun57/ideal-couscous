-- HJ212 protocol metadata is additive. Existing raw evidence remains immutable.
CREATE TABLE IF NOT EXISTS ingest_frame_protocols (
    raw_frame_id INTEGER PRIMARY KEY,
    protocol_family TEXT NOT NULL CHECK (protocol_family IN ('sl651', 'hj212', 'unknown')),
    parser_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (raw_frame_id) REFERENCES ingest_raw_frames(id)
);

CREATE INDEX IF NOT EXISTS idx_ingest_protocol_family_raw
    ON ingest_frame_protocols(protocol_family, raw_frame_id);

INSERT OR IGNORE INTO monitoring_factor_definitions
    (protocol_code, protocol_factor, raw_unit, parse_format, business_metric, standard_unit, decimal_precision, is_published)
VALUES
    ('HJ212:w01001', 'hj212_ph', 'pH', 'text-decimal', 'ph', 'pH', 2, 1),
    ('HJ212:w01003', 'hj212_turbidity', 'NTU', 'text-decimal', 'turbidity', 'NTU', 0, 1),
    ('HJ212:w01009', 'hj212_dissolved_oxygen', 'mg/L', 'text-decimal', 'dissolved_oxygen', 'mg/L', 1, 1),
    ('HJ212:w01010', 'hj212_water_temperature', 'degC', 'text-decimal', 'water_temp', 'degC', 1, 1),
    ('HJ212:w01014', 'hj212_conductivity', 'uS/cm', 'text-decimal', 'conductivity', 'uS/cm', 0, 1),
    -- Only the real-time water-sample value is eligible for this ingestion path.
    ('HJ212:w01019-Rtd', 'hj212_permanganate_index_realtime', 'mg/L', 'text-decimal', 'codmn', 'mg/L', 1, 1),
    ('HJ212:w21001', 'hj212_total_nitrogen', 'mg/L', 'text-decimal', 'total_nitrogen', 'mg/L', 2, 1),
    ('HJ212:w21003', 'hj212_ammonia_nitrogen', 'mg/L', 'text-decimal', 'ammonia', 'mg/L', 2, 1),
    ('HJ212:w21011', 'hj212_total_phosphorus', 'mg/L', 'text-decimal', 'total_phosphorus', 'mg/L', 3, 1),
    -- Preserve vendor legacy codes as map-able aliases; never rewrite received evidence to w201xx.
    ('HJ212:022', 'hj212_legacy_w20115_total_tin', 'ug/L', 'text-decimal', 'total_tin', 'ug/L', 3, 1),
    ('HJ212:027', 'hj212_legacy_w20120_total_lead', 'ug/L', 'text-decimal', 'total_lead', 'ug/L', 3, 1),
    ('HJ212:029', 'hj212_legacy_w20122_total_copper', 'mg/L', 'text-decimal', 'total_copper', 'mg/L', 3, 1),
    ('HJ212:030', 'hj212_legacy_w20123_total_zinc', 'mg/L', 'text-decimal', 'total_zinc', 'mg/L', 3, 1);
