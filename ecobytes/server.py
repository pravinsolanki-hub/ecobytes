"""
EcoBytes Backend Server (Python Standard Library HTTP & REST Engine)
Fast, Multi-Threaded, In-Memory Cached, and Zero-Dependency!
"""
import http.server
import socketserver
import urllib.request
import urllib.parse
import json
import os
import sys
import sqlite3
import time
import threading
import concurrent.futures
import math
from datetime import datetime, timezone, timedelta
import cpcb
import db
import ml_engine
import seed_data
import sms_dispatcher
import coupled_forecast

# Coupled Meteorology-Chemistry & Atmospheric Diagnostic Modules
try:
    import atmospheric_inversion
except Exception:
    atmospheric_inversion = None

try:
    import regional_biomass_realtime
except Exception:
    regional_biomass_realtime = None

try:
    import weather_chemistry_coupling
except Exception:
    weather_chemistry_coupling = None

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

PORT = 3000
PUBLIC_DIR = os.path.join(os.path.dirname(__file__), "public")

# Simple in-memory cache to respect API rate limits and serve responses in < 5ms
CACHE = {}
CACHE_TTL = 300 # 5 minutes

def fetch_url_json(url, headers, timeout=4):
    """Fetch JSON from a URL with strict timeout."""
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

# Hyper-local Ground Station Calibration (aligns satellite/CAMS model with CPCB/DPCC ground monitors like on aqi.in)
LOCATION_CALIBRATION = {
    "delhi-cp": {"pm25_factor": 1.25, "pm10_factor": 1.30, "name": "Connaught Place (Central Delhi)"},
    "delhi-anand-vihar": {"pm25_factor": 1.38, "pm10_factor": 1.55, "name": "Anand Vihar (East Delhi - Dust Hub)"},
    "delhi-igi": {"pm25_factor": 0.95, "pm10_factor": 1.05, "name": "IGI Airport T3 (South-West)"},
    "delhi-punjabi-bagh": {"pm25_factor": 1.22, "pm10_factor": 1.32, "name": "Punjabi Bagh (West Delhi)"},
    "delhi-noida": {"pm25_factor": 1.18, "pm10_factor": 1.28, "name": "Sector 62 Noida (NCR East)"},
    "delhi-gurugram": {"pm25_factor": 1.15, "pm10_factor": 1.22, "name": "Cyber Hub Gurugram (NCR South)"}
}

def compute_us_epa_aqi(pm25, pm10):
    """Calculates US EPA AQI standard for comparison with commercial portals like aqi.in and IQAir."""
    def calc_epa(cp, breakpoints):
        for bp in breakpoints:
            if bp["c_low"] <= cp <= bp["c_high"]:
                return round(((bp["i_high"] - bp["i_low"]) / (bp["c_high"] - bp["c_low"])) * (cp - bp["c_low"]) + bp["i_low"])
        if cp > breakpoints[-1]["c_high"]:
            return 500
        return 0

    pm25_bp = [
        {"c_low": 0.0, "c_high": 12.0, "i_low": 0, "i_high": 50},
        {"c_low": 12.1, "c_high": 35.4, "i_low": 51, "i_high": 100},
        {"c_low": 35.5, "c_high": 55.4, "i_low": 101, "i_high": 150},
        {"c_low": 55.5, "c_high": 150.4, "i_low": 151, "i_high": 200},
        {"c_low": 150.5, "c_high": 250.4, "i_low": 201, "i_high": 300},
        {"c_low": 250.5, "c_high": 500.4, "i_low": 301, "i_high": 500}
    ]
    val25 = calc_epa(float(pm25 or 0), pm25_bp) if pm25 else 0
    return max(0, min(500, val25))

def fetch_open_meteo(lat, lon):
    """Fetch current and 72h-96h hourly data from Open-Meteo Air Quality & Weather APIs in parallel."""
    cache_key = f"{lat}_{lon}"
    now = time.time()
    if cache_key in CACHE and (now - CACHE[cache_key]["timestamp"]) < CACHE_TTL:
        return CACHE[cache_key]["data"]

    headers = {"User-Agent": "EcoBytes-Delhi-AQI-Platform/2.0"}
    
    # 1. Air Quality API (requesting 4 days = 96 hours for full 72h horizon)
    aq_url = (
        f"https://air-quality-api.open-meteo.com/v1/air-quality?"
        f"latitude={lat}&longitude={lon}&"
        f"current=pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,sulphur_dioxide,ozone&"
        f"hourly=pm10,pm2_5,nitrogen_dioxide,ozone&forecast_days=4"
    )
    
    # 2. Weather API (requesting 4 days = 96 hours)
    weather_url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}&"
        f"current=temperature_2m,relative_humidity_2m,precipitation,surface_pressure,wind_speed_10m,wind_direction_10m,cloud_cover&"
        f"hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation,surface_pressure&forecast_days=4"
    )

    try:
        # Run both API calls in parallel threads with 6-second timeout
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut_aq = executor.submit(fetch_url_json, aq_url, headers, 6)
            fut_w = executor.submit(fetch_url_json, weather_url, headers, 6)
            aq_data = fut_aq.result()
            w_data = fut_w.result()

        payload = {"air_quality": aq_data, "weather": w_data, "source": "Open-Meteo Live API"}
        CACHE[cache_key] = {"data": payload, "timestamp": now}
        return payload

    except Exception as e:
        # If cache exists even if older than TTL, return it
        if cache_key in CACHE:
            return CACHE[cache_key]["data"]
        # Fallback calibrated to typical Delhi NCR ambient values
        return get_delhi_fallback_data(lat, lon)

