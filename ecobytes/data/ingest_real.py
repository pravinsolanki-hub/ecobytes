"""
data/ingest_real.py - Ingests atmospheric reanalysis & air-quality observations for Delhi-NCR stations.

Supported Data Sources:
1. Copernicus CAMS Reanalysis / Open-Meteo Air Quality (source='cams_reanalysis')
   - High-fidelity assimilated Eulerian atmospheric chemistry reanalysis (PM2.5, PM10, NO2, O3, SO2, CO + Surface Meteorology).
   - Publicly accessible, no API key needed.
   - NOTE: CAMS is a numerical atmospheric chemistry reanalysis model (~40km grid), NOT independent in-situ station ground truth.

2. OpenAQ v3 API (source='real_openaq')
   - In-situ regulatory monitoring station measurements via OPENAQ_API_KEY environment variable.

3. Local CPCB / DPCC Station CSV Exports (source='cpcb_station')
   - Ingests official continuous ambient air quality monitoring station (CAAQMS) CSV files.
"""

import os
import sys
import csv
import json
import sqlite3
import urllib.request
import urllib.parse
import argparse
from datetime import datetime, timedelta

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure parent directory is in python path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import db
import cpcb

DB_PATH = os.path.join(BASE_DIR, "ecobytes.db")

STATIONS = [
    {"id": "delhi-cp", "name": "Connaught Place", "lat": 28.6328, "lon": 77.2197},
    {"id": "delhi-anand-vihar", "name": "Anand Vihar", "lat": 28.6469, "lon": 77.3160},
    {"id": "delhi-igi", "name": "IGI Airport T3", "lat": 28.5562, "lon": 77.1000},
    {"id": "delhi-punjabi-bagh", "name": "Punjabi Bagh", "lat": 28.6692, "lon": 77.1264},
    {"id": "delhi-noida", "name": "Sector 62 Noida", "lat": 28.6280, "lon": 77.3649},
    {"id": "delhi-gurugram", "name": "Cyber Hub Gurugram", "lat": 28.4950, "lon": 77.0895},
]


def fetch_open_meteo_cams_history(lat: float, lon: float, start_date: str, end_date: str) -> dict | None:
    """
    Fetches hourly atmospheric reanalysis (air quality + surface meteorology) from Open-Meteo CAMS reanalysis.
    """
    aq_params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": "pm2_5,pm10,nitrogen_dioxide,ozone,sulphur_dioxide,carbon_monoxide",
        "timezone": "Asia/Kolkata"
    }
    aq_url = f"https://air-quality-api.open-meteo.com/v1/air-quality?{urllib.parse.urlencode(aq_params)}"

    met_params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,surface_pressure,rain",
        "timezone": "Asia/Kolkata"
    }
    met_url = f"https://archive-api.open-meteo.com/v1/archive?{urllib.parse.urlencode(met_params)}"

    try:
        req_aq = urllib.request.Request(aq_url, headers={"User-Agent": "EcoBytes-DataIngestion/2.0"})
        with urllib.request.urlopen(req_aq, timeout=15) as resp:
            aq_json = json.loads(resp.read().decode("utf-8"))

        req_met = urllib.request.Request(met_url, headers={"User-Agent": "EcoBytes-DataIngestion/2.0"})
        with urllib.request.urlopen(req_met, timeout=15) as resp:
            met_json = json.loads(resp.read().decode("utf-8"))

        return {"air_quality": aq_json.get("hourly", {}), "weather": met_json.get("hourly", {})}
    except Exception as e:
        print(f"️ [INGEST WARNING] Failed fetching CAMS reanalysis for ({lat}, {lon}): {e}")
        return None


