# EcoBytes

EcoBytes is a 72-hour air quality forecasting prototype for Delhi-NCR. It combines numerical weather forecasts, regional atmospheric chemistry guidance, atmospheric inversion physics, and satellite fire detections to forecast criteria pollutants (PM2.5, PM10, NO2, SO2, CO, O3) and calculate official Central Pollution Control Board (CPCB) National Air Quality Index (NAQI) values.

## What It Does

1. Weather & Chemistry Guidance: Pulls hourly GFS/ECMWF weather forecasts and Copernicus CAMS regional chemistry guidance from Open-Meteo.
2. Inversion & Boundary Layer Physics: Computes vertical lapse rates (between surface and 925 hPa) and planetary boundary layer ventilation to estimate nocturnal smog trapping.
3. Satellite Stubble Fires: Ingests NASA FIRMS VIIRS active fire hotspots across Punjab and Haryana to estimate upwind transport into Delhi.
4. CPCB NAQI Calculation: Implements official CPCB sub-index formulas and dominant pollutant selection (tested against statutory breakpoints).
5. ML Bias Correction: Includes a gradient-boosted residual model structure designed to learn local station bias on top of synoptic CAMS guidance.

## Current Project Status

The pipeline, weather integrations, physics calculations, CPCB index logic, dashboard, and alert simulator are fully implemented.

The machine learning residual model is currently untrained. The repository does not include pre-bundled station ground truth or unvalidated accuracy claims. Training the ML layer requires real ground station observations (via OpenAQ or CPCB CCR portal CSV exports).

## How to Run

### Requirements
- Python 3.10+
- Dependencies: `requests`, `scikit-learn`, `numpy` (optional, for ML training)

### Setup and Start
```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Initialize database with monitoring locations
python -c "import db; db.init_db()"

# 3. Start the backend server
python server.py
```
Open http://localhost:3000 in your browser to view the dashboard.

### Ingesting Real Ground Station Data & Training
```bash
# Ingest historical data from OpenAQ archive (no API key needed)
python data/ingest_openaq_archive.py --location-ids 8118 --year 2024 --months 9 10

# Or ingest official CPCB CCR portal CSV exports
python data/ingest_real.py --source cpcb_csv --csv path/to/cpcb_export.csv --station delhi-anand-vihar

# Train residual model on ingested station data
python train.py --source real_openaq   # or --source cpcb_station

# Evaluate multi-horizon performance
python evaluate_horizons.py --source station
```

### Running Unit Tests
```bash
python -m unittest discover tests
```

## Project Layout

- `server.py`: HTTP backend serving REST endpoints and dashboard assets.
- `coupled_forecast.py`: 72-hour multi-pollutant coupling engine.
- `atmospheric_inversion.py`: Pressure-level thermal lapse rate and ventilation index calculations.
- `regional_biomass_realtime.py`: NASA FIRMS satellite fire ingestion and transport scoring.
- `cpcb.py`: Official CPCB NAQI math and sub-index calculations.
- `ml_engine.py` & `train.py`: Feature engineering and residual ML training pipeline.
- `data/`: Ingestion tools for OpenAQ archive (`ingest_openaq_archive.py`) and CPCB CSVs (`ingest_real.py`).
- `public/`: Frontend dashboard (HTML, CSS, JavaScript, Leaflet map).
- `tests/`: Unit tests covering CPCB math, inversion physics, 72h forecast output, and data ingestion guards.

## Limitations

- Screening Model: EcoBytes uses reduced-order physics proxies. It is an early-warning screening tool, not a replacement for 3D numerical models (WRF-Chem/CMAQ).
- Unscheduled Local Events: Point-source emissions (such as localized waste burning or festival fireworks) require real-time assimilation and cannot be captured from synoptic weather alone.
