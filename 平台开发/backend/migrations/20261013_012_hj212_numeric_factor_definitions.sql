-- Approved HJ212 numeric-code definitions for the evidenced national-station endpoint family.
-- Definitions are additive only. Endpoint mappings remain evidence-gated by application logic.
INSERT OR IGNORE INTO monitoring_factor_definitions
    (protocol_code, protocol_factor, raw_unit, parse_format, business_metric,
     standard_unit, decimal_precision, is_published)
VALUES
    ('HJ212:001', 'hj212_numeric_ph', NULL, 'text-decimal', 'ph', 'pH', 2, 1),
    ('HJ212:004', 'hj212_numeric_water_temperature', NULL, 'text-decimal', 'water_temp', 'degC', 1, 1),
    ('HJ212:006', 'hj212_numeric_conductivity', NULL, 'text-decimal', 'conductivity', 'uS/cm', 0, 1),
    ('HJ212:016', 'hj212_numeric_permanganate_index', NULL, 'text-decimal', 'codmn', 'mg/L', 1, 1),
    ('HJ212:060', 'hj212_numeric_ammonia_nitrogen', NULL, 'text-decimal', 'ammonia', 'mg/L', 2, 1),
    ('HJ212:065', 'hj212_numeric_total_nitrogen', NULL, 'text-decimal', 'total_nitrogen', 'mg/L', 2, 1),
    ('HJ212:081', 'hj212_numeric_chlorophyll_a', NULL, 'text-decimal', 'chlorophyll', 'mg/L', 3, 1),
    ('HJ212:101', 'hj212_numeric_total_phosphorus', NULL, 'text-decimal', 'total_phosphorus', 'mg/L', 3, 1),
    ('HJ212:153', 'hj212_numeric_turbidity', NULL, 'text-decimal', 'turbidity', 'NTU', 0, 1),
    ('HJ212:273', 'hj212_numeric_algae_density', NULL, 'text-decimal', 'algae_density', '个/L', 3, 1);
