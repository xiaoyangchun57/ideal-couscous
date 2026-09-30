-- Preserve the confirmed two-hour analysis delay as schedule data instead of
-- inferring it from minute transport cadence.
ALTER TABLE monitoring_business_schedules
    ADD COLUMN result_delay_seconds INTEGER NOT NULL DEFAULT 0
    CHECK (result_delay_seconds BETWEEN 0 AND interval_seconds);

UPDATE monitoring_business_schedules
SET result_delay_seconds=7200
WHERE interval_seconds=14400
  AND protocol_code LIKE 'HJ212:%';
