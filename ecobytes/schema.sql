-- ==========================================================
-- EcoBytes: AirSense AI Production SQL Schema (SQLite / PostgreSQL)
-- Central Pollution Control Board (CPCB) NAQI Architecture
-- ==========================================================

-- 1. Locations Table (Delhi NCR Monitoring Stations)
CREATE TABLE IF NOT EXISTS locations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    zone TEXT NOT NULL
);

-- Seed Initial Delhi NCR Monitoring Zones
INSERT OR IGNORE INTO locations (id, name, latitude, longitude, zone) VALUES
('delhi-cp', 'Connaught Place (Central Delhi)', 28.6328, 77.2197, 'Central'),
('delhi-anand-vihar', 'Anand Vihar (East Delhi - High Dust Hub)', 28.6469, 77.3160, 'East'),
('delhi-igi', 'Indira Gandhi International Airport T3', 28.5562, 77.1000, 'South-West'),
('delhi-punjabi-bagh', 'Punjabi Bagh (West Delhi Residential/Traffic)', 28.6692, 77.1264, 'West'),
('delhi-noida', 'Sector 62 Noida (NCR Industrial/IT Zone)', 28.6280, 77.3649, 'NCR East'),
('delhi-gurugram', 'Cyber Hub Gurugram (NCR Commercial Corridor)', 28.4950, 77.0895, 'NCR South');

-- 2. Environmental Observations (Raw & Normalized Time Series)
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id TEXT,
    recorded_at TEXT NOT NULL,
    -- CPCB Monitored Pollutants (ug/m3, CO in mg/m3)
    pm25 REAL,
    pm10 REAL,
    no2 REAL,
    o3 REAL,
    so2 REAL,
    co REAL,
    -- Meteorological Parameters
    temperature REAL,
    humidity REAL,
    wind_speed REAL,
    wind_direction REAL,
    pressure REAL,
    rainfall REAL,
    cloud_cover REAL,
    -- Indian CPCB Calculated Sub-indices & Provenance
    cpcb_aqi INTEGER,
    category TEXT,
    source TEXT DEFAULT 'synthetic_demo',
    provider TEXT DEFAULT 'CPCB / CAAQMS',
    FOREIGN KEY (location_id) REFERENCES locations(id)
);

CREATE INDEX IF NOT EXISTS idx_observations_loc_time ON observations (location_id, recorded_at DESC);

-- 3. SMS Alert Notification Logs
-- Trigger Rules: PM2.5 > 50 ug/m3 OR PM10 > 120 ug/m3
CREATE TABLE IF NOT EXISTS sms_alerts (
    alert_id BIGSERIAL PRIMARY KEY,
    location_id VARCHAR(50) REFERENCES locations(location_id),
    triggered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    rule_triggered VARCHAR(50) NOT NULL, -- e.g. 'PM2.5 > 50 ug/m3'
    pm25_val DECIMAL(6, 2),
    pm10_val DECIMAL(6, 2),
    cpcb_aqi INT,
    recipient_phone VARCHAR(20) NOT NULL,
    message_text TEXT NOT NULL,
    delivery_status VARCHAR(20) DEFAULT 'SENT', -- SENT, FAILED, SIMULATED
    gateway_response_id VARCHAR(100)
);

CREATE INDEX IF NOT EXISTS idx_alerts_triggered ON sms_alerts (triggered_at DESC);

-- 4. User Subscriptions for SMS Broadcasts
CREATE TABLE IF NOT EXISTS alert_subscriptions (
    subscription_id BIGSERIAL PRIMARY KEY,
    phone_number VARCHAR(20) UNIQUE NOT NULL,
    preferred_location_id VARCHAR(50) REFERENCES locations(location_id),
    is_active BOOLEAN DEFAULT TRUE,
    subscribed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. Machine Learning Forecast Records (For Post-Hoc Validation & Drift Tracking)
CREATE TABLE IF NOT EXISTS ml_forecast_evaluations (
    forecast_id BIGSERIAL PRIMARY KEY,
    location_id VARCHAR(50) REFERENCES locations(location_id),
    prediction_made_at TIMESTAMP WITH TIME ZONE NOT NULL,
    forecast_target_time TIMESTAMP WITH TIME ZONE NOT NULL,
    predicted_pm25 DECIMAL(6, 2),
    predicted_pm10 DECIMAL(6, 2),
    predicted_cpcb_aqi INT,
    actual_observed_pm25 DECIMAL(6, 2),   -- Populated retrospectively
    actual_observed_pm10 DECIMAL(6, 2),   -- Populated retrospectively
    actual_observed_aqi INT,              -- Populated retrospectively
    model_version VARCHAR(50) DEFAULT 'xgboost_delhi_v1.0',
    absolute_error_aqi INT GENERATED ALWAYS AS (ABS(predicted_cpcb_aqi - actual_observed_aqi)) STORED
);
