"""EcoBytes problem-aligned 72-hour hybrid forecast engine.

This module implements a practical SIH architecture without pretending to be a
full WRF-Chem numerical solver:

1. CAMS/Open-Meteo hourly chemistry: PM2.5, PM10, O3, NO2, SO2, CO.
2. Open-Meteo meteorology: temperature, RH, wind, precipitation, PBL,
   pressure-level temperature/geopotential height for inversion diagnosis.
3. Local PM2.5 ML correction: the bundled model is used only as a local
   correction layer; CAMS remains the independent regional chemistry baseline.
4. Reduced-order aerosol feedback: predicted aerosol loading can reduce the
   effective mixing height, bounded so it cannot explode the forecast.
5. Regional fire/plume transport: recent NASA FIRMS hotspot cache is projected
   forward using wind direction/speed, travel time, PBL and Gaussian-like
   crosswind decay. It is an impact-risk indicator, not a mass-conserving
   chemical-transport simulation.
6. Multi-pollutant CPCB-style screening AQI and dominant pollutant.

The output explicitly reports data provenance and stale/fallback conditions.
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import cpcb
import ml_engine
import db

CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
CACHE_TTL_SECONDS = 300

HEADERS = {
    "User-Agent": "EcoBytes-SIH-ProblemAligned/3.0",
    "Accept": "application/json",
}

CHEMISTRY_HOURLY = (
    "pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,sulphur_dioxide,ozone,"
    "aerosol_optical_depth"
)

WEATHER_HOURLY = (
    "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,"
    "precipitation,surface_pressure,boundary_layer_height,shortwave_radiation,"
    "cloud_cover,temperature_925hPa,temperature_850hPa,"
    "geopotential_height_925hPa,geopotential_height_850hPa"
)

FIRE_CACHE = Path(__file__).resolve().parent / "data" / "regional_biomass_realtime.json"


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        x = float(value)
        if not math.isfinite(x):
            return default
        return x
    except (TypeError, ValueError):
        return default


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_json(url: str, timeout: int = 10) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_suite(lat: float, lon: float) -> dict[str, Any]:
    """Fetch chemistry + weather for one coordinate and cache the bundle."""
    key = f"{lat:.4f},{lon:.4f}"
    now = time.time()
    cached = CACHE.get(key)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    q = {
        "latitude": lat,
        "longitude": lon,
        "timezone": "Asia/Kolkata",
        "forecast_hours": 76,
        "current": "pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,sulphur_dioxide,ozone",
        "hourly": CHEMISTRY_HOURLY,
    }
    aq_url = "https://air-quality-api.open-meteo.com/v1/air-quality?" + urllib.parse.urlencode(q)

    wq = {
        "latitude": lat,
        "longitude": lon,
        "timezone": "Asia/Kolkata",
        "forecast_hours": 76,
        "hourly": WEATHER_HOURLY,
    }
    weather_url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(wq)

    try:
        aq = _fetch_json(aq_url, 10)
        weather = _fetch_json(weather_url, 10)
        payload = {
            "air_quality": aq,
            "weather": weather,
            "chemistry_source": "CAMS via Open-Meteo Air Quality API",
            "weather_source": "Open-Meteo forecast model",
            "fetched_at_utc": _utc_now().isoformat(),
            "fallback": False,
        }
        CACHE[key] = (now, payload)
        return payload
    except Exception as exc:
        payload = fallback_suite(lat, lon)
        payload["error"] = str(exc)
        CACHE[key] = (now, payload)
        return payload


def fallback_suite(lat: float, lon: float) -> dict[str, Any]:
    """Offline-safe deterministic chemistry/weather forecast for demo mode."""
    start = datetime.now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    times = [(start + timedelta(hours=i)).isoformat(timespec="minutes") for i in range(76)]
    n = len(times)
    pm25 = []
    pm10 = []
    o3 = []
    no2 = []
    so2 = []
    co = []
    temp = []
    rh = []
    wind = []
    winddir = []
    rain = []
    pressure = []
    pbl = []
    sw = []
    cloud = []
    t925 = []
    t850 = []
    z925 = []
    z850 = []
    for i in range(n):
        h = (start.hour + i) % 24
        diurnal = math.sin(2 * math.pi * (h - 9) / 24)
        night = 1.0 if h <= 6 or h >= 20 else 0.0
        pm = 58 + 18 * (1 - diurnal) + 10 * night
        pm25.append(round(pm, 2))
        pm10.append(round(pm * 1.95, 2))
        o3.append(round(max(10, 42 + 25 * diurnal - 5 * night), 2))
        no2.append(round(max(10, 44 + 12 * night - 4 * diurnal), 2))
        so2.append(14.0)
        co.append(900.0 + 250.0 * night)
        temp.append(round(29 + 8 * diurnal, 2))
        rh.append(round(74 - 22 * diurnal, 2))
        wind.append(round(2.2 + 2.8 * max(0, diurnal), 2))
        winddir.append(300.0)
        rain.append(0.0)
        pressure.append(1007.0)
        pbl.append(round(260 + 900 * max(0, diurnal) + 110 * (1 - night), 1))
        sw.append(round(0 if night else max(0, 550 * math.sin(math.pi * (h - 6) / 12)), 1))
        cloud.append(25.0)
        t925.append(round(27 + 1.5 * night, 2))
        t850.append(round(20 + 0.7 * night, 2))
        z925.append(950.0)
        z850.append(1650.0)
    return {
        "air_quality": {"current": {
            "pm2_5": pm25[0], "pm10": pm10[0], "ozone": o3[0],
            "nitrogen_dioxide": no2[0], "sulphur_dioxide": so2[0],
            "carbon_monoxide": co[0]
        }, "hourly": {
            "time": times, "pm2_5": pm25, "pm10": pm10, "ozone": o3,
            "nitrogen_dioxide": no2, "sulphur_dioxide": so2,
            "carbon_monoxide": co, "aerosol_optical_depth": [0.7] * n
        }},
        "weather": {"hourly": {
            "time": times, "temperature_2m": temp,
            "relative_humidity_2m": rh, "wind_speed_10m": wind,
            "wind_direction_10m": winddir, "precipitation": rain,
            "surface_pressure": pressure, "boundary_layer_height": pbl,
            "shortwave_radiation": sw, "cloud_cover": cloud,
            "temperature_925hPa": t925, "temperature_850hPa": t850,
            "geopotential_height_925hPa": z925,
            "geopotential_height_850hPa": z850,
        }, "current": {
            "temperature_2m": temp[0], "relative_humidity_2m": rh[0],
            "wind_speed_10m": wind[0], "wind_direction_10m": winddir[0],
            "precipitation": rain[0], "surface_pressure": pressure[0],
            "cloud_cover": cloud[0]
        }},
        "chemistry_source": "Demo fallback (no live CAMS connection)",
        "weather_source": "Demo fallback (no live weather connection)",
        "fetched_at_utc": _utc_now().isoformat(),
        "fallback": True,
    }


def _recent_observed_history(location_id: str) -> list[dict[str, Any]]:
    try:
        return db.get_recent_observations(location_id, 72)
    except Exception:
        return []


def _local_ml_pm25(location_id: str, pm25_hist: list[float], pm10_hist: list[float],
                   temp: float, rh: float, wind: float, pressure: float, rain: float,
                   hour: int, dow: int) -> float | None:
    """Use the existing model as a local correction signal."""
    if not pm25_hist:
        return None
    try:
        return float(ml_engine.predict_future_pm25(
            lag1_pm25=pm25_hist[-1],
            lag3_pm25=pm25_hist[-3] if len(pm25_hist) >= 3 else pm25_hist[-1],
            lag1_pm10=pm10_hist[-1] if pm10_hist else pm25_hist[-1] * 2.15,
            temp=temp, humidity=rh, wind_speed=wind, pressure=pressure,
            hour=hour, rain_val=rain, day_of_week=dow,
            pm25_history=pm25_hist[-24:], pm10_history=(pm10_hist[-24:] if pm10_hist else None),
        ))
    except Exception:
        return None


def _pollution_bias(location_id: str, aq_current: dict[str, Any]) -> dict[str, float]:
    """Estimate conservative local bias from recent stored observations."""
    recent = _recent_observed_history(location_id)
    recent = [r for r in recent if str(r.get("source", "")).lower() in {"cpcb_openaq", "dpcc_station", "cpcb_station", "openaq"}]
    if len(recent) < 6:
        return {}
    out: dict[str, float] = {}
    fields = {"pm25": "pm2_5", "pm10": "pm10", "o3": "ozone", "no2": "nitrogen_dioxide", "so2": "sulphur_dioxide"}
    for local_key, api_key in fields.items():
        obs = [_safe_float(r.get(local_key)) for r in recent]
        obs = [x for x in obs if x is not None and x >= 0]
        base = _safe_float(aq_current.get(api_key))
        if len(obs) >= 6 and base is not None and base > 1:
            median = sorted(obs)[len(obs) // 2]
            ratio = _clamp(median / base, 0.65, 1.50)
            out[local_key] = ratio
    return out


def _angdiff(a: float, b: float) -> float:
    d = abs((a - b + 180.0) % 360.0 - 180.0)
    return d


def _load_fire_cache() -> dict[str, Any] | None:
    try:
        if not FIRE_CACHE.exists():
            return None
        data = json.loads(FIRE_CACHE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1); p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1); dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1 = math.radians(lat1); p2 = math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def fire_plume_forecast(receptor_lat: float, receptor_lon: float, forecast_times: list[str],
                        wind_dirs: list[float], wind_speeds: list[float], pbls: list[float]) -> dict[str, Any]:
    """Project recent fire detections toward a receptor for each forecast hour."""
    cache = _load_fire_cache()
    if not cache or not cache.get("fires"):
        return {
            "available": False,
            "source": "NASA FIRMS cache unavailable",
            "stale": True,
            "hours": [
                {"fire_impact_score": 0.0, "plume_probability_pct": 0.0, "upwind_fire_count": 0,
                 "upwind_frp_mw": 0.0, "transport_status": "No fire source data"}
                for _ in forecast_times
            ],
        }

    collected = cache.get("collected_at_utc")
    try:
        age_h = max(0.0, (_utc_now() - datetime.fromisoformat(collected.replace("Z", "+00:00"))).total_seconds() / 3600)
    except Exception:
        age_h = 999.0
    stale = age_h > 72.0
    fires = cache.get("fires") or []
    out = []
    for i, _ts in enumerate(forecast_times):
        wd = _safe_float(wind_dirs[i], 270.0) or 270.0
        ws = max(0.3, _safe_float(wind_speeds[i], 2.0) or 2.0) * 1000 / 3600.0
        layer = max(150.0, _safe_float(pbls[i], 500.0) or 500.0)
        wind_to = (wd + 180.0) % 360.0
        total = 0.0; frp_total = 0.0; count = 0
        for fire in fires:
            flat = _safe_float(fire.get("source_latitude")); flon = _safe_float(fire.get("source_longitude"))
            frp = max(0.0, _safe_float(fire.get("frp_mw"), 0.0) or 0.0)
            if flat is None or flon is None or frp <= 0:
                continue
            dist = _haversine_km(flat, flon, receptor_lat, receptor_lon)
            bearing = _bearing_deg(flat, flon, receptor_lat, receptor_lon)
            alignment = max(0.0, math.cos(math.radians(_angdiff(bearing, wind_to))))
            # Smoke arrival is strongest when the source-receptor vector is downwind.
            travel_h = dist / max(ws * 3.6, 0.5)
            if travel_h > 60:
                continue
            age_factor = math.exp(-max(0.0, age_h + travel_h) / 30.0)
            distance_decay = math.exp(-dist / 260.0)
            pbl_factor = _clamp(900.0 / layer, 0.45, 2.2)
            wind_factor = _clamp(1.8 / max((ws * 3.6), 0.8), 0.35, 1.7)
            contribution = (frp / 80.0) * alignment * distance_decay * pbl_factor * wind_factor * age_factor
            if contribution > 0.005:
                count += 1
                frp_total += frp * alignment
            total += contribution
        score = _clamp(total * 22.0, 0.0, 100.0)
        if stale:
            score *= max(0.0, math.exp(-(age_h - 72.0) / 12.0))
        prob = _clamp(score * 1.35, 0.0, 100.0)
        status = "High transport potential" if score >= 45 else ("Moderate transport potential" if score >= 20 else ("Low transport potential" if count else "No aligned plume"))
        out.append({
            "fire_impact_score": round(score, 1),
            "plume_probability_pct": round(prob, 1),
            "upwind_fire_count": count,
            "upwind_frp_mw": round(frp_total, 1),
            "transport_status": status,
        })
    return {
        "available": True,
        "source": "NASA FIRMS active-fire cache",
        "stale": stale,
        "cache_age_hours": round(age_h, 1),
        "attribution_note": cache.get("attribution_note") or "Active-fire detections are not automatically classified as stubble burning.",
        "hours": out,
    }


def inversion_features(t2m: float, t925: float, z925: float, t850: float, z850: float, pbl: float, rh: float, wind: float) -> dict[str, Any]:
    # Surface-to-925hPa layer. Pressure-surface heights are provided by the weather model.
    dz_km = max((z925 - 0.0) / 1000.0, 0.2)
    delta = t925 - t2m
    strength = delta / dz_km
    # Independent stability cross-check using 850hPa profile.
    dz2 = max((z850 - z925) / 1000.0, 0.2)
    lapse925_850 = (t850 - t925) / dz2
    inv_score = _clamp(max(0.0, strength) / 4.0, 0.0, 1.0)
    pbl_score = _clamp((800.0 - pbl) / 600.0, 0.0, 1.0)
    calm_score = _clamp((3.5 - wind) / 3.5, 0.0, 1.0)
    stable = _clamp(0.55 * inv_score + 0.30 * pbl_score + 0.15 * calm_score, 0.0, 1.0)
    return {
        "surface_temp_c": round(t2m, 2),
        "temperature_925hPa_c": round(t925, 2),
        "temperature_850hPa_c": round(t850, 2),
        "inversion_temperature_difference_c": round(delta, 2),
        "inversion_strength_k_per_km": round(strength, 2),
        "925_850_lapse_rate_k_per_km": round(lapse925_850, 2),
        "boundary_layer_height_m": round(pbl, 1),
        "low_pbl_flag": int(pbl < 500),
        "strong_inversion_flag": int(strength >= 2.0),
        "stable_boundary_layer_score": round(stable, 3),
        "humidity_pct": round(rh, 1),
        "wind_speed_ms": round(wind, 2),
    }


def _effective_pbl(pbl: float, pm25: float, inv_score: float, fire_score: float) -> tuple[float, float]:
    aerosol_loading = _clamp(pm25 / 180.0, 0.0, 1.0)
    reduction = _clamp(0.04 + 0.18 * aerosol_loading + 0.10 * inv_score + 0.06 * (fire_score / 100.0), 0.0, 0.32)
    return max(100.0, pbl * (1 - reduction)), reduction


def build_forecast(location: dict[str, Any], hours: int = 72, raw_live: dict[str, Any] | None = None, ml_status: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_72h_forecast(location, raw_live=raw_live, ml_status=ml_status, hours=hours)


def build_72h_forecast(location: dict[str, Any], raw_live: dict[str, Any] | None = None, ml_status: dict[str, Any] | None = None, hours: int = 72) -> dict[str, Any]:
    hours = max(1, min(72, int(hours)))
    if raw_live is not None and raw_live.get("air_quality") and raw_live.get("weather"):
        raw = raw_live
    else:
        raw = fetch_suite(location["latitude"], location["longitude"])

    aq = raw.get("air_quality", {})
    weather = raw.get("weather", {})
    aqt = aq.get("hourly", {}) or {}
    wt = weather.get("hourly", {}) or {}
    times_all = aqt.get("time") or wt.get("time") or []
    if len(times_all) >= hours + 1:
        start = 1
    else:
        start = 0
    times = list(times_all[start:start + hours])

    def arr(src: dict[str, Any], key: str, default: float) -> list[float]:
        vals = src.get(key) or []
        return [(_safe_float(v, default) if v is not None else default) for v in vals[start:start + hours]]

    temps = arr(wt, "temperature_2m", 28.0)
    rhs = arr(wt, "relative_humidity_2m", 65.0)
    winds = arr(wt, "wind_speed_10m", 3.0)
    winddirs = arr(wt, "wind_direction_10m", 270.0)
    rains = arr(wt, "precipitation", 0.0)
    pressures = arr(wt, "surface_pressure", 1007.0)
    pbls = arr(wt, "boundary_layer_height", 600.0)
    sws = arr(wt, "shortwave_radiation", 0.0)
    clouds = arr(wt, "cloud_cover", 25.0)
    t925s = arr(wt, "temperature_925hPa", 25.0)
    t850s = arr(wt, "temperature_850hPa", 19.0)
    z925s = arr(wt, "geopotential_height_925hPa", 950.0)
    z850s = arr(wt, "geopotential_height_850hPa", 1650.0)

    pm25_cams = arr(aqt, "pm2_5", 60.0)
    pm10_cams = arr(aqt, "pm10", 120.0)
    o3_cams = arr(aqt, "ozone", 45.0)
    no2_cams = arr(aqt, "nitrogen_dioxide", 40.0)
    so2_cams = arr(aqt, "sulphur_dioxide", 15.0)
    co_cams = arr(aqt, "carbon_monoxide", 1000.0)
    aod = arr(aqt, "aerosol_optical_depth", 0.6)

    def pad(values: list[float], fallback: float) -> list[float]:
        if not values:
            return [fallback] * hours
        if len(values) < hours:
            values = values + [values[-1]] * (hours - len(values))
        return values[:hours]

    temps = pad(temps, 28.0)
    rhs = pad(rhs, 65.0)
    winds = pad(winds, 3.0)
    winddirs = pad(winddirs, 270.0)
    rains = pad(rains, 0.0)
    pressures = pad(pressures, 1007.0)
    pbls = pad(pbls, 600.0)
    sws = pad(sws, 0.0)
    clouds = pad(clouds, 25.0)
    t925s = pad(t925s, 25.0)
    t850s = pad(t850s, 19.0)
    z925s = pad(z925s, 950.0)
    z850s = pad(z850s, 1650.0)
    pm25_cams = pad(pm25_cams, 60.0)
    pm10_cams = pad(pm10_cams, 120.0)
    o3_cams = pad(o3_cams, 45.0)
    no2_cams = pad(no2_cams, 40.0)
    so2_cams = pad(so2_cams, 15.0)
    co_cams = pad(co_cams, 1000.0)
    aod = pad(aod, 0.6)

    aq_current = aq.get("current", {}) or {}
    bias = _pollution_bias(location["id"], aq_current)

    recent = _recent_observed_history(location["id"])
    reliable_sources = {"cpcb_openaq", "dpcc_station", "cpcb_station", "openaq"}
    real_recent = [r for r in recent if str(r.get("source", "")).lower() in reliable_sources]
    history_for_ml = real_recent if len(real_recent) >= 12 else recent
    pm25_hist = [_safe_float(r.get("pm25")) for r in history_for_ml]
    pm25_hist = [x for x in pm25_hist if x is not None and x >= 0][-24:]
    pm10_hist = [_safe_float(r.get("pm10")) for r in history_for_ml]
    pm10_hist = [x for x in pm10_hist if x is not None and x >= 0][-24:]
    initial_hist_len = len(pm25_hist)
    ml_history_source = "real_station_history" if len(real_recent) >= 12 else "bundled_demo_history"
    if not pm25_hist:
        current_pm = _safe_float(aq_current.get("pm2_5"), pm25_cams[0] if pm25_cams else 60.0) or 60.0
        pm25_hist = [current_pm] * 24
    while len(pm25_hist) < 24:
        pm25_hist.insert(0, pm25_hist[0])
    while len(pm10_hist) < 24:
        pm10_hist.insert(0, pm10_hist[0] if pm10_hist else (pm10_cams[0] if pm10_cams else pm25_hist[0] * 2.0))

    fire = fire_plume_forecast(location["latitude"], location["longitude"], times,
                               winddirs, winds, pbls)

    series = []
    for i in range(hours):
        try:
            dt = datetime.fromisoformat(times[i])
            hour = dt.hour; dow = dt.weekday()
        except Exception:
            dt = datetime.now(); hour = dt.hour; dow = dt.weekday()

        inv = inversion_features(temps[i], t925s[i], z925s[i], t850s[i], z850s[i], pbls[i], rhs[i], winds[i])
        inv_score = inv["stable_boundary_layer_score"]
        fire_hour = fire["hours"][i] if i < len(fire.get("hours", [])) else {"fire_impact_score": 0.0, "plume_probability_pct": 0.0, "upwind_fire_count": 0, "upwind_frp_mw": 0.0, "transport_status": "No fire source data"}
        fire_score = float(fire_hour["fire_impact_score"])

        ml_pred = _local_ml_pm25(location["id"], pm25_hist, pm10_hist,
                                 temps[i], rhs[i], winds[i], pressures[i], rains[i], hour, dow)
        cams_pm = max(0.0, pm25_cams[i])
        if ml_pred is None:
            hybrid_pm = cams_pm
        else:
            local_blend = 0.38 if initial_hist_len >= 12 else 0.18
            hybrid_pm = (1 - local_blend) * cams_pm + local_blend * max(0.0, ml_pred)

        aerosol_feedback = _clamp((hybrid_pm / 180.0) * 0.55 + aod[i] * 0.15, 0.0, 1.0)
        effective_pbl, pbl_reduction = _effective_pbl(pbls[i], hybrid_pm, inv_score, fire_score)
        ventilation = max(0.0, winds[i] * effective_pbl)
        feedback_mult = _clamp(
            1.0 + 0.06 * inv_score + 0.05 * (fire_score / 100.0) - 0.03 * _clamp(ventilation / 6000.0, 0.0, 1.0),
            0.90, 1.16
        )
        rain_factor = _clamp(1.0 - 0.015 * max(0.0, rains[i]), 0.82, 1.0)
        corrected_pm25 = _clamp(hybrid_pm * feedback_mult * rain_factor, 0.0, 600.0)

        pm10 = max(0.0, pm10_cams[i]) * bias.get("pm10", 1.0)
        o3 = max(0.0, o3_cams[i]) * bias.get("o3", 1.0)
        no2 = max(0.0, no2_cams[i]) * bias.get("no2", 1.0)
        so2 = max(0.0, so2_cams[i]) * bias.get("so2", 1.0)
        co_mg = max(0.0, co_cams[i]) / 1000.0

        solar_factor = _clamp(sws[i] / 700.0, 0.0, 1.0)
        ozone_sensitivity = _clamp(0.55 * solar_factor + 0.25 * _clamp((temps[i] - 20.0) / 18.0, 0, 1) + 0.20 * _clamp((100 - rhs[i]) / 70.0, 0, 1), 0, 1)

        pm25_hist.append(corrected_pm25); pm25_hist = pm25_hist[-24:]
        pm10_hist.append(pm10); pm10_hist = pm10_hist[-24:]

        pollutants = {
            "pm25": corrected_pm25,
            "pm10": pm10,
            "o3": o3,
            "no2": no2,
            "so2": so2,
            "co": co_mg,
        }
        aq_result = cpcb.compute_cpcb_aqi(pollutants)
        inv["effective_boundary_layer_height_m"] = round(effective_pbl, 1)
        inv["aerosol_feedback_index"] = round(aerosol_feedback, 3)
        inv["pbl_reduction_fraction"] = round(pbl_reduction, 3)
        inv["ventilation_coefficient_m2_s"] = round(ventilation, 1)
        inv["coupled_stability_score"] = round(_clamp(inv_score + 0.20 * aerosol_feedback + 0.15 * fire_score / 100.0, 0.0, 1.0), 3)
        inv["weather_to_chemistry_index"] = round(_clamp(0.45 * inv_score + 0.35 * _clamp(winds[i] / 8.0, 0, 1) + 0.20 * ozone_sensitivity, 0.0, 1.0), 3)
        inv["regional_biomass_impact_score"] = round(fire_score, 1)
        inv["upwind_fire_count"] = int(fire_hour.get("upwind_fire_count", 0))

        series.append({
            "index": i,
            "day_number": i // 24 + 1,
            "time": times[i].split("T")[-1][:5],
            "datetime": times[i],
            "temperature": round(temps[i], 1),
            "humidity": round(rhs[i], 1),
            "wind_speed": round(winds[i], 2),
            "wind_direction": round(winddirs[i], 1),
            "pressure": round(pressures[i], 1),
            "rain": round(rains[i], 2),
            "pm25_cams": round(cams_pm, 2),
            "forecast_pm25": round(corrected_pm25, 2),
            "ml_predicted_pm25": round(corrected_pm25, 2),
            "forecast_pm10": round(pm10, 2),
            "forecast_o3": round(o3, 2),
            "forecast_no2": round(no2, 2),
            "forecast_so2": round(so2, 2),
            "forecast_co": round(co_mg, 3),
            "chemistry_source": raw.get("chemistry_source", "CAMS via Open-Meteo"),
            "weather_source": raw.get("weather_source", "Open-Meteo"),
            "cpcb_screening_aqi": aq_result["aqi"],
            "ml_predicted_aqi": aq_result["aqi"],
            "category": aq_result["category"],
            "color": aq_result["color"],
            "dominant_pollutant": aq_result["dominant_pollutant"],
            "aqi_sub_indices": aq_result["sub_indices"],
            "physical_diagnostics": inv,
            "inversion_strength_k_per_km": inv["inversion_strength_k_per_km"],
            "boundary_layer_height_m": inv["boundary_layer_height_m"],
            "effective_boundary_layer_height_m": effective_pbl,
            "fire_impact_score": fire_score,
            "plume_probability_pct": fire_hour.get("plume_probability_pct", 0.0),
            "upwind_fire_count": fire_hour.get("upwind_fire_count", 0),
            "upwind_frp_mw": fire_hour.get("upwind_frp_mw", 0.0),
            "plume_transport_status": fire_hour.get("transport_status", "No fire source data"),
            "aerosol_feedback_index": inv["aerosol_feedback_index"],
            "ozone_chemistry_sensitivity": ozone_sensitivity,
            "rain_washout_factor": rain_factor,
        })

    days = []
    for d in range(3):
        items = series[d * 24:(d + 1) * 24]
        if not items:
            continue
        peak_aqi_item = max(items, key=lambda x: x["ml_predicted_aqi"])
        peak_pm_item = max(items, key=lambda x: x["forecast_pm25"])
        peak_o3_item = max(items, key=lambda x: x["forecast_o3"])
        avg_inv = sum(x["inversion_strength_k_per_km"] for x in items) / len(items)
        peak_aqi = peak_aqi_item["ml_predicted_aqi"]
        if peak_aqi >= 450:
            grap_stage = "Stage IV (AQI ≥450)"
        elif peak_aqi >= 401:
            grap_stage = "Stage III (AQI 401–450)"
        elif peak_aqi >= 301:
            grap_stage = "Stage II (AQI 301–400)"
        elif peak_aqi >= 201:
            grap_stage = "Stage I (AQI 201–300)"
        else:
            grap_stage = "No GRAP stage by AQI band"
        avg_pbl = sum(x["boundary_layer_height_m"] for x in items) / len(items)
        max_fire = max(x["fire_impact_score"] for x in items)
        days.append({
            "day_number": d + 1,
            "title": ["Next 24 Hours", "24–48h Horizon", "48–72h Horizon"][d],
            "date": items[0]["datetime"].split("T")[0],
            "peak_aqi": peak_aqi,
            "avg_aqi": int(round(sum(x["ml_predicted_aqi"] for x in items) / len(items))),
            "grap_stage": grap_stage,
            "grap_reference": "CAQM revised GRAP schedule (21 Nov 2025); operational invocation is based on Delhi daily AQI and official CAQM decisions.",
            "grap_action": "Use the current CAQM GRAP schedule for any operational action; this forecast is an early-warning input, not an invocation order.",
            "peak_pm25": peak_pm_item["forecast_pm25"],
            "peak_pm10": max(x["forecast_pm10"] for x in items),
            "peak_o3": peak_o3_item["forecast_o3"],
            "peak_no2": max(x["forecast_no2"] for x in items),
            "max_inversion_strength_k_per_km": round(max(x["inversion_strength_k_per_km"] for x in items), 2),
            "avg_inversion_strength_k_per_km": round(avg_inv, 2),
            "avg_boundary_layer_height_m": round(avg_pbl, 1),
            "max_biomass_impact_score": round(max_fire, 1),
            "dominant_pollutant": peak_aqi_item["dominant_pollutant"],
            "category": peak_aqi_item["category"],
            "color": peak_aqi_item["color"],
            "advisory": cpcb.get_category_info(peak_aqi_item["ml_predicted_aqi"])["advisory"],
            "driver_summary": (
                f"Inversion/PBL trapping (max inversion {max(x['inversion_strength_k_per_km'] for x in items):.2f} K/km; "
                f"avg PBL {avg_pbl:.0f} m)" if avg_inv >= 1.0 or avg_pbl < 500 else
                f"Atmospheric ventilation (avg PBL {avg_pbl:.0f} m; winds {sum(x['wind_speed'] for x in items)/len(items):.1f} m/s)"
            ),
        })

    max_aqi = max((x["ml_predicted_aqi"] for x in series), default=0)
    max_pm = max((x["forecast_pm25"] for x in series), default=0.0)
    peak = max(series, key=lambda x: x["ml_predicted_aqi"], default={})
    return {
        "success": True,
        "location": location,
        "total_hours": len(series),
        "forecast": series,
        "day1": days[0] if len(days) > 0 else {},
        "day2": days[1] if len(days) > 1 else {},
        "day3": days[2] if len(days) > 2 else {},
        "max_72h_aqi": max_aqi,
        "peak_72h_pm25": round(max_pm, 2),
        "peak_hour": peak.get("datetime"),
        "is_emergency": max_aqi >= 300,
        "surge_window": peak.get("datetime", ""),
        "forecast_method": (
            "Hybrid 72h forecast: CAMS/Open-Meteo multi-pollutant chemistry + weather-aware local PM2.5 ML correction + "
            "vertical inversion/PBL diagnostics + bounded aerosol feedback + NASA FIRMS plume transport risk"
        ),
        "data_provenance": {
            "chemistry": raw.get("chemistry_source"),
            "meteorology": raw.get("weather_source"),
            "inversion": "Open-Meteo pressure-level temperature/geopotential height + boundary-layer height",
            "fire": fire.get("source"),
            "fire_cache_stale": fire.get("stale"),
            "fire_cache_age_hours": fire.get("cache_age_hours"),
            "local_history_points": initial_hist_len,
            "local_ml_history_source": ml_history_source,
            "local_bias_correction_applied": bool(bias),
            "local_bias_note": "Conservative correction from stored local history; not labeled official CPCB calibration.",
            "fallback_mode": raw.get("fallback", False),
        },
        "audit": {
            "history_used": {
                "points_used": initial_hist_len,
                "is_padded": initial_hist_len < 24,
                "source": ml_history_source,
                "note": "Local PM2.5 history is used as a short-range ML correction layer; the regional CAMS chemistry forecast remains the independent baseline."
            },
            "weather_forecast_hours": {
                "real_forecast_hours": sum(1 for i in range(hours) if not raw.get("fallback") and i < len(times_all)),
                "note": "Meteorological inputs include 2m conditions, wind, precipitation, PBL and 925/850 hPa profile diagnostics."
            },
            "chemistry_forecast_hours": len(times),
            "model_features": 31,
            "model_role": "Local PM2.5 correction only; not the sole source of the multi-pollutant forecast.",
            "aqi_basis": "CPCB-style screening sub-index interpolation for forecast concentrations; verify against official NAQI averaging/validation before operational use.",
            "limitations": [
                "The bundled ML model was trained on the demonstration / synthetic training dataset across 6 Active Monitoring Locations; real CPCB validation requires training and testing on operational field observations.",
                "FIRMS hotspots are active-fire detections and are not automatically classified as crop-residue burning.",
                "Fire transport is a bounded risk indicator, not a full emission-inventory chemical transport solver.",
                "CAMS/Open-Meteo provides regional chemistry guidance and does not replace a high-resolution WRF-Chem/CMAQ deployment."
            ],
        },
        "emergency_message": (
            f"72-hour outlook: peak screening AQI {max_aqi} ({peak.get('category','Unknown')}) at {peak.get('datetime','unknown')}. "
            f"Dominant pollutant: {peak.get('dominant_pollutant','unknown')}. "
            f"Inversion/PBL and regional fire-transport indicators are included in the forecast context."
        )
    }
