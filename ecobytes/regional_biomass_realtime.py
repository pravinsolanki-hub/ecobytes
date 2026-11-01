#!/usr/bin/env python3
"""
EcoBytes - Real-Time Regional Biomass / Fire Impact Collector

Fetches REAL-TIME / near-real-time data via APIs:

1) NASA FIRMS Area API
   - VIIRS NOAA-21 Near Real-Time fire detections
   - requires FIRMS_MAP_KEY (or use --demo for zero-key demonstration)

2) Open-Meteo API
   - Anand Vihar / Delhi current wind speed and direction
   - hourly boundary-layer height
   - no API key required for normal non-commercial use

The script:
    FIRMS fire hotspots
          +
    live Delhi wind / mixing conditions
          ↓
    source-receptor geometry
          ↓
    regional fire-impact features
          ↓
    JSON + CSV files

IMPORTANT
---------
A satellite fire hotspot is NOT automatically a stubble-burning event.
This script reports "regional fire / biomass-burning candidate impact" and
does not claim measured stubble-burning PM2.5 contribution.

This is a screening / ML feature system, NOT a replacement for WRF-Chem,
CMAQ, HYSPLIT, or a validated chemical-transport model.

Run once:
    python regional_biomass_realtime.py --once

Keep accumulating every 15 minutes:
    python regional_biomass_realtime.py --interval 15

Run offline/demo mode without NASA API Key:
    python regional_biomass_realtime.py --once --demo

Windows CMD:
    set FIRMS_MAP_KEY=YOUR_KEY
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

# Windows console UTF-8 support
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RECEPTOR_NAME = "Anand Vihar, Delhi"
RECEPTOR_LAT = 28.6468
RECEPTOR_LON = 77.3160

# Broad Delhi-NCR upwind / regional source domain (Punjab, Haryana, W-UP, Rajasthan).
WEST = 73.5
SOUTH = 27.0
EAST = 80.0
NORTH = 32.0

FIRMS_SOURCE = "VIIRS_NOAA21_NRT"
FIRMS_DAYS = 1

FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

DEFAULT_JSON = Path("data/regional_biomass_realtime.json")
DEFAULT_CSV = Path("data/regional_biomass_realtime.csv")

HEADERS = {
    "User-Agent": "EcoBytes-RegionalBiomassRealtime/1.0",
    "Accept": "application/json,text/csv,*/*",
}

EARTH_RADIUS_KM = 6371.008

# Representative regional fire hotspots across Punjab & Haryana for --demo evaluation
DEMO_FIRMS_HOTSPOTS = [
    {"latitude": "30.2812", "longitude": "75.8456", "frp": "48.5", "satellite": "NOAA-21", "confidence": "nominal", "acq_date": "2026-09-20", "acq_time": "1130"},  # Sangrur, Punjab
    {"latitude": "30.9010", "longitude": "75.8573", "frp": "62.0", "satellite": "NOAA-21", "confidence": "high", "acq_date": "2026-09-20", "acq_time": "1130"},     # Ludhiana, Punjab
    {"latitude": "29.9695", "longitude": "76.8783", "frp": "28.4", "satellite": "NOAA-21", "confidence": "nominal", "acq_date": "2026-09-20", "acq_time": "1130"},  # Kurukshetra, Haryana
    {"latitude": "29.6857", "longitude": "76.9905", "frp": "34.1", "satellite": "NOAA-21", "confidence": "nominal", "acq_date": "2026-09-20", "acq_time": "1130"},  # Karnal, Haryana
    {"latitude": "30.2110", "longitude": "74.9455", "frp": "55.2", "satellite": "NOAA-21", "confidence": "high", "acq_date": "2026-09-20", "acq_time": "1130"},     # Bathinda, Punjab
    {"latitude": "28.8955", "longitude": "76.6066", "frp": "18.6", "satellite": "NOAA-21", "confidence": "nominal", "acq_date": "2026-09-20", "acq_time": "1130"},  # Rohtak, Haryana
]


# ---------------------------------------------------------------------------
# Math / geometry
# ---------------------------------------------------------------------------

def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize_angle(angle: float) -> float:
    return angle % 360.0


def angular_difference(a: float, b: float) -> float:
    d = abs(normalize_angle(a) - normalize_angle(b))
    return min(d, 360.0 - d)


def haversine_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(p1)
        * math.cos(p2)
        * math.sin(dlambda / 2.0) ** 2
    )

    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def initial_bearing_deg(
    source_lat: float,
    source_lon: float,
    receptor_lat: float,
    receptor_lon: float,
) -> float:
    lat1 = math.radians(source_lat)
    lat2 = math.radians(receptor_lat)
    dlon = math.radians(receptor_lon - source_lon)

    x = math.sin(dlon) * math.cos(lat2)
    y = (
        math.cos(lat1) * math.sin(lat2)
        - math.sin(lat1)
        * math.cos(lat2)
        * math.cos(dlon)
    )

    return normalize_angle(math.degrees(math.atan2(x, y)))


def iso_hour_from_firms(
    date_str: str,
    time_str: str,
) -> str:
    """
    FIRMS acq_time is HHMM UTC. Round to nearest hour.
    """
    cleaned = str(time_str).zfill(4)
    hh = int(cleaned[:2])
    mm = int(cleaned[2:])

    from datetime import datetime, timedelta

    try:
        dt = datetime.strptime(
            f"{date_str} {hh:02d}{mm:02d}",
            "%Y-%m-%d %H%M",
        ).replace(tzinfo=timezone.utc)
    except Exception:
        dt = datetime.now(timezone.utc)

    if mm >= 30:
        dt = dt.replace(minute=0, second=0, microsecond=0)
        dt += timedelta(hours=1)
    else:
        dt = dt.replace(minute=0, second=0, microsecond=0)

    return dt.strftime("%Y-%m-%dT%H:00:00Z")


# ---------------------------------------------------------------------------
# API fetchers
# ---------------------------------------------------------------------------

def get_firms_map_key() -> str:
    key = os.getenv("FIRMS_MAP_KEY", "").strip()

    if not key:
        raise RuntimeError(
            "FIRMS_MAP_KEY is missing. Get a free NASA FIRMS MAP_KEY "
            "and set it as an environment variable (or run with --demo for simulated testing)."
        )

    return key


def fetch_firms(
    session: requests.Session,
    map_key: str,
) -> list[dict[str, str]]:
    area = f"{WEST},{SOUTH},{EAST},{NORTH}"

    url = (
        f"{FIRMS_URL}/{map_key}/{FIRMS_SOURCE}/"
        f"{area}/{FIRMS_DAYS}"
    )

    response = session.get(
        url,
        headers=HEADERS,
        timeout=45,
    )
    response.raise_for_status()

    text = response.text.strip()

    if not text:
        return []

    if text.lower().startswith(("invalid", "error")):
        raise RuntimeError(
            f"NASA FIRMS API returned an error: {text[:500]}"
        )

    reader = csv.DictReader(text.splitlines())

    return list(reader)


def fetch_weather(
    session: requests.Session,
) -> dict[str, Any]:
    """
    Gets current wind at the receptor and the latest hourly PBL height.
    """
    params = {
        "latitude": RECEPTOR_LAT,
        "longitude": RECEPTOR_LON,
        "timezone": "UTC",
        "current": (
            "temperature_2m,"
            "relative_humidity_2m,"
            "wind_speed_10m,"
            "wind_direction_10m,"
            "precipitation,"
            "pressure_msl"
        ),
        "hourly": "boundary_layer_height",
        "forecast_days": 2,
    }

    response = session.get(
        OPEN_METEO_URL,
        params=params,
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    payload = response.json()

    current = payload.get("current") or {}
    hourly = payload.get("hourly") or {}

    current_time = str(current.get("time", ""))

    pbl = None
    pbl_times = hourly.get("time") or []
    pbl_values = hourly.get("boundary_layer_height") or []

    if current_time and pbl_times:
        try:
            idx = pbl_times.index(current_time)
            pbl = safe_float(pbl_values[idx])
        except (ValueError, IndexError):
            pass

    return {
        "timestamp_utc": current_time,
        "temperature_c": safe_float(
            current.get("temperature_2m")
        ),
        "relative_humidity_pct": safe_float(
            current.get("relative_humidity_2m")
        ),
        "wind_speed_m_s": safe_float(
            current.get("wind_speed_10m")
        ),
        "wind_direction_deg": safe_float(
            current.get("wind_direction_10m")
        ),
        "precipitation_mm": safe_float(
            current.get("precipitation")
        ),
        "pressure_hpa": safe_float(
            current.get("pressure_msl")
        ),
        "boundary_layer_height_m": pbl,
    }


# ---------------------------------------------------------------------------
# Impact model
# ---------------------------------------------------------------------------

def compute_fire_impact(
    fire: dict[str, Any],
    weather: dict[str, Any],
) -> dict[str, Any] | None:
    lat = safe_float(fire.get("latitude"))
    lon = safe_float(fire.get("longitude"))

    if lat is None or lon is None:
        return None

    frp = max(
        safe_float(fire.get("frp"), 0.0) or 0.0,
        0.0,
    )

    wind_speed = max(
        safe_float(
            weather.get("wind_speed_m_s"),
            1.0,
        )
        or 1.0,
        0.5,
    )

    wind_from = normalize_angle(
        safe_float(
            weather.get("wind_direction_deg"),
            0.0,
        )
        or 0.0
    )

    pbl = safe_float(
        weather.get("boundary_layer_height_m")
    )

    distance = haversine_km(
        lat,
        lon,
        RECEPTOR_LAT,
        RECEPTOR_LON,
    )

    bearing = initial_bearing_deg(
        lat,
        lon,
        RECEPTOR_LAT,
        RECEPTOR_LON,
    )

    # Meteorological wind direction is the direction FROM which wind comes.
    # The plume travels toward wind_from + 180°.
    wind_to = normalize_angle(
        wind_from + 180.0
    )

    angle_error = angular_difference(
        bearing,
        wind_to,
    )

    angle_rad = math.radians(angle_error)

    downwind_distance = distance * math.cos(angle_rad)
    crosswind_distance = abs(
        distance * math.sin(angle_rad)
    )

    upwind = int(downwind_distance > 0)

    # 1 = directly downwind; 0 = perpendicular/opposed.
    alignment = clip(
        math.cos(angle_rad),
        0.0,
        1.0,
    )

    # Regional-distance attenuation (e^-d/100).
    distance_decay = math.exp(
        -distance / 100.0
    )

    # Log-compressed FRP feature.
    frp_factor = clip(
        math.log1p(frp) / math.log1p(500.0),
        0.0,
        1.0,
    )

    # Slow wind + low PBL -> higher near-surface accumulation potential.
    wind_factor = clip(
        4.0 / wind_speed,
        0.0,
        1.0,
    )

    pbl_factor = (
        clip(
            700.0 / max(pbl, 150.0),
            0.0,
            1.0,
        )
        if pbl is not None
        else 0.5
    )

    # Conservative screening score.
    impact_score = clip(
        alignment
        * distance_decay
        * frp_factor
        * wind_factor
        * pbl_factor
        * upwind,
        0.0,
        1.0,
    )

    timestamp = iso_hour_from_firms(
        str(fire.get("acq_date", "")),
        str(fire.get("acq_time", "")),
    )

    return {
        "timestamp_utc": timestamp,
        "source": "NASA FIRMS",
        "sensor": fire.get(
            "satellite",
            "NOAA-21",
        ),
        "source_latitude": round(
            lat,
            5,
        ),
        "source_longitude": round(
            lon,
            5,
        ),
        "frp_mw": round(
            frp,
            3,
        ),
        "confidence": fire.get(
            "confidence",
            "nominal",
        ),
        "acq_date": fire.get(
            "acq_date",
            "",
        ),
        "acq_time": fire.get(
            "acq_time",
            "",
        ),
        "source_distance_km": round(
            distance,
            3,
        ),
        "source_to_receptor_bearing_deg": round(
            bearing,
            2,
        ),
        "wind_from_deg": round(
            wind_from,
            2,
        ),
        "wind_speed_m_s": round(
            wind_speed,
            3,
        ),
        "boundary_layer_height_m": (
            round(pbl, 2)
            if pbl is not None
            else None
        ),
        "downwind_distance_km": round(
            max(downwind_distance, 0.0),
            3,
        ),
        "crosswind_distance_km": round(
            crosswind_distance,
            3,
        ),
        "source_upwind_flag": upwind,
        "directional_alignment_score": round(
            alignment,
            6,
        ),
        "distance_decay": round(
            distance_decay,
            6,
        ),
        "frp_factor": round(
            frp_factor,
            6,
        ),
        "wind_factor": round(
            wind_factor,
            6,
        ),
        "pbl_factor": round(
            pbl_factor,
            6,
        ),
        "regional_fire_impact_score": round(
            impact_score,
            6,
        ),
        "receptor": RECEPTOR_NAME,
    }


def aggregate(
    features: list[dict[str, Any]],
    weather: dict[str, Any],
) -> dict[str, Any]:
    upwind = [
        row for row in features
        if int(row["source_upwind_flag"]) == 1
    ]

    total_frp = sum(
        safe_float(row["frp_mw"], 0.0) or 0.0
        for row in features
    )

    upwind_frp = sum(
        safe_float(row["frp_mw"], 0.0) or 0.0
        for row in upwind
    )

    scores = [
        safe_float(
            row["regional_fire_impact_score"],
            0.0,
        )
        or 0.0
        for row in features
    ]

    weighted_score = 0.0
    if total_frp > 0:
        weighted_score = sum(
            (
                safe_float(
                    row["regional_fire_impact_score"],
                    0.0,
                )
                or 0.0
            )
            * (
                safe_float(
                    row["frp_mw"],
                    0.0,
                )
                or 0.0
            )
            for row in features
        ) / total_frp

    return {
        "collected_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "receptor": RECEPTOR_NAME,
        "receptor_lat": RECEPTOR_LAT,
        "receptor_lon": RECEPTOR_LON,
        "fire_source": FIRMS_SOURCE,
        "active_fire_count": len(features),
        "upwind_fire_count": len(upwind),
        "total_frp_mw": round(
            total_frp,
            3,
        ),
        "upwind_frp_mw": round(
            upwind_frp,
            3,
        ),
        "weighted_regional_fire_impact": round(
            weighted_score,
            6,
        ),
        "max_fire_impact_score": round(
            max(scores) if scores else 0.0,
            6,
        ),
        "weather": weather,
        "fires": features,
        "attribution_note": (
            "FIRMS active-fire detections are not automatically "
            "classified as stubble burning."
        ),
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def append_csv(
    summary: dict[str, Any],
    output: Path,
) -> None:
    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    row = {
        "collected_at_utc": summary["collected_at_utc"],
        "receptor": summary["receptor"],
        "active_fire_count": summary["active_fire_count"],
        "upwind_fire_count": summary["upwind_fire_count"],
        "total_frp_mw": summary["total_frp_mw"],
        "upwind_frp_mw": summary["upwind_frp_mw"],
        "weighted_regional_fire_impact": (
            summary["weighted_regional_fire_impact"]
        ),
        "max_fire_impact_score": (
            summary["max_fire_impact_score"]
        ),
        "wind_speed_m_s": summary["weather"].get(
            "wind_speed_m_s"
        ),
        "wind_direction_deg": summary["weather"].get(
            "wind_direction_deg"
        ),
        "boundary_layer_height_m": summary["weather"].get(
            "boundary_layer_height_m"
        ),
    }

    fields = list(row.keys())

    new_file = (
        not output.exists()
        or output.stat().st_size == 0
    )

    with output.open(
        "a",
        newline="",
        encoding="utf-8",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
        )

        if new_file:
            writer.writeheader()

        writer.writerow(row)


def save_json(
    summary: dict[str, Any],
    output: Path,
) -> None:
    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output.write_text(
        json.dumps(
            summary,
            indent=2,
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

def collect_once(
    session: requests.Session,
    map_key: str,
    json_output: Path,
    csv_output: Path,
    is_demo: bool = False,
) -> None:
    weather = fetch_weather(session)

    if is_demo or map_key == "DEMO_KEY":
        fires = DEMO_FIRMS_HOTSPOTS
    else:
        fires = fetch_firms(
            session,
            map_key,
        )

    features: list[dict[str, Any]] = []

    for fire in fires:
        row = compute_fire_impact(
            fire,
            weather,
        )

        if row is not None:
            features.append(row)

    summary = aggregate(
        features,
        weather,
    )

    save_json(
        summary,
        json_output,
    )

    append_csv(
        summary,
        csv_output,
    )

    print(
        f"[{summary['collected_at_utc']}] "
        f"fires={summary['active_fire_count']} | "
        f"upwind={summary['upwind_fire_count']} | "
        f"FRP={summary['upwind_frp_mw']} MW | "
        f"impact={summary['weighted_regional_fire_impact']:.4f}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch live/near-real-time NASA FIRMS fire data and "
            "Open-Meteo weather, then estimate regional fire impact."
        )
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Fetch one snapshot and exit.",
    )

    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run in zero-key demonstration mode using representative northern Indian fire hotspots.",
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=15,
        help="Collection interval in minutes (default: 15).",
    )

    parser.add_argument(
        "--json",
        type=Path,
        default=DEFAULT_JSON,
        help=f"Latest JSON output (default: {DEFAULT_JSON}).",
    )

    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
        help=f"Accumulating CSV output (default: {DEFAULT_CSV}).",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.interval < 1:
        print("ERROR: --interval must be at least 1 minute.")
        return 2

    is_demo = args.demo
    if is_demo:
        map_key = "DEMO_KEY"
        print("ℹ️ Running in --demo demonstration mode with representative Punjab/Haryana fire hotspots.")
    else:
        try:
            map_key = get_firms_map_key()
        except RuntimeError as exc:
            print(f"ERROR: {exc}")
            print("Tip: Run with `python regional_biomass_realtime.py --once --demo` to test without an API key.")
            return 2

    session = requests.Session()

    try:
        while True:
            try:
                collect_once(
                    session=session,
                    map_key=map_key,
                    json_output=args.json,
                    csv_output=args.csv,
                    is_demo=is_demo,
                )
            except requests.RequestException as exc:
                print(f"[API ERROR] {exc}")
            except Exception as exc:
                print(f"[COLLECTOR ERROR] {exc}")

            if args.once:
                break

            print(
                f"[WAIT] Next API fetch in {args.interval} minute(s). "
                "Press Ctrl+C to stop."
            )

            time.sleep(
                args.interval * 60
            )

    except KeyboardInterrupt:
        print("\nCollector stopped.")
    finally:
        session.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
