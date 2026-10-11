-- Bounded read paths for the post-deployment station monitoring UI.
CREATE INDEX IF NOT EXISTS idx_ingest_raw_authenticated_endpoint_received
    ON ingest_raw_frames(endpoint_id, received_at DESC, id)
    WHERE authentication_status='authenticated';

CREATE INDEX IF NOT EXISTS idx_ingest_raw_authenticated_received
    ON ingest_raw_frames(received_at DESC, id)
    WHERE authentication_status='authenticated';

CREATE INDEX IF NOT EXISTS idx_ingest_raw_station_auth_received
    ON ingest_raw_frames(station_code, received_at DESC, id)
    WHERE authentication_status='credential_failed';

CREATE INDEX IF NOT EXISTS idx_monitoring_business_current_site_scheduled
    ON monitoring_business_observations(
        business_site_id, scheduled_at DESC, endpoint_id, business_metric,
        instrument_asset_code, protocol_code, id
    ) WHERE is_current=1;

CREATE INDEX IF NOT EXISTS idx_monitoring_business_valid_endpoint_factor
    ON monitoring_business_observations(
        endpoint_id, business_site_id, protocol_code, business_metric,
        instrument_asset_code, projection_version, observation_batch_id
    ) WHERE is_current=1 AND quality='valid';