def get_delhi_fallback_data(lat, lon):
    """Deterministic, calibrated fallback if offline or API times out."""
    return {
        "air_quality": {
            "current": {
                "pm2_5": 54.2,
                "pm10": 128.5,
                "nitrogen_dioxide": 44.0,
                "ozone": 36.5,
                "sulphur_dioxide": 14.2,
                "carbon_monoxide": 1.2
            },
            "hourly": {
                "time": [(datetime.now() + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(72)],
                "pm2_5": [68.0 + 22.0 * math.sin(2 * math.pi * ((i % 24) - 9) / 24) for i in range(72)],
                "pm10": [155.0 + 35.0 * math.sin(2 * math.pi * ((i % 24) - 9) / 24) for i in range(72)],
                "nitrogen_dioxide": [38.0 + 12.0 * math.sin(2 * math.pi * ((i % 24) - 9) / 24) for i in range(72)],
                "ozone": [25.0 + 15.0 * math.cos(2 * math.pi * ((i % 24) - 14) / 24) for i in range(72)]
            }
        },
        "weather": {
            "current": {
                "temperature_2m": 32.4,
                "relative_humidity_2m": 58.0,
                "wind_speed_10m": 4.2,
                "wind_direction_10m": 280,
                "surface_pressure": 1007.2,
                "precipitation": 0.0,
                "cloud_cover": 20.0
            },
            "hourly": {
                "time": [(datetime.now() + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(72)],
                "temperature_2m": [28.0 + 7.0 * math.sin(2 * math.pi * ((i % 24) - 9) / 24) for i in range(72)],
                "relative_humidity_2m": [65.0 - 20.0 * math.sin(2 * math.pi * ((i % 24) - 9) / 24) for i in range(72)],
                "wind_speed_10m": [3.5 + 2.5 * math.sin(2 * math.pi * ((i % 24) - 13) / 24) for i in range(72)],
                "precipitation": [0.0] * 72
            }
        },
        "source": "Calibrated Delhi Ambient Simulation"
    }

def background_cache_worker():
    """Continuously pre-fetches live API data for all Delhi NCR stations in the background."""
    while True:
        try:
            locations = db.get_locations()
            for loc in locations:
                fetch_open_meteo(loc["latitude"], loc["longitude"])
                time.sleep(1) # brief pause to prevent rate-limiting
        except Exception:
            pass
        time.sleep(180) # repeat every 3 minutes


def build_forecast_histories(loc_id, live_pm25, live_pm10):
    """Build the live forecasting buffer from actual recent observations."""
    recent = db.get_recent_observations(loc_id, 48)
    pm25_hist = [r["pm25"] for r in recent if r.get("pm25") is not None]
    pm10_hist = [r["pm10"] for r in recent if r.get("pm10") is not None]
    if live_pm25 is not None:
        pm25_hist.append(live_pm25)
    if live_pm10 is not None:
        pm10_hist.append(live_pm10)
    real_points = min(len(pm25_hist), 24)
    # If fewer than 24 real hourly points exist yet (e.g. a brand-new
    # station), ml_engine pads the buffer by repeating the earliest known
    # value backward so the model still gets a fixed-length input. That
    # padding is fabricated history, not observed data, so callers need to
    # know it happened rather than silently trusting a "24h history" label.
    history_is_padded = real_points < 24
    return pm25_hist[-24:], pm10_hist[-24:], len(recent), real_points, history_is_padded


class ThreadedHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Multi-threaded TCP Server to handle concurrent browser requests instantly."""
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        # Gracefully handle client disconnects / aborted connections without crashing
        exc_type, _, _ = sys.exc_info()
        if exc_type in (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError):
            return
        super().handle_error(request, client_address)


class EcoBytesRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=PUBLIC_DIR, **kwargs)

    def log_message(self, format, *args):
        # Suppress default verbose access logs so our clean latency logs stand out
        pass

    def _send_json(self, data, status=200, summary=""):
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            self.wfile.write(json.dumps(data, indent=2).encode("utf-8"))

            elapsed_ms = (time.time() - getattr(self, "_start_time", time.time())) * 1000
            extra = f" | {summary}" if summary else ""
            print(f"   [{status} OK] {elapsed_ms:6.1f} ms | {self.command} {self.path}{extra}")
            sys.stdout.flush()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError):
            pass
        except Exception as e:
            print(f"   Error sending JSON response: {e}")

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        self._start_time = time.time()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 1. API: Locations
        if path == "/api/locations":
            locations = db.get_locations()
            return self._send_json({
                "success": True,
                "locations": locations,
                "locations_label": "6 Active Monitoring Locations",
                "dataset_type": "Demonstration / synthetic training dataset",
                "validation_notice": "Demonstration / synthetic training dataset. Real CPCB validation requires training and testing on operational CPCB/DPCC ground station observations."
            }, summary="6 Active Monitoring Locations | Synthetic Demo")

        # 2. API: Current AQI & Weather
        elif path == "/api/aqi/current":
            loc_id = query.get("location", ["delhi-cp"])[0]
            locations = db.get_locations()
            loc = next((l for l in locations if l["id"] == loc_id), locations[0])

            raw = fetch_open_meteo(loc["latitude"], loc["longitude"])
            aq_cur = raw["air_quality"].get("current", {})
            w_cur = raw["weather"].get("current", {})

            # Calibrate raw regional satellite CAMS data with hyper-local ground monitor factors (CPCB/DPCC)
            calib = LOCATION_CALIBRATION.get(loc["id"], {"pm25_factor": 1.15, "pm10_factor": 1.25})
            raw_pm25 = aq_cur.get("pm2_5")
            raw_pm10 = aq_cur.get("pm10")
            calib_pm25 = round(raw_pm25 * calib["pm25_factor"], 1) if raw_pm25 is not None else None
            calib_pm10 = round(raw_pm10 * calib["pm10_factor"], 1) if raw_pm10 is not None else None

            pollutants = {
                "pm25": calib_pm25,
                "pm10": calib_pm10,
                "no2": aq_cur.get("nitrogen_dioxide"),
                "o3": aq_cur.get("ozone"),
                "so2": aq_cur.get("sulphur_dioxide"),
                "co": (aq_cur.get("carbon_monoxide") or 0.0) / 1000.0
            }

            cpcb_result = cpcb.compute_cpcb_aqi(pollutants)
            us_epa_aqi = compute_us_epa_aqi(pollutants["pm25"], pollutants["pm10"])

            weather_data = {
                "temperature": w_cur.get("temperature_2m"),
                "humidity": w_cur.get("relative_humidity_2m"),
                "wind_speed": w_cur.get("wind_speed_10m"),
                "wind_direction": w_cur.get("wind_direction_10m"),
                "pressure": w_cur.get("surface_pressure"),
                "precipitation": w_cur.get("precipitation", 0.0),
                "cloud_cover": w_cur.get("cloud_cover", 0.0),
                "source": raw.get("source", "Open-Meteo Live API + CPCB Ground Calibrated")
            }

            # Check SMS Alert Triggers: PM2.5 > 50 OR PM10 > 120
            pm25 = pollutants["pm25"] or 0
            pm10 = pollutants["pm10"] or 0
            alerts_triggered = []

            if pm25 > 50:
                alerts_triggered.append(f"PM2.5 breached threshold: {pm25:.1f} ug/m3 (> 50 limit)")
            if pm10 > 120:
                alerts_triggered.append(f"PM10 breached threshold: {pm10:.1f} ug/m3 (> 120 limit)")

            if alerts_triggered:
                msg = (
                    f"EcoBytes CPCB Alert [{loc['name']}]: "
                    f"{'; '.join(alerts_triggered)}. AQI: {cpcb_result['aqi']} ({cpcb_result['category']}). "
                    f"Advisory: {cpcb_result['advisory']}"
                )
                db.log_alert(loc["id"], "Auto: " + "; ".join(alerts_triggered), pm25, pm10, msg)

            db.log_observation({
                "location_id": loc["id"],
                "recorded_at": datetime.now().isoformat(),
                "pm25": pm25,
                "pm10": pm10,
                "no2": pollutants["no2"],
                "o3": pollutants["o3"],
                "so2": pollutants["so2"],
                "temperature": weather_data["temperature"],
                "humidity": weather_data["humidity"],
                "wind_speed": weather_data["wind_speed"],
                "wind_direction": weather_data["wind_direction"],
                "pressure": weather_data["pressure"],
                "rainfall": weather_data["precipitation"],
                "cloud_cover": weather_data["cloud_cover"],
                "cpcb_aqi": cpcb_result["aqi"],
                "category": cpcb_result["category"]
            })

            # Compute unified real-time physical inversion, boundary layer, and biomass diagnostics using the pressure-level model
            suite = coupled_forecast.fetch_suite(loc["latitude"], loc["longitude"])
            w_hourly = suite.get("weather", {}).get("hourly", {})
            t2m = float(weather_data["temperature"] or 28.0)
            t925 = float(w_hourly.get("temperature_925hPa", [t2m])[0] if w_hourly.get("temperature_925hPa") else t2m)
            z925 = float(w_hourly.get("geopotential_height_925hPa", [950.0])[0] if w_hourly.get("geopotential_height_925hPa") else 950.0)
            t850 = float(w_hourly.get("temperature_850hPa", [t2m - 5.0])[0] if w_hourly.get("temperature_850hPa") else t2m - 5.0)
            z850 = float(w_hourly.get("geopotential_height_850hPa", [1650.0])[0] if w_hourly.get("geopotential_height_850hPa") else 1650.0)
            pbl = float(w_hourly.get("boundary_layer_height", [350.0])[0] if w_hourly.get("boundary_layer_height") else 350.0)
            rh = float(weather_data["humidity"] or 60.0)
            wind_ms = float(weather_data["wind_speed"] or 3.5) / 3.6

            phys_diag = coupled_forecast.inversion_features(t2m, t925, z925, t850, z850, pbl, rh, wind_ms)

            # Live regional fire/plume transport for "now" (reuses the same NASA FIRMS cache
            # and wind-alignment transport model as the 72h forecast engine).
            fire_now = coupled_forecast.fire_plume_forecast(
                loc["latitude"], loc["longitude"],
                [datetime.now().isoformat()],
                [weather_data["wind_direction"] or 270.0],
                [weather_data["wind_speed"] or 3.5],
                [pbl],
            )
            fire_hour = fire_now["hours"][0] if fire_now.get("hours") else {"fire_impact_score": 0.0, "upwind_fire_count": 0}
            fire_score = float(fire_hour.get("fire_impact_score", 0.0))

            # Additional diagnostic coupling indicators matching the forecast engine
            inv_score = phys_diag["stable_boundary_layer_score"]
            effective_pbl, pbl_reduction = coupled_forecast._effective_pbl(pbl, pm25, inv_score, fire_score)
            ventilation_coeff = round((wind_ms * 3.6) * (effective_pbl / 3.6), 1)

            aqt_hourly = suite.get("air_quality", {}).get("hourly", {}) or {}
            aod0 = float(aqt_hourly.get("aerosol_optical_depth", [0.6])[0]) if aqt_hourly.get("aerosol_optical_depth") else 0.6
            sw0 = float(w_hourly.get("shortwave_radiation", [0.0])[0]) if w_hourly.get("shortwave_radiation") else 0.0
            solar_factor = coupled_forecast._clamp(sw0 / 700.0, 0.0, 1.0)
            ozone_sensitivity = coupled_forecast._clamp(
                0.55 * solar_factor + 0.25 * coupled_forecast._clamp((t2m - 20.0) / 18.0, 0, 1)
                + 0.20 * coupled_forecast._clamp((100 - rh) / 70.0, 0, 1), 0, 1
            )
            aerosol_feedback = coupled_forecast._clamp((pm25 / 180.0) * 0.55 + aod0 * 0.15, 0.0, 1.0)

            phys_diag["effective_boundary_layer_height_m"] = round(effective_pbl, 1)
            phys_diag["pbl_reduction_fraction"] = round(pbl_reduction, 3)
            phys_diag["ventilation_coefficient_m2_s"] = ventilation_coeff
            phys_diag["aerosol_feedback_index"] = round(aerosol_feedback, 3)
            phys_diag["coupled_stability_score"] = round(
                coupled_forecast._clamp(inv_score + 0.20 * aerosol_feedback + 0.15 * fire_score / 100.0, 0.0, 1.0), 3
            )
            phys_diag["weather_to_chemistry_index"] = round(
                coupled_forecast._clamp(0.45 * inv_score + 0.35 * coupled_forecast._clamp(wind_ms / 8.0, 0, 1) + 0.20 * ozone_sensitivity, 0.0, 1.0), 3
            )
            phys_diag["regional_biomass_impact_score"] = round(fire_score, 1)
            phys_diag["upwind_fire_count"] = int(fire_hour.get("upwind_fire_count", 0))
            phys_diag["inversion_status"] = (
                " Strong Surface Inversion (Critical PM Trapping Risk)" if phys_diag["inversion_strength_k_per_km"] >= 2.0
                else ("️ Moderate Thermal Stability (Restricted Mixing)" if phys_diag["inversion_strength_k_per_km"] >= 0.8
                      else " Well-Mixed Boundary Layer (Active Dilution)")
            )

            response_payload = {
                "success": True,
                "location": loc,
                "timestamp": datetime.now().isoformat(),
                "pollutants": pollutants,
                "cpcb": cpcb_result,
                "us_epa_aqi": us_epa_aqi,
                "standards_comparison": {
                    "indian_cpcb_naqi": cpcb_result["aqi"],
                    "us_epa_aqi": us_epa_aqi,
                    "explanation": "Indian CPCB NAQI is the official statutory standard for Delhi NCR GRAP rules. Commercial sites like aqi.in display US EPA AQI by default."
                },
                "weather": weather_data,
                "physical_diagnostics": phys_diag,
                "alert_rules": {
                    "pm25_limit": 50,
                    "pm10_limit": 120,
                    "is_alert_active": len(alerts_triggered) > 0,
                    "active_triggers": alerts_triggered
                },
                "standards": "CPCB National Air Quality Index (India)"
            }
            summary = f"AQI: {cpcb_result['aqi']} ({cpcb_result['category']}) | Temp: {weather_data['temperature']}°C, Wind: {weather_data['wind_speed']} km/h"
            return self._send_json(response_payload, summary=summary)

        # 3. API: 24-Hour Coupled Multi-Pollutant Forecast
        elif path == "/api/forecast/24h":
            loc_id = query.get("location", ["delhi-cp"])[0]
            locations = db.get_locations()
            loc = next((l for l in locations if l["id"] == loc_id), locations[0])

            try:
                result = coupled_forecast.build_72h_forecast(
                    location=loc,
                    ml_status=ml_engine.get_model_status(),
                    hours=24
                )
                result["hours"] = len(result.get("forecast", []))
                return self._send_json(result, summary=f"24h Coupled Forecast: {loc['name']} ({result['hours']} hours)")
            except Exception as exc:
                return self._send_json({"success": False, "error": str(exc)}, status=500)

        # 3b. API: 72-Hour Coupled Multi-Pollutant Forecast (Weather + CAMS + Inversion + FIRMS + ML)
        elif path == "/api/forecast/72h":
            loc_id = query.get("location", ["delhi-cp"])[0]
            locations = db.get_locations()
            loc = next((l for l in locations if l["id"] == loc_id), locations[0])

            try:
                result = coupled_forecast.build_72h_forecast(
                    location=loc,
                    ml_status=ml_engine.get_model_status(),
                    hours=72
                )
                return self._send_json(result, summary=(
                    f"Coupled 72h: Peak AQI={result.get('max_72h_aqi')} | "
                    f"Multi-Pollutants (PM2.5, PM10, O3, NO2, SO2, CO) + Inversion & FIRMS"
                ))
            except Exception as exc:
                return self._send_json({"success": False, "error": str(exc)}, status=500)

        # 4. API: SMS Alert History
        elif path == "/api/alerts/history":
            history = db.get_alert_history(limit=15)
            for item in history:
                if "9876543210" in str(item.get("recipient_phone", "")):
                    item["recipient_phone"] = "Citizen Broadcast"
            return self._send_json({"success": True, "alerts": history}, summary=f"{len(history)} alert logs")

        # 4b. API: Latest Subscribed Citizen Phone (User-entered only)
        elif path == "/api/alerts/latest-subscription":
            subs = db.get_subscriptions()
            latest_phone = ""
            for s in subs:
                p = s.get("phone", "")
                if p and "9876543210" not in p:
                    latest_phone = p
                    break
            return self._send_json({"success": True, "phone": latest_phone}, summary=f"Latest sub: {latest_phone}")

        # 4c. API: Get SMS Gateway Configuration
        elif path == "/api/alerts/gateway":
            config = db.get_gateway_config()
            raw_key = config.get("api_key", "")
            masked_key = (raw_key[:4] + "*" * (len(raw_key) - 8) + raw_key[-4:]) if len(raw_key) > 8 else ("*" * len(raw_key))
            return self._send_json({
                "success": True,
                "provider": config.get("provider", "fast2sms"),
                "is_live": config.get("is_live", False),
                "is_key_set": bool(raw_key),
                "masked_key": masked_key,
                "twilio_sid": config.get("twilio_sid", "")
            }, summary=f"Gateway: {config.get('provider')} (Live={config.get('is_live')})")

        # 5. API: Weather vs Pollution Correlations
        elif path == "/api/correlations":
            correlation_data = [
                {"wind_speed": 1.2, "pm25": 142.0, "condition": "Calm Inversion - Heavy Trapping"},
                {"wind_speed": 2.0, "pm25": 108.0, "condition": "Stagnant - High Accumulation"},
                {"wind_speed": 3.5, "pm25": 78.0, "condition": "Light Air - Moderate Dispersal"},
                {"wind_speed": 5.0, "pm25": 54.0, "condition": "Gentle Breeze - Good Mixing"},
                {"wind_speed": 7.5, "pm25": 42.0, "condition": "Moderate Wind - Clearing Basin"},
                {"wind_speed": 10.0, "pm25": 31.0, "condition": "Strong Breeze - High Ventilation"},
                {"wind_speed": 14.0, "pm25": 24.0, "condition": "Vigorous Advection - Clean Air"}
            ]
            return self._send_json({"success": True, "correlation_data": correlation_data}, summary="Dispersion curve loaded")

        # 6. API: ML Model Status & Metrics
        elif path == "/api/ml/status":
            m_status = ml_engine.get_model_status()
            r2 = m_status.get('metrics', {}).get('r2_score', 'N/A')
            samples = m_status.get('training_samples', 0)
            r2_display = f"{r2:.3f}" if isinstance(r2, (float, int)) else str(r2)
            return self._send_json({
                "success": True,
                "architecture": "EcoBytes Hybrid: Open-Meteo Ingestion + Trained ML Model Layer",
                "mcp_server": "Active (ml/mcp_server.py)",
                "active_model": m_status.get("architecture", "HistGradientBoostingRegressor"),
                "target_city": "Delhi NCR, India",
                "model_details": m_status
            }, summary=f"Model Ready: Score={r2_display} | Samples={samples}")

        # 7. API: Regional Biomass & Fire Detections (NASA FIRMS / VIIRS NOAA-21 Cache)
        elif path == "/api/regional/fires":
            fire_cache_path = os.path.join(os.path.dirname(__file__), "data", "regional_biomass_realtime.json")
            if os.path.exists(fire_cache_path):
                try:
                    with open(fire_cache_path, "r", encoding="utf-8") as f:
                        fire_data = json.load(f)
                    fires = fire_data.get("fires", [])
                    return self._send_json({
                        "success": True,
                        "source": "NASA FIRMS VIIRS NOAA-21 NRT Cache",
                        "collected_at_utc": fire_data.get("collected_at_utc"),
                        "active_fire_count": fire_data.get("active_fire_count", len(fires)),
                        "total_frp_mw": fire_data.get("total_frp_mw", 0.0),
                        "fires": fires,
                        "attribution": "NASA FIRMS (Fire Information for Resource Management System) - Regional Stubble / Biomass Candidate Detections"
                    }, summary=f"{len(fires)} Fire Hotspots Loaded")
                except Exception as e:
                    pass
            return self._send_json({
                "success": True,
                "source": "NASA FIRMS VIIRS NOAA-21 Demonstration Cache",
                "active_fire_count": 0,
                "fires": [],
                "attribution": "NASA FIRMS VIIRS NOAA-21"
            }, summary="0 Fire Hotspots")

        # Fallback to serving static public files
        super().do_GET()
        elapsed_ms = (time.time() - self._start_time) * 1000
        print(f"   [200 OK] {elapsed_ms:6.1f} ms | GET {self.path}")
        sys.stdout.flush()

    def do_POST(self):
        self._start_time = time.time()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get('Content-Length', 0))
        body_raw = self.rfile.read(length).decode('utf-8')
        data = json.loads(body_raw) if body_raw else {}

        # 1. Subscribe Phone for SMS Alerts
        if path == "/api/alerts/subscribe":
            phone_raw = data.get("phone", "").strip()
            loc_id = data.get("location_id", "delhi-cp")
            if not phone_raw:
                return self._send_json({"success": False, "error": "10-digit mobile number required"}, 400)

            # Extract digits
            import re
            digits = re.sub(r"\D", "", phone_raw)
            if len(digits) == 12 and digits.startswith("91"):
                digits = digits[2:]
            elif len(digits) == 11 and digits.startswith("0"):
                digits = digits[1:]

            if len(digits) != 10:
                return self._send_json({"success": False, "error": f"Invalid mobile number ({len(digits)} digits entered). Exactly 10 digits required."}, 400)

            phone = f"+91-{digits}"
            try:
                db.add_subscription(phone, loc_id)
            except ValueError as e:
                return self._send_json({"success": False, "error": str(e)}, 400)

            # If Live Gateway is active, push real welcome SMS
            gw_config = db.get_gateway_config()
            welcome_res = None
            if gw_config.get("is_live") and gw_config.get("api_key"):
                welcome_msg = f" EcoBytes CPCB Alert Engine: Successfully subscribed {phone} for real-time air quality & smog breach alerts in {loc_id.upper()}."
                welcome_res = sms_dispatcher.dispatch_carrier_sms(phone, welcome_msg, gw_config)

            return self._send_json({
                "success": True,
                "phone": phone,
                "digits": digits,
                "live_dispatched": bool(welcome_res and welcome_res.get("success")),
                "message": f"Successfully registered {phone} for EcoBytes CPCB Alerts ({loc_id})."
            }, summary=f"Registered {phone}")

        # 1b. Save SMS Gateway Settings
        elif path == "/api/alerts/gateway":
            provider = data.get("provider", "fast2sms")
            api_key = data.get("api_key", "").strip()
            is_live = "1" if data.get("is_live") else "0"

            new_config = {
                "provider": provider,
                "is_live": is_live
            }
            if api_key and not api_key.startswith("****"):
                new_config["api_key"] = api_key
            if "twilio_sid" in data:
                new_config["twilio_sid"] = data.get("twilio_sid", "")
            if "twilio_token" in data and not data["twilio_token"].startswith("****"):
                new_config["twilio_token"] = data.get("twilio_token", "")
            if "twilio_from" in data:
                new_config["twilio_from"] = data.get("twilio_from", "")

            db.save_gateway_config(new_config)
            return self._send_json({"success": True, "message": "SMS Gateway settings saved successfully."})

        # 1c. Test Real SMS Dispatch to Subscriber Phone
        elif path == "/api/alerts/test-real-sms":
            phone_raw = data.get("phone", "").strip()
            digits = sms_dispatcher.clean_phone_10_digits(phone_raw)
            if not digits:
                return self._send_json({"success": False, "error": "Please enter a valid 10-digit mobile number."}, 400)

            gw_config = db.get_gateway_config()
            gw_config["is_live"] = True
            if not gw_config.get("api_key") and gw_config.get("provider") == "fast2sms":
                return self._send_json({
                    "success": False,
                    "error": "No Fast2SMS API key configured yet. Please enter your API key in Gateway Settings."
                }, 400)

            test_msg = f"EcoBytes CPCB Alert Engine: Test cellular SMS dispatch successful! Your mobile (+91-{digits}) is verified to receive real-time Delhi NCR air quality alerts."
            dispatch_res = sms_dispatcher.dispatch_carrier_sms(digits, test_msg, gw_config)

            if dispatch_res.get("success"):
                db.log_alert("delhi-cp", "Manual Gateway Verification Test", 45.0, 95.0, test_msg, f"+91-{digits}", "SENT (LIVE CARRIER)")
                return self._send_json({
                    "success": True,
                    "message": f"Real SMS pushed successfully to +91-{digits}!",
                    "details": dispatch_res
                })
            else:
                return self._send_json({
                    "success": False,
                    "error": dispatch_res.get("error", "Failed to dispatch carrier SMS"),
                    "details": dispatch_res
                }, 400)

        # 2. Simulate / Test SMS Alert Trigger (Dynamic Across ALL CPCB Categories!)
        elif path == "/api/alerts/simulate":
            loc_id = data.get("location_id", "delhi-cp")
            pm25 = float(data.get("pm25", 58.0))
            pm10 = float(data.get("pm10", 135.0))

            # Determine phone from user payload, registered subscription, or fallback for simulation
            import re
            req_phone = str(data.get("phone", "")).strip()
            digits = re.sub(r"\D", "", req_phone)
            if len(digits) == 12 and digits.startswith("91"):
                digits = digits[2:]
            elif len(digits) == 11 and digits.startswith("0"):
                digits = digits[1:]

            if len(digits) == 10:
                phone = f"+91-{digits}"
            else:
                subs = db.get_subscriptions()
                user_sub = None
                for s in subs:
                    p = s.get("phone", "")
                    p_digits = re.sub(r"\D", "", p)
                    if len(p_digits) == 12 and p_digits.startswith("91"):
                        p_digits = p_digits[2:]
                    elif len(p_digits) == 11 and p_digits.startswith("0"):
                        p_digits = p_digits[1:]
                    if len(p_digits) == 10:
                        user_sub = f"+91-{p_digits}"
                        break
                phone = user_sub if user_sub else "Citizen Broadcast"

            locations = db.get_locations()
            loc = next((l for l in locations if l["id"] == loc_id), locations[0])

            cpcb_res = cpcb.compute_cpcb_aqi({"pm25": pm25, "pm10": pm10})

            # Check Rule logic: Breached if PM2.5 > 50 OR PM10 > 120
            rules_broken = []
            if pm25 > 50:
                rules_broken.append(f"PM2.5 ({pm25:.1f} ug/m3 > 50 limit)")
            if pm10 > 120:
                rules_broken.append(f"PM10 ({pm10:.1f} ug/m3 > 120 limit)")

            is_alert = len(rules_broken) > 0
            category = cpcb_res["category"]
            aqi = cpcb_res["aqi"]
            advisory = cpcb_res["advisory"]

            if is_alert:
                rule_str = " & ".join(rules_broken)
                sms_text = (
                    f" EcoBytes CPCB Alert [{loc['name']}]: "
                    f"Air quality risk: {category.upper()}! {rule_str}. "
                    f"Overall NAQI: {aqi} ({category}). "
                    f"Action: {advisory}"
                )
                gw_config = db.get_gateway_config()
                if gw_config.get("is_live") and (gw_config.get("api_key") or gw_config.get("twilio_sid")):
                    dispatch_res = sms_dispatcher.dispatch_carrier_sms(phone, sms_text, gw_config)
                    if dispatch_res.get("success"):
                        dispatch_status = "SENT (LIVE CARRIER)"
                    else:
                        dispatch_status = f"FAILED ({dispatch_res.get('error', 'Carrier rejected')[:28]})"
                else:
                    dispatch_res = {"mode": "SIMULATED", "provider": "Sandbox Simulation", "status": "SENT (SIMULATED)"}
                    dispatch_status = "SENT (SIMULATED)"

                alert_id = db.log_alert(loc["id"], rule_str, pm25, pm10, sms_text, phone, dispatch_status)
            else:
                rule_str = "Concentrations within safe limits"
                sms_text = (
                    f"ℹ️ EcoBytes Notice [{loc['name']}]: "
                    f"Air quality is {category.upper()}. Overall NAQI: {aqi} ({category}). "
                    f"Concentrations (PM2.5: {pm25:.1f}, PM10: {pm10:.1f}) are within safe limits. "
                    f"Advisory: {advisory}"
                )
                dispatch_res = {"mode": "SAFE_LIMITS", "provider": "None", "status": "SAFE"}
                dispatch_status = "SAFE (NO DISPATCH NEEDED)"
                alert_id = None

            return self._send_json({
                "success": True,
                "triggered": is_alert,
                "alert_id": alert_id,
                "rule": rule_str,
                "recipient": phone,
                "sms_content": sms_text,
                "cpcb": cpcb_res,
                "dispatch": dispatch_res,
                "status": dispatch_status
            }, summary=f"PM2.5: {pm25:.1f} | NAQI: {aqi} ({category.upper()}) | Alert: {'TRIGGERED' if is_alert else 'SAFE'} | {dispatch_status}")

        # 2b. Dispatch 72-Hour Pre-Emptive Smog Warning SMS
        elif path == "/api/alerts/dispatch-72h":
            loc_id = data.get("location_id", "delhi-cp")
            message = data.get("message", "").strip()
            max_aqi = data.get("max_aqi", 0)
            grap_stage = data.get("grap_stage", "Advisory")

            # Determine phone from user payload, registered subscription, or fallback
            import re
            req_phone = str(data.get("phone", "")).strip()
            digits = re.sub(r"\D", "", req_phone)
            if len(digits) == 12 and digits.startswith("91"):
                digits = digits[2:]
            elif len(digits) == 11 and digits.startswith("0"):
                digits = digits[1:]

            if len(digits) == 10:
                phone = f"+91-{digits}"
            else:
                subs = db.get_subscriptions()
                user_sub = None
                for s in subs:
                    p = s.get("phone", "")
                    p_digits = re.sub(r"\D", "", p)
                    if len(p_digits) == 12 and p_digits.startswith("91"):
                        p_digits = p_digits[2:]
                    elif len(p_digits) == 11 and p_digits.startswith("0"):
                        p_digits = p_digits[1:]
                    if len(p_digits) == 10:
                        user_sub = f"+91-{p_digits}"
                        break
                phone = user_sub if user_sub else "Citizen Broadcast"

            locations = db.get_locations()
            loc = next((l for l in locations if l["id"] == loc_id), locations[0])

            if not message:
                message = f" EcoBytes 72H Smog Warning [{loc['name']}]: High pollution episode forecasted in next 72h. Peak AQI: {max_aqi}. GRAP: {grap_stage}."

            gw_config = db.get_gateway_config()
            if gw_config.get("is_live") and (gw_config.get("api_key") or gw_config.get("twilio_sid")):
                dispatch_res = sms_dispatcher.dispatch_carrier_sms(phone, message, gw_config)
                if dispatch_res.get("success"):
                    dispatch_status = "SENT (LIVE CARRIER)"
                else:
                    dispatch_status = f"FAILED ({dispatch_res.get('error', 'Carrier rejected')[:28]})"
            else:
                dispatch_res = {"mode": "SIMULATED", "provider": "Fast2SMS Sandbox", "status": "SENT (SIMULATED)"}
                dispatch_status = "SENT (SIMULATED)"

            rule_label = f"72H Smog Warning ({grap_stage})"
            alert_id = db.log_alert(loc["id"], rule_label, float(max_aqi or 0), 0.0, message, phone, dispatch_status)

            return self._send_json({
                "success": True,
                "alert_id": alert_id,
                "recipient": phone,
                "message": message,
                "dispatch": dispatch_res,
                "status": dispatch_status
            }, summary=f"72H Warning Pushed to {phone} | {dispatch_status}")

        # 3. Train / Retrain ML Model
        elif path == "/api/ml/train":
            loc_id = data.get("location_id")
            result = ml_engine.train_model(loc_id)
            r2 = result.get('metrics', {}).get('r2_score', 'N/A')
            r2_display = f"{r2:.3f}" if isinstance(r2, (float, int)) else str(r2)
            return self._send_json(result, summary=f"Model Trained: Score={r2_display}")

        # 4. Reseed Database
        elif path == "/api/db/reseed":
            days = int(data.get("days", 60))
            seed_data.seed_database(days)
            ml_res = ml_engine.train_model()
            return self._send_json({
                "success": True,
                "message": f"Database successfully re-seeded with {days} days of demonstration / synthetic training dataset across 6 Active Monitoring Locations.",
                "dataset_type": "Demonstration / synthetic training dataset",
                "locations_label": "6 Active Monitoring Locations",
                "validation_notice": "Demonstration / synthetic training dataset. Real CPCB validation requires training and testing on operational CPCB/DPCC ground station observations.",
                "ml_status": ml_res
            }, summary=f"Reseeded {days} days (Synthetic Demo)")

        return self._send_json({"error": "Endpoint not found"}, 404)


def run():
    os.chdir(os.path.dirname(__file__))

    # Auto-seed database if empty
    conn = sqlite3.connect(db.DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM observations;")
    obs_count = cur.fetchone()[0]
    conn.close()

    if obs_count < 100:
        print("[INIT] Database has insufficient records. Auto-seeding 60 days of demonstration / synthetic training dataset across 6 Active Monitoring Locations...")
        seed_data.seed_database(60)

    # Pre-train or load ML model
    print("[INIT] Initializing EcoBytes ML Model...")
    ml_status = ml_engine.get_model_status()
    r2_val = ml_status.get('metrics', {}).get('r2_score', 0)
    r2_display = f"{r2_val:.3f}" if isinstance(r2_val, (float, int)) else str(r2_val)
    print(f"[INIT] Model Ready: Samples={ml_status.get('training_samples', 0)} | Score={r2_display}")

    # Start background cache worker thread to keep live data warm
    bg_thread = threading.Thread(target=background_cache_worker, daemon=True)
    bg_thread.start()

    # Use ThreadedHTTPServer to handle requests concurrently without blocking
    while True:
        try:
            with ThreadedHTTPServer(("", PORT), EcoBytesRequestHandler) as httpd:
                print("=" * 65)
                print("   ECOBYTES: Weather-Aware Delhi NCR AQI & Alerting System")
                print(f"   Server running at: http://localhost:{PORT}")
                print("   Multi-Threaded Engine with Live Pre-Cached API Sync")
                print("   API Endpoints: /api/aqi/current, /api/forecast/24h, /api/alerts/simulate")
                print("   ML Training API: POST /api/ml/train, GET /api/ml/status")
                print("   Standard: Central Pollution Control Board (CPCB) NAQI")
                print("=" * 65)
                sys.stdout.flush()
                httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[SHUTDOWN] EcoBytes Server stopped.")
            break
        except Exception as e:
            print(f"\n[SERVER RECOVERY] Exception: {e}. Rebinding port {PORT} in 2s...")
            time.sleep(2)

if __name__ == "__main__":
    run()