def fetch_openaq_v3_measurements(lat: float, lon: float, days: int = 60) -> list[dict] | None:
    """
    Fetches real station observations from OpenAQ v3 API if OPENAQ_API_KEY is configured.
    """
    api_key = os.environ.get("OPENAQ_API_KEY")
    if not api_key:
        return None

    url = f"https://api.openaq.org/v3/locations?coordinates={lat},{lon}&radius=12000&limit=5"
    headers = {"X-API-Key": api_key, "User-Agent": "EcoBytes-DataIngestion/2.0"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("results", [])
    except Exception as e:
        print(f"️ [OPENAQ WARNING] Failed to query OpenAQ v3: {e}")
        return None


def ingest_cams_reanalysis(days: int = 60, source_label: str = "cams_reanalysis"):
    """
    Downloads and stores Copernicus CAMS reanalysis observations into SQLite database.
    """
    db.init_db()
    conn = db.get_connection()
    cur = conn.cursor()

    end_dt = datetime.now() - timedelta(days=2)  # Reanalyses finalize ~2 days ago
    start_dt = end_dt - timedelta(days=days)

    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")

    print(f"Downloading Copernicus CAMS Atmospheric Reanalysis ({start_str} to {end_str}) across 6 Delhi stations...")

    total_inserted = 0

    for stn in STATIONS:
        stn_id = stn["id"]
        lat, lon = stn["lat"], stn["lon"]
        print(f"  Fetching station {stn['name']} ({stn_id})...")

        payload = fetch_open_meteo_cams_history(lat, lon, start_str, end_str)
        if not payload:
            print(f"    Skipped {stn_id} (API unavailable)")
            continue

        aq_h = payload.get("air_quality", {})
        met_h = payload.get("weather", {})

        times = aq_h.get("time", [])
        if not times:
            continue

        rows_to_insert = []
        for i, t_str in enumerate(times):
            pm25 = aq_h.get("pm2_5", [None])[i] if i < len(aq_h.get("pm2_5", [])) else None
            pm10 = aq_h.get("pm10", [None])[i] if i < len(aq_h.get("pm10", [])) else None
            no2 = aq_h.get("nitrogen_dioxide", [None])[i] if i < len(aq_h.get("nitrogen_dioxide", [])) else None
            o3 = aq_h.get("ozone", [None])[i] if i < len(aq_h.get("ozone", [])) else None
            so2 = aq_h.get("sulphur_dioxide", [None])[i] if i < len(aq_h.get("sulphur_dioxide", [])) else None
            co = aq_h.get("carbon_monoxide", [None])[i] if i < len(aq_h.get("carbon_monoxide", [])) else None

            if co is not None and co > 50.0:
                co = co / 1000.0

            temp = met_h.get("temperature_2m", [25.0])[i] if i < len(met_h.get("temperature_2m", [])) else 25.0
            rh = met_h.get("relative_humidity_2m", [60.0])[i] if i < len(met_h.get("relative_humidity_2m", [])) else 60.0
            ws = met_h.get("wind_speed_10m", [4.0])[i] if i < len(met_h.get("wind_speed_10m", [])) else 4.0
            wd = met_h.get("wind_direction_10m", [280.0])[i] if i < len(met_h.get("wind_direction_10m", [])) else 280.0
            pres = met_h.get("surface_pressure", [1010.0])[i] if i < len(met_h.get("surface_pressure", [])) else 1010.0
            rain = met_h.get("rain", [0.0])[i] if i < len(met_h.get("rain", [])) else 0.0

            if pm25 is None:
                continue

            cpcb_res = cpcb.compute_cpcb_aqi({
                "pm25": pm25, "pm10": pm10, "no2": no2, "o3": o3, "so2": so2, "co": co
            })

            rows_to_insert.append((
                stn_id,
                t_str,
                round(pm25, 2) if pm25 is not None else None,
                round(pm10, 2) if pm10 is not None else None,
                round(no2, 2) if no2 is not None else None,
                round(o3, 2) if o3 is not None else None,
                round(so2, 2) if so2 is not None else None,
                round(temp, 2) if temp is not None else None,
                round(rh, 2) if rh is not None else None,
                round(ws, 2) if ws is not None else None,
                round(wd, 2) if wd is not None else None,
                round(pres, 2) if pres is not None else None,
                round(rain, 2) if rain is not None else 0.0,
                20.0,
                cpcb_res.get("aqi", 50),
                cpcb_res.get("category", "Moderate"),
                source_label,
                "Open-Meteo CAMS (model output)"
            ))

        if rows_to_insert:
            cur.execute("DELETE FROM observations WHERE location_id = ? AND source = ?", (stn_id, source_label))
            cur.executemany("""
                INSERT INTO observations (
                    location_id, recorded_at, pm25, pm10, no2, o3, so2,
                    temperature, humidity, wind_speed, wind_direction, pressure,
                    rainfall, cloud_cover, cpcb_aqi, category, source, provider
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, rows_to_insert)
            conn.commit()
            print(f"    Stored {len(rows_to_insert)} CAMS reanalysis hourly records for {stn_id}.")
            total_inserted += len(rows_to_insert)

    conn.close()
    print(f"CAMS reanalysis ingestion complete: Stored {total_inserted} observations with source='{source_label}'.")
    return total_inserted


def check_source_provenance_guard(source_label: str, filename: str = "", provider: str = ""):
    """
    Prevents numerical model or reanalysis outputs from masquerading as station observations.
    """
    restricted_targets = {"cpcb_station", "real_openaq", "station"}
    if source_label.lower() in restricted_targets or any(kw in source_label.lower() for kw in ("station", "openaq", "cpcb")):
        forbidden_keywords = ["open-meteo", "cams", "reanalysis"]
        for kw in forbidden_keywords:
            if kw in filename.lower():
                err_msg = (
                    f"MISLABELED DATA GUARD: Refusing to store rows as '{source_label}' because "
                    f"filename '{os.path.basename(filename)}' contains model keyword '{kw}'."
                )
                print(err_msg)
                raise ValueError(err_msg)
            if kw in source_label.lower():
                err_msg = (
                    f"MISLABELED DATA GUARD: Refusing to store rows as '{source_label}' because "
                    f"source label contains model keyword '{kw}'."
                )
                print(err_msg)
                raise ValueError(err_msg)
            if kw in provider.lower():
                err_msg = (
                    f"MISLABELED DATA GUARD: Refusing to store rows as '{source_label}' because "
                    f"provider '{provider}' indicates model/reanalysis output ('{kw}')."
                )
                print(err_msg)
                raise ValueError(err_msg)
    return True


def ingest_cpcb_csv(csv_path: str, default_station_id: str = "delhi-anand-vihar", source_label: str = "cpcb_station"):
    """
    Ingests local CPCB CAAQMS / CCR portal CSV files into SQLite database.
    Robustly handles CCR export column names (From Date, PM2.5 (ug/m3), etc.),
    day-first dates, missing/NA values, and enforces the mislabeled data guard.
    """
    if not os.path.exists(csv_path):
        print(f"ERROR: File not found: {csv_path}")
        return 0

    # 1. Enforce mislabeled data guard on filename and source_label
    check_source_provenance_guard(source_label, filename=csv_path)

    db.init_db()
    conn = db.get_connection()
    cur = conn.cursor()

    print(f"Parsing CPCB CSV file: {csv_path}...")
    rows_to_insert = []
    total_lines = 0
    parsed_count = 0
    skipped_count = 0
    skipped_reasons = {
        "missing_timestamp": 0,
        "unparseable_timestamp": 0,
        "missing_particulates": 0,
        "comment_or_header": 0
    }

    # Common NA representations in CPCB exports
    NA_STRINGS = {"", "na", "n/a", "none", "null", "nan", "-", "no data", "offline", "uncalibrated", "calibrating"}

    with open(csv_path, mode="r", encoding="utf-8-sig", errors="replace") as f:
        valid_lines = []
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                skipped_reasons["comment_or_header"] += 1
                if any(kw in stripped.lower() for kw in ("open-meteo", "cams", "reanalysis")):
                    check_source_provenance_guard(source_label, filename=csv_path, provider=stripped)
                continue
            valid_lines.append(line)

        if not valid_lines:
            print("Warning: CSV file contains no data rows.")
            conn.close()
            return 0

        reader = csv.DictReader(valid_lines)

        for line_no, raw_row in enumerate(reader, 1):
            total_lines += 1
            row = {k.strip().lower(): v.strip() for k, v in raw_row.items() if k}

            # Guard against provider column containing model output
            provider_val = row.get("provider", "")
            if provider_val:
                check_source_provenance_guard(source_label, filename=csv_path, provider=provider_val)

            # Find timestamp with flexible column matching
            t_str = None
            date_candidates = [
                "from date", "from", "timestamp", "recorded_at", "date",
                "date & time", "date time", "datetime", "sampling date", "to date"
            ]
            for cand in date_candidates:
                for rk, rv in row.items():
                    if cand in rk and rv and rv.lower() not in NA_STRINGS:
                        t_str = rv
                        break
                if t_str:
                    break

            if not t_str:
                skipped_count += 1
                skipped_reasons["missing_timestamp"] += 1
                continue

            # Parse timestamp supporting day-first (Indian standard DD-MM-YYYY) and ISO formats
            parsed_dt = None
            date_formats = (
                "%d-%m-%Y %H:%M:%S",
                "%d/%m/%Y %H:%M:%S",
                "%d-%m-%Y %H:%M",
                "%d/%m/%Y %H:%M",
                "%d-%b-%Y %H:%M",
                "%d/%b/%Y %H:%M",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%dT%H:%M",
                "%Y-%m-%d"
            )
            for fmt in date_formats:
                try:
                    parsed_dt = datetime.strptime(t_str, fmt)
                    break
                except ValueError:
                    continue

            if not parsed_dt:
                skipped_count += 1
                skipped_reasons["unparseable_timestamp"] += 1
                continue

            iso_timestamp = parsed_dt.strftime("%Y-%m-%dT%H:00:00")

            def parse_val(keys, default=None):
                for k in keys:
                    for rk, rv in row.items():
                        if k in rk:
                            cleaned = rv.strip().lower()
                            if cleaned not in NA_STRINGS:
                                try:
                                    return float(cleaned)
                                except ValueError:
                                    pass
                return default

            pm25 = parse_val(["pm2.5 (ug/m3)", "pm2.5(ug/m3)", "pm2.5", "pm25", "pm 2.5", "fine particulate"])
            pm10 = parse_val(["pm10 (ug/m3)", "pm10(ug/m3)", "pm10", "pm 10", "respirable particulate"])
            no2 = parse_val(["no2 (ug/m3)", "no2(ug/m3)", "no2", "nitrogen dioxide", "nox"])
            o3 = parse_val(["ozone (ug/m3)", "o3 (ug/m3)", "o3(ug/m3)", "ozone", "o3"])
            so2 = parse_val(["so2 (ug/m3)", "so2(ug/m3)", "so2", "sulphur dioxide", "sulfur dioxide"])
            co = parse_val(["co (mg/m3)", "co(mg/m3)", "co", "carbon monoxide"])
            temp = parse_val(["temp (degree c)", "temp", "temperature", "ambient temperature", "at"], default=25.0)
            rh = parse_val(["rh (%)", "humidity", "rh", "relative humidity"], default=60.0)
            ws = parse_val(["wind speed (m/s)", "ws (m/s)", "wind speed", "ws", "wind_speed"], default=4.0)
            wd = parse_val(["wind direction (deg)", "wd (deg)", "wind direction", "wd", "wind_direction"], default=280.0)
            pres = parse_val(["pressure", "pres", "bp", "barometric pressure"], default=1010.0)
            rain = parse_val(["rainfall", "rain", "precipitation"], default=0.0)
            station_raw = row.get("location_id") or row.get("station") or default_station_id
            stn_lower = station_raw.lower()
            if "anand vihar" in stn_lower:
                station = "delhi-anand-vihar"
            elif "punjabi bagh" in stn_lower:
                station = "delhi-punjabi-bagh"
            elif "connaught" in stn_lower or " cp" in stn_lower or stn_lower == "cp":
                station = "delhi-cp"
            elif "igi" in stn_lower or "airport" in stn_lower:
                station = "delhi-igi"
            elif "noida" in stn_lower:
                station = "delhi-noida"
            elif "gurugram" in stn_lower or "gurgaon" in stn_lower:
                station = "delhi-gurugram"
            else:
                station = station_raw
            direct_aqi = parse_val(["aqi", "cpcb_aqi", "air quality index", "index", "overall aqi", "cpcb aqi"])
            if pm25 is None and pm10 is None and direct_aqi is not None:
                # Invert CPCB AQI back to statutory PM2.5 concentration
                pm25 = cpcb.invert_aqi_to_concentration("pm25", direct_aqi)

            # CPCB stations must have at least one particulate measurement
            if pm25 is None and pm10 is None:
                skipped_count += 1
                skipped_reasons["missing_particulates"] += 1
                continue

            cpcb_res = cpcb.compute_cpcb_aqi({
                "pm25": pm25, "pm10": pm10, "no2": no2, "o3": o3, "so2": so2, "co": co
            })
            if direct_aqi is not None and not any(p is not None for p in (no2, o3, so2, co, pm10)):
                final_aqi_val = int(round(direct_aqi))
                final_cat_info = cpcb.get_category_info(final_aqi_val)
                cpcb_res["aqi"] = final_aqi_val
                cpcb_res["category"] = final_cat_info["category"]

            rows_to_insert.append((
                station,
                iso_timestamp,
                round(pm25, 2) if pm25 is not None else None,
                round(pm10, 2) if pm10 is not None else None,
                round(no2, 2) if no2 is not None else None,
                round(o3, 2) if o3 is not None else None,
                round(so2, 2) if so2 is not None else None,
                round(temp, 2) if temp is not None else 25.0,
                round(rh, 2) if rh is not None else 60.0,
                round(ws, 2) if ws is not None else 4.0,
                round(wd, 2) if wd is not None else 280.0,
                round(pres, 2) if pres is not None else 1010.0,
                round(rain, 2) if rain is not None else 0.0,
                20.0,
                cpcb_res.get("aqi", 50),
                cpcb_res.get("category", "Moderate"),
                source_label,
                provider_val if provider_val else "CPCB CAAQMS Station"
            ))
            parsed_count += 1

    print(f"\nCSV Parsing Summary for {os.path.basename(csv_path)}:")
    print(f"  Total rows read: {total_lines}")
    print(f"  Successfully parsed: {parsed_count}")
    print(f"  Skipped: {skipped_count}")
    for reason, cnt in skipped_reasons.items():
        if cnt > 0:
            print(f"    - {reason.replace('_', ' ').capitalize()}: {cnt}")

    if rows_to_insert:
        cur.executemany("""
            INSERT OR REPLACE INTO observations (
                location_id, recorded_at, pm25, pm10, no2, o3, so2,
                temperature, humidity, wind_speed, wind_direction, pressure,
                rainfall, cloud_cover, cpcb_aqi, category, source, provider
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, rows_to_insert)
        conn.commit()
        print(f"Successfully ingested {len(rows_to_insert)} CPCB station records (source='{source_label}').")
    else:
        print("Warning: No valid observation records could be parsed from CSV.")

    conn.close()

    # Automatically fetch matching CAMS guidance for the stored station rows
    if rows_to_insert:
        fetch_cams_for_station_records(source_labels=(source_label,))

    return len(rows_to_insert)


def ingest_openaq_v3(days: int = 60, source_label: str = "real_openaq"):
    """
    Downloads and stores real in-situ ground station observations from OpenAQ v3 API.
    """
    api_key = os.environ.get("OPENAQ_API_KEY")
    if not api_key:
        print("Warning: OPENAQ_API_KEY environment variable is required for OpenAQ ingestion.")
        return 0

    db.init_db()
    conn = db.get_connection()
    cur = conn.cursor()

    headers = {"X-API-Key": api_key, "User-Agent": "EcoBytes-DataIngestion/2.0"}
    total_inserted = 0

    print("Querying OpenAQ v3 API across Delhi-NCR monitoring stations...")
    for stn in STATIONS:
        stn_id = stn["id"]
        lat, lon = stn["lat"], stn["lon"]
        locs = fetch_openaq_v3_measurements(lat, lon, days)
        if not locs:
            print(f"  No matching OpenAQ v3 location found within 12km of {stn['name']}")
            continue

        loc_id = locs[0]["id"]
        m_url = f"https://api.openaq.org/v3/locations/{loc_id}/latest"
        try:
            req = urllib.request.Request(m_url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                m_data = json.loads(resp.read().decode("utf-8")).get("results", [])
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            print(f"  Warning: Failed fetching measurements for location {loc_id}: {e}")
            continue

        pollutants = {}
        ts = None
        for m in m_data:
            param = m.get("parameter", {}).get("name", "").lower()
            val = m.get("value")
            if val is not None and val >= 0:
                pollutants[param] = float(val)
            if not ts and m.get("datetime"):
                ts = m["datetime"].get("utc") or m["datetime"].get("local")

        if not ts:
            ts = datetime.now().isoformat()
        else:
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%Y-%m-%dT%H:00:00")
            except (ValueError, TypeError, AttributeError):
                ts = datetime.now().strftime("%Y-%m-%dT%H:00:00")

        pm25 = pollutants.get("pm25", pollutants.get("pm2.5"))
        pm10 = pollutants.get("pm10")
        no2 = pollutants.get("no2")
        o3 = pollutants.get("o3", pollutants.get("ozone"))
        so2 = pollutants.get("so2")
        co = pollutants.get("co")

        if pm25 is None and pm10 is None:
            continue

        aqi_res = cpcb.compute_cpcb_aqi({
            "pm25": pm25, "pm10": pm10, "no2": no2, "o3": o3, "so2": so2, "co": co
        })

        cur.execute("""
            INSERT OR REPLACE INTO observations (
                location_id, recorded_at, pm25, pm10, no2, o3, so2,
                temperature, humidity, wind_speed, wind_direction, pressure,
                rainfall, cloud_cover, cpcb_aqi, category, source, provider
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            stn_id, ts, pm25, pm10, no2, o3, so2,
            28.0, 60.0, 4.0, 290.0, 1010.0,
            0.0, 20.0, aqi_res.get("aqi"), aqi_res.get("category"), source_label, "OpenAQ v3 API"
        ))
        total_inserted += 1

    conn.commit()
    conn.close()
    print(f"Ingested {total_inserted} real OpenAQ station records into database.")

    # Automatically fetch matching CAMS guidance for the stored station rows
    if total_inserted > 0:
        fetch_cams_for_station_records(source_labels=(source_label,))

    return total_inserted


def fetch_cams_for_station_records(source_labels: tuple[str, ...] = ("real_openaq", "cpcb_station", "station")) -> int:
    """
    Fetches CAMS guidance for the exact coordinates and date range of stored station rows,
    ensuring each station timestamp has a matching CAMS value for residual ML modeling.
    Prints how many timestamps have both station observation and CAMS guidance.
    """
    db.init_db()
    conn = db.get_connection()
    cur = conn.cursor()

    placeholders = ",".join("?" for _ in source_labels)
    cur.execute(f"""
        SELECT o.location_id, l.latitude, l.longitude, l.name,
               MIN(o.recorded_at) as min_ts, MAX(o.recorded_at) as max_ts, COUNT(*) as cnt
        FROM observations o
        JOIN locations l ON o.location_id = l.id
        WHERE o.source IN ({placeholders})
        GROUP BY o.location_id;
    """, tuple(source_labels))
    stations = cur.fetchall()

    if not stations:
        print(f"No station records found in observations table for sources: {source_labels}")
        conn.close()
        return 0

    print(f"\nFetching CAMS guidance for {len(stations)} station(s) with stored ground observations...")
    total_cams_inserted = 0

    for stn in stations:
        stn_id = stn[0]
        lat = stn[1]
        lon = stn[2]
        stn_name = stn[3]
        min_ts = stn[4]
        max_ts = stn[5]
        cnt = stn[6]

        start_date = min_ts[:10]
        end_date = max_ts[:10]
        print(f"  Station '{stn_name}' ({stn_id}): {cnt} station records covering {start_date} to {end_date} (lat={lat}, lon={lon})")

        payload = fetch_open_meteo_cams_history(lat, lon, start_date, end_date)
        if not payload:
            print(f"    [NOTICE] Could not fetch CAMS guidance from API for {stn_id} ({start_date} to {end_date}). Check network connectivity.")
            continue

        aq_h = payload.get("air_quality", {})
        met_h = payload.get("weather", {})
        times = aq_h.get("time", [])

        if not times:
            print(f"    [NOTICE] Empty CAMS timeseries returned for {stn_id}.")
            continue

        rows_to_insert = []
        for i, t_str in enumerate(times):
            iso_ts = t_str if len(t_str) == 19 else (t_str + ":00" if len(t_str) == 16 else t_str)
            pm25 = aq_h.get("pm2_5", [None])[i] if i < len(aq_h.get("pm2_5", [])) else None
            pm10 = aq_h.get("pm10", [None])[i] if i < len(aq_h.get("pm10", [])) else None
            no2 = aq_h.get("nitrogen_dioxide", [None])[i] if i < len(aq_h.get("nitrogen_dioxide", [])) else None
            o3 = aq_h.get("ozone", [None])[i] if i < len(aq_h.get("ozone", [])) else None
            so2 = aq_h.get("sulphur_dioxide", [None])[i] if i < len(aq_h.get("sulphur_dioxide", [])) else None
            co = aq_h.get("carbon_monoxide", [None])[i] if i < len(aq_h.get("carbon_monoxide", [])) else None
            if co is not None and co > 50.0:
                co = co / 1000.0

            temp = met_h.get("temperature_2m", [25.0])[i] if i < len(met_h.get("temperature_2m", [])) else 25.0
            rh = met_h.get("relative_humidity_2m", [60.0])[i] if i < len(met_h.get("relative_humidity_2m", [])) else 60.0
            ws = met_h.get("wind_speed_10m", [4.0])[i] if i < len(met_h.get("wind_speed_10m", [])) else 4.0
            wd = met_h.get("wind_direction_10m", [280.0])[i] if i < len(met_h.get("wind_direction_10m", [])) else 280.0
            pres = met_h.get("surface_pressure", [1010.0])[i] if i < len(met_h.get("surface_pressure", [])) else 1010.0
            rain = met_h.get("rain", [0.0])[i] if i < len(met_h.get("rain", [])) else 0.0

            cpcb_res = cpcb.compute_cpcb_aqi({
                "pm25": pm25, "pm10": pm10, "no2": no2, "o3": o3, "so2": so2, "co": co
            })

            rows_to_insert.append((
                stn_id,
                iso_ts,
                round(pm25, 2) if pm25 is not None else None,
                round(pm10, 2) if pm10 is not None else None,
                round(no2, 2) if no2 is not None else None,
                round(o3, 2) if o3 is not None else None,
                round(so2, 2) if so2 is not None else None,
                round(temp, 2) if temp is not None else 25.0,
                round(rh, 2) if rh is not None else 60.0,
                round(ws, 2) if ws is not None else 4.0,
                round(wd, 2) if wd is not None else 280.0,
                round(pres, 2) if pres is not None else 1010.0,
                round(rain, 2) if rain is not None else 0.0,
                20.0,
                cpcb_res.get("aqi", 50),
                cpcb_res.get("category", "Moderate"),
                "cams_reanalysis",
                "Open-Meteo CAMS (model output)"
            ))

        if rows_to_insert:
            cur.executemany("""
                INSERT OR REPLACE INTO observations (
                    location_id, recorded_at, pm25, pm10, no2, o3, so2,
                    temperature, humidity, wind_speed, wind_direction, pressure,
                    rainfall, cloud_cover, cpcb_aqi, category, source, provider
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, rows_to_insert)
            conn.commit()
            total_cams_inserted += len(rows_to_insert)
            print(f"    Inserted {len(rows_to_insert)} CAMS guidance rows for {stn_id}.")

    # Count timestamps having BOTH station observation and CAMS guidance
    cur.execute(f"""
        SELECT s.location_id, COUNT(DISTINCT s.recorded_at)
        FROM observations s
        JOIN observations c
          ON s.location_id = c.location_id
         AND s.recorded_at = c.recorded_at
        WHERE s.source IN ({placeholders})
          AND c.source = 'cams_reanalysis'
        GROUP BY s.location_id;
    """, tuple(source_labels))
    matched_by_station = cur.fetchall()

    total_matched = sum(m[1] for m in matched_by_station)
    print("\nCAMS Guidance Alignment Summary:")
    if matched_by_station:
        for m in matched_by_station:
            print(f"  Station '{m[0]}': {m[1]} timestamps have both station observation and CAMS guidance.")
        print(f"  Total matched timestamps across all stations: {total_matched}")
    else:
        print("  0 timestamps currently have both station observation and CAMS guidance.")

    conn.close()
    return total_matched


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest atmospheric & air-quality observations.")
    parser.add_argument("--source", type=str, default="cams_reanalysis",
                        choices=["cams_reanalysis", "cpcb_csv", "openaq", "fetch_cams"],
                        help="Data source to ingest (cams_reanalysis, cpcb_csv, openaq, fetch_cams)")
    parser.add_argument("--days", type=int, default=60, help="Number of historical days to download")
    parser.add_argument("--csv", type=str, default=None, help="Path to CPCB/DPCC CSV file (required if --source cpcb_csv)")
    parser.add_argument("--station", type=str, default="delhi-anand-vihar", help="Station ID for CSV import")
    parser.add_argument("--fetch-cams", action="store_true", help="Fetch CAMS guidance for existing stored station observations")

    args = parser.parse_args()

    if args.fetch_cams or args.source == "fetch_cams":
        fetch_cams_for_station_records()
    elif args.source == "cams_reanalysis":
        ingest_cams_reanalysis(days=args.days, source_label="cams_reanalysis")
    elif args.source == "cpcb_csv":
        if not args.csv:
            print("Error: Please specify --csv <path_to_csv> when using --source cpcb_csv.")
            sys.exit(1)
        ingest_cpcb_csv(args.csv, default_station_id=args.station, source_label="cpcb_station")
    elif args.source == "openaq":
        api_key = os.environ.get("OPENAQ_API_KEY")
        if not api_key:
            print("Error: OPENAQ_API_KEY environment variable is not set.")
            sys.exit(1)
        ingest_openaq_v3(days=args.days, source_label="real_openaq")
