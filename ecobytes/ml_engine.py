"""EcoBytes ML engine - accuracy-upgraded, residual-targeted one-step PM2.5 forecaster.

The model is targeted on the residual downscaling bias:
    residual = observed_pm25 - cams_pm25
and includes cams_pm25 as a first-class synoptic feature.

The final calibrated forecast is reconstructed as:
    forecast_pm25 = clamp(cams_pm25 + predicted_residual, 0.0, 600.0)

Features use only information available at prediction time (historical PM2.5 + current forecast weather)
augmented with physically-inspired atmospheric inversion, biomass transport,
and two-way weather-chemistry feedback features.
"""

import json
import math
import os
import pickle
import sqlite3
import sys
import threading
from datetime import datetime

_MODEL_LOCK = threading.Lock()

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "ecobytes.db")
MODEL_FILE = os.path.join(BASE_DIR, "model_metadata.json")
MODEL_ARTIFACT = os.path.join(BASE_DIR, "ml_model.pkl")

FEATURE_NAMES = [
    # 0. Synoptic CAMS Numerical Chemistry Guidance
    "cams_pm25",
    # 1. Historical pollution lags & autoregression (1-10)
    "pm25_lag1", "pm25_lag3", "pm25_lag6", "pm25_lag12", "pm25_lag24",
    "pm25_roll6", "pm25_roll24", "pm25_momentum_1_3", "pm25_momentum_3_24",
    "pm10_lag1",
    # 2. Core surface meteorology (11-15)
    "temperature", "humidity", "wind_speed", "pressure", "rainfall",
    # 3. Calendar & diurnal solar harmonics (16-20)
    "hour_sin", "hour_cos", "dow_sin", "dow_cos", "weekend",
    # 4. Atmospheric Inversion & Boundary Layer Dynamics (21-25)
    "inversion_strength_k_per_km", "boundary_layer_height_m", "low_pbl_flag",
    "strong_inversion_flag", "stable_boundary_layer_score",
    # 5. Regional Biomass & Stubble-Burning Transport (26-27)
    "regional_biomass_impact_score", "upwind_fire_count",
    # 6. Two-Way Weather-Chemistry Coupling Layer (28-31)
    "weather_to_chemistry_index", "aerosol_feedback_index",
    "effective_boundary_layer_height_m", "coupled_stability_score",
]

SKLEARN_AVAILABLE = False
try:
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
    _probe = HistGradientBoostingRegressor(max_iter=1)
    SKLEARN_AVAILABLE = True
except Exception:
    SKLEARN_AVAILABLE = False


def _safe_float(value, default):
    try:
        if value is None:
            return float(default)
        value = float(value)
        return value if math.isfinite(value) else float(default)
    except (TypeError, ValueError):
        return float(default)


