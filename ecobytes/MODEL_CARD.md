# Model Card: EcoBytes Atmospheric PM2.5 and Multi-Pollutant Forecasting Engine

## 1. Model Details

- **Model Name**: EcoBytes Coupled Atmospheric Predictor
- **Version**: 3.0.0
- **Model Architecture**: Hybrid Physics-ML Regressor (`forecast = clamp(cams_guidance + predicted_residual, 0.0, 600.0)`)
- **Library Backend**: `EcoBytes._PurePythonHybridBoosting` (pure-Python fallback; upgrades to `sklearn.ensemble.HistGradientBoostingRegressor` when installed)
- **Primary Target**: Hourly PM2.5 concentration (ug/m3) across 6 urban Delhi-NCR monitoring stations (t+1h to t+72h).
- **Co-Pollutants Predicted**: PM10, NO2, SO2, CO, O3 (derived via coupled regional guidance and chemical balance).
- **Regulatory Framework**: Central Pollution Control Board (CPCB) National Air Quality Index (NAQI) technical standard.
- **License**: MIT

## 2. Intended Use and Scope

### Primary Intended Uses:
- City-scale early screening of multi-day air pollution episodes and inversion trapping events.
- Decision support for Graded Response Action Plan (GRAP) stage advisory forecasting 24 to 72 hours in advance.
- Public health warnings prior to rapid nocturnal particulate accumulation.

### Scientific Scope and Limitations:
- Reduced-Order Screening Model: EcoBytes is a lightweight reduced-order screening proxy coupling thermodynamic lapse rate heuristics with gradient boosting. It does not replace full 3D numerical Eulerian models (WRF-Chem, CMAQ) for statutory emissions regulation.
- Ground Truth Requirement: The residual ML model requires real station data to train. When only CAMS reanalysis is available, observed PM2.5 equals CAMS guidance, making the residual identically zero.
- Spatial Autocorrelation: Delhi-NCR stations are geographically proximate (~10 to 40 km apart) and share CAMS grid cells. Leave-One-Station-Out CV indicates intra-urban spatial transferability, not generalization to distant regions.

## 3. Data Sources

- Copernicus Atmosphere Monitoring Service (CAMS): Regional synoptic background particulate and trace gas guidance (`cams_reanalysis`).
- CPCB CAAQMS / OpenAQ v3: Physical ground station measurements for local calibration (`cpcb_station` / `real_openaq`).
- Open-Meteo GFS / ECMWF: Surface and multi-level (925 hPa, 850 hPa) thermodynamic profiles (`nwp_forecast`).
- NASA Earthdata (FIRMS VIIRS NOAA-21): Active stubble fire detection across Punjab and Haryana (`nasa_firms`).
- Synthetic Demo Seeder: Offline sandbox demonstration data (`synthetic_demo`).

## 4. Evaluation and Current Status

### Current Status: NOT EVALUATED ON STATION DATA
The residual ML model requires in-situ station observations (CPCB CAAQMS or OpenAQ) to train. Without station data, the model remains untrained, and forecasts rely on CAMS guidance with boundary-layer physics adjustments.

### Unit-Tested Components
- CPCB NAQI breakpoint calculations match official Table 1 breakpoints (`tests/test_cpcb.py`).
- Atmospheric physics modules produce bounded outputs (`tests/test_inversion.py`).
- 72-hour forecast pipeline runs end-to-end with non-negative PM2.5 (`tests/test_forecast_engine.py`).
- Data provenance guards prevent model outputs from being stored as station data (`tests/test_ingest.py`).

### How to Generate Real Metrics
```bash
python data/ingest_real.py --source cpcb_csv --csv <path>
python train.py --source cpcb_station
python evaluate_horizons.py --source cpcb_station
```

## 5. Known Failure Modes and Edge Cases

1. Sudden Dust Storms: Rapid convective dust advection from the Thar Desert can cause sudden PM10 spikes that precede numerical weather model updates by 1 to 3 hours.
2. Episodic Fireworks and Unregulated Combustion: Point-source spikes are not captured in synoptic NWP models and require real-time sensor assimilation.
3. Sensor Dropouts: If ground feeds drop out, the system falls back to regional CAMS numerical guidance and physical lapse rate multipliers.
4. No Archived CAMS Forecast Baseline: A fair model vs NWP skill comparison requires archived real-time forecast fields from Open-Meteo historical forecast runs rather than reanalysis.

## 6. Development Notes and AI Disclosure

This project was built with the assistance of AI pair-programming tools (Google Antigravity / Claude).

### Roles of AI in Development:
- Implementing physical formula proxies (lapse rate inversion models, boundary layer ventilation index).
- Writing and maintaining the automated test suite.
- Structuring the data ingestion, training, and evaluation pipelines.
- Auditing the codebase for exception handling and data leakage.

### Key Concepts for Project Defense:
- How `seed_data.py` generates synthetic PM2.5 (AR(1) + diurnal cycle + stubble transport + noise).
- Why the residual model cannot be trained on CAMS reanalysis alone (observed equals CAMS, so residual is zero).
- What `atmospheric_inversion.py` computes from 925 hPa and 850 hPa pressure levels.
- The difference between CAMS reanalysis and CAMS forecast guidance.