def compute_physical_diagnostics(pm25, pm10, temperature, humidity, wind_speed, rainfall, hour):
    """
    Reduced-order physical diagnostic engine for Delhi NCR.
    Computes atmospheric inversion, planetary boundary layer dynamics,
    regional biomass smoke transport potential, and weather-chemistry two-way feedbacks.
    """
    h = int(hour) % 24
    temp = _safe_float(temperature, 28.0)
    rh = _safe_float(humidity, 60.0)
    wind = _safe_float(wind_speed, 3.5)
    rain = max(0.0, _safe_float(rainfall, 0.0))
    p1 = max(5.0, _safe_float(pm25, 60.0))

    # 1. Atmospheric Inversion Proxy
    nocturnal = 1.0 if (h >= 20 or h <= 6) else 0.0
    stagnation = max(0.0, min(1.0, (4.5 - wind) / 4.0))
    cooling = max(0.0, min(1.0, (26.0 - temp) / 18.0))
    inv_strength = round(max(0.0, 3.8 * nocturnal * stagnation * (0.6 + 0.4 * cooling)), 2)
    strong_inv_flag = 1.0 if inv_strength >= 2.2 else 0.0

    # 2. Boundary Layer Dynamics
    solar_elevation = math.sin(math.pi * max(0.0, h - 6) / 12.0) if 6 <= h <= 18 else 0.0
    thermal_pbl = 350.0 + 1300.0 * solar_elevation
    mechanical_pbl = 120.0 * max(0.8, wind)
    pbl_height = max(110.0, min(2300.0, (thermal_pbl + mechanical_pbl) * (1.0 - 0.25 * (inv_strength / 4.0))))
    low_pbl_flag = 1.0 if pbl_height <= 400.0 else 0.0
    stable_boundary_score = round(max(0.0, min(1.0, (inv_strength / 4.0) * 0.65 + (max(0.0, 500.0 - pbl_height) / 500.0) * 0.35)), 3)

    # 3. Regional Biomass & Stubble-Burning Transport
    rain_scavenging = min(0.9, rain * 0.12)
    plume_trapping_capacity = (
        0.45 * stagnation +
        0.35 * max(0.0, (750.0 - pbl_height) / 600.0) +
        0.20 * min(1.0, inv_strength / 3.0)
    )
    biomass_impact_score = min(100.0, round(max(0.0, (1.0 - rain_scavenging)) * plume_trapping_capacity * 50.0, 1))
    upwind_fire_count_est = float(round(biomass_impact_score * 0.40))

    # 4. Two-Way Weather-Chemistry Coupling Layer
    hygroscopic_factor = min(1.5, max(0.2, (rh / 65.0) ** 1.5))
    aerosol_confinement = min(1.0, 0.60 * stable_boundary_score + 0.40 * (1.0 - min(1.0, wind / 6.0)))
    aerosol_feedback = round(min(1.0, 0.50 * aerosol_confinement + 0.50 * (hygroscopic_factor / 1.5)), 3)
    effective_pbl = max(90.0, pbl_height * (1.0 - (0.08 + 0.28 * aerosol_feedback)))

    reaction_rate = min(1.5, math.exp(0.04 * (temp - 20.0)) / 2.5)
    hygroscopic_growth = 0.1 + ((rh - 50.0) / 50.0) ** 2 if rh > 50.0 else 0.1 * (rh / 50.0)
    dilution_index = min(1.0, 0.55 * (wind / 7.0) + 0.45 * (effective_pbl / 1200.0))
    weather_to_chemistry = min(1.5, 0.30 * reaction_rate + 0.25 * hygroscopic_growth + 0.25 * (1.0 - dilution_index) + 0.20 * (1.0 - rain_scavenging))
    coupled_stability = min(1.0, 0.6 * stable_boundary_score + 0.4 * (1.0 - dilution_index))

    return {
        "inversion_strength_k_per_km": round(inv_strength, 2),
        "boundary_layer_height_m": round(pbl_height, 1),
        "low_pbl_flag": low_pbl_flag,
        "strong_inversion_flag": strong_inv_flag,
        "stable_boundary_layer_score": round(stable_boundary_score, 3),
        "regional_biomass_impact_score": round(biomass_impact_score, 1),
        "upwind_fire_count": upwind_fire_count_est,
        "weather_to_chemistry_index": round(weather_to_chemistry, 3),
        "aerosol_feedback_index": round(aerosol_feedback, 3),
        "effective_boundary_layer_height_m": round(effective_pbl, 1),
        "coupled_stability_score": round(coupled_stability, 3),
    }


def _feature_row(cams_pm25, pm_hist, pm10_hist, temperature, humidity, wind_speed, pressure, rainfall, hour, day_of_week=0):
    """Build inference features from CAMS guidance + historical PM history buffer + physical diagnostics."""
    hist = list(pm_hist)
    if not hist:
        hist = [60.0]
    while len(hist) < 24:
        hist.insert(0, hist[0])

    p1, p3, p6, p12, p24 = (hist[-1], hist[-3], hist[-6], hist[-12], hist[-24])
    roll6 = sum(hist[-6:]) / 6.0
    roll24 = sum(hist[-24:]) / 24.0

    if pm10_hist:
        pm10_1 = _safe_float(pm10_hist[-1], p1 * 2.15)
    else:
        pm10_1 = p1 * 2.15

    h = int(hour) % 24
    dow = int(day_of_week) % 7

    cams_val = _safe_float(cams_pm25, p1)
    temp = _safe_float(temperature, 28.0)
    hum = _safe_float(humidity, 60.0)
    wind = _safe_float(wind_speed, 3.5)
    press = _safe_float(pressure, 1007.0)
    rain = max(0.0, _safe_float(rainfall, 0.0))

    # Compute physical coupling & inversion features
    phys = compute_physical_diagnostics(p1, pm10_1, temp, hum, wind, rain, h)

    return [
        cams_val,
        p1, p3, p6, p12, p24,
        roll6, roll24,
        p1 - p3, p3 - p24,
        pm10_1,
        temp, hum, wind, press, rain,
        math.sin(2.0 * math.pi * h / 24.0),
        math.cos(2.0 * math.pi * h / 24.0),
        math.sin(2.0 * math.pi * dow / 7.0),
        math.cos(2.0 * math.pi * dow / 7.0),
        1.0 if dow >= 5 else 0.0,
        # Physical Inversion & Boundary Layer
        phys["inversion_strength_k_per_km"],
        phys["boundary_layer_height_m"],
        phys["low_pbl_flag"],
        phys["strong_inversion_flag"],
        phys["stable_boundary_layer_score"],
        # Regional Biomass / Stubble Plumes
        phys["regional_biomass_impact_score"],
        phys["upwind_fire_count"],
        # Two-Way Weather-Chemistry Coupling Layer
        phys["weather_to_chemistry_index"],
        phys["aerosol_feedback_index"],
        phys["effective_boundary_layer_height_m"],
        phys["coupled_stability_score"],
    ]


def _solve_linear_system(A, b):
    n = len(b)
    M = [A[i][:] + [b[i]] for i in range(n)]
    for i in range(n):
        pivot = i
        for r in range(i + 1, n):
            if abs(M[r][i]) > abs(M[pivot][i]):
                pivot = r
        M[i], M[pivot] = M[pivot], M[i]
        if abs(M[i][i]) < 1e-12:
            M[i][i] = 1e-6
        factor = M[i][i]
        for c in range(i, n + 1):
            M[i][c] /= factor
        for r in range(n):
            if r != i:
                f = M[r][i]
                for c in range(i, n + 1):
                    M[r][c] -= f * M[i][c]
    return [M[i][n] for i in range(n)]


class _PurePythonHybridBoosting:
    """Zero-dependency pure-Python regularized linear + decision stump residual booster."""
    def __init__(self, alpha=2.0, n_stumps=30, learning_rate=0.08):
        self.alpha = float(alpha)
        self.n_stumps = int(n_stumps)
        self.learning_rate = float(learning_rate)
        self.weights = None
        self.means = None
        self.stds = None
        self.stumps = []

    def fit(self, X, y):
        n = len(X)
        d = len(X[0])
        self.means = [sum(X[i][j] for i in range(n)) / n for j in range(d)]
        self.stds = []
        for j in range(d):
            var = sum((X[i][j] - self.means[j]) ** 2 for i in range(n)) / n
            self.stds.append(math.sqrt(var) if var > 1e-8 else 1.0)

        XtX = [[0.0] * (d + 1) for _ in range(d + 1)]
        Xty = [0.0] * (d + 1)

        for i in range(n):
            row_norm = [1.0] + [(X[i][j] - self.means[j]) / self.stds[j] for j in range(d)]
            for r in range(d + 1):
                Xty[r] += row_norm[r] * y[i]
                for c in range(d + 1):
                    XtX[r][c] += row_norm[r] * row_norm[c]

        for j in range(1, d + 1):
            XtX[j][j] += self.alpha

        self.weights = _solve_linear_system(XtX, Xty)

        preds_stage1 = []
        for i in range(n):
            p = self.weights[0] + sum(self.weights[j + 1] * (X[i][j] - self.means[j]) / self.stds[j] for j in range(d))
            preds_stage1.append(p)

        residuals = [y[i] - preds_stage1[i] for i in range(n)]

        self.stumps = []
        for _ in range(self.n_stumps):
            best = None
            total_res = sum(residuals)
            for f in range(d):
                vals = sorted(set(row[f] for row in X))
                if len(vals) < 3:
                    continue
                qs = [vals[int(len(vals) * q)] for q in (0.1, 0.25, 0.5, 0.75, 0.9)]
                for th in qs:
                    li = [i for i, row in enumerate(X) if row[f] <= th]
                    if len(li) < 15 or len(li) > n - 15:
                        continue
                    rs = sum(residuals[i] for i in li)
                    rc = n - len(li)
                    rr = total_res - rs
                    gain = rs * rs / len(li) + rr * rr / rc
                    if best is None or gain > best[0]:
                        best = (gain, f, th, rs / len(li), rr / rc)
            if best is None:
                break
            _, f, th, lv, rv = best
            stump = (f, th, lv * self.learning_rate, rv * self.learning_rate)
            self.stumps.append(stump)
            for i, row in enumerate(X):
                residuals[i] -= stump[2] if row[f] <= th else stump[3]
        return self

    def predict(self, X):
        d = len(self.means)
        out = []
        for row in X:
            p = self.weights[0] + sum(self.weights[j + 1] * (row[j] - self.means[j]) / self.stds[j] for j in range(d))
            for f, th, lv, rv in self.stumps:
                p += lv if row[f] <= th else rv
            out.append(p)
        return out


_CACHED_MODEL_BUNDLE = None


def get_model_status():
    if os.path.exists(MODEL_FILE):
        try:
            with open(MODEL_FILE, "r", encoding="utf-8") as f:
                meta = json.load(f)
            return meta
        except Exception:
            pass
    return {"status": "UNINITIALIZED"}


def train_model(location_id=None, source="cpcb_station"):
    """
    Triggers model training pipeline via train.py.
    """
    import train
    try:
        res = train.run_training_pipeline(dataset_source=source)
        if res:
            global _CACHED_MODEL_BUNDLE
            with _MODEL_LOCK:
                _CACHED_MODEL_BUNDLE = None  # Invalidate cache to pick up newly trained model
            return {"success": True, "metrics": res.get("metrics", {}), "metadata": res}
        return {"success": False, "message": f"Training aborted: no valid observations for source='{source}'."}
    except SystemExit:
        return {
            "success": False,
            "message": f"Training requires real station data (source='{source}'). Ingest via data/ingest_real.py first."
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def _load_model():
    global _CACHED_MODEL_BUNDLE
    with _MODEL_LOCK:
        if _CACHED_MODEL_BUNDLE is not None:
            return _CACHED_MODEL_BUNDLE

        meta = get_model_status()
        if os.path.exists(MODEL_ARTIFACT):
            try:
                with open(MODEL_ARTIFACT, "rb") as f:
                    model = pickle.load(f)
                    _CACHED_MODEL_BUNDLE = (meta, model)
                    return meta, model
            except Exception:
                pass
        return meta, None


def predict_future_pm25(
    cams_pm25=None,
    lag1_pm25=60.0,
    lag3_pm25=None,
    lag1_pm10=None,
    temp=25.0,
    humidity=60.0,
    wind_speed=3.5,
    pressure=1010.0,
    hour=12,
    rain_val=0.0,
    lag6_pm25=None,
    lag12_pm25=None,
    lag24_pm25=None,
    pm25_roll6=None,
    pm25_roll24=None,
    day_of_week=0,
    pm25_history=None,
    pm10_history=None,
):
    """
    Predict one hour ahead using CAMS guidance + residual correction:
        forecast_pm25 = clamp(cams_pm25 + predicted_residual, 0.0, 600.0)
    """
    p1 = _safe_float(lag1_pm25, 60.0)
    cams_val = _safe_float(cams_pm25, p1)

    if pm25_history:
        hist = [_safe_float(v, 60.0) for v in pm25_history]
        while len(hist) < 24:
            hist.insert(0, hist[0] if hist else 60.0)
    else:
        l1 = p1
        l3 = _safe_float(lag3_pm25, l1)
        l6 = _safe_float(lag6_pm25, l3)
        l12 = _safe_float(lag12_pm25, l6)
        l24 = _safe_float(lag24_pm25, l12)
        hist = [l24] * 24
        hist[-1] = l1
        hist[-2] = (l1 + l3) / 2.0
        hist[-3] = l3
        hist[-4] = (l3 + l6) / 2.0
        hist[-5] = (l3 + l6) / 2.0
        hist[-6] = l6
        for k in range(7, 12):
            hist[-k] = l6 + (l12 - l6) * ((k - 6) / 6.0)
        hist[-12] = l12
        for k in range(13, 24):
            hist[-k] = l12 + (l24 - l12) * ((k - 12) / 12.0)
        hist[-24] = l24

    if pm10_history:
        pm10_hist = [_safe_float(v, _safe_float(lag1_pm10, p1 * 2.15)) for v in pm10_history]
        while len(pm10_hist) < 24:
            pm10_hist.insert(0, pm10_hist[0] if pm10_hist else p1 * 2.15)
    else:
        p10 = _safe_float(lag1_pm10, p1 * 2.15)
        pm10_hist = [p10] * 24

    row = _feature_row(
        cams_val,
        hist[-24:],
        pm10_hist[-24:],
        temp,
        humidity,
        wind_speed,
        pressure,
        rain_val,
        hour,
        day_of_week,
    )
    if pm25_roll6 is not None:
        row[6] = _safe_float(pm25_roll6, row[6])
    if pm25_roll24 is not None:
        row[7] = _safe_float(pm25_roll24, row[7])

    meta, model = _load_model()
    if model is None:
        return round(max(0.0, cams_val), 1)

    try:
        pred_residual = float(model.predict([row])[0])
        final_pm = max(0.0, min(600.0, cams_val + pred_residual))
    except Exception:
        final_pm = cams_val
    return round(final_pm, 1)
