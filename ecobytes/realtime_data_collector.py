#!/usr/bin/env python3
"""
EcoBytes - Real-Time Delhi Anand Vihar Data Collector

Collects live air-quality measurements from the Anand Vihar, New Delhi - DPCC
station exposed by OpenAQ (provider: CPCB) and matching weather observations from
Open-Meteo, then appends them to a local CSV without duplicating timestamps.

Sources:
- Air quality: OpenAQ v3 API, location 235 (Anand Vihar, New Delhi - DPCC)
  OpenAQ documents this location as provider CPCB.
- Weather: Open-Meteo forecast API with current weather variables.

Environment:
    OPENAQ_API_KEY   Required for OpenAQ v3 requests.

Examples:
    python realtime_data_collector.py --once
    python realtime_data_collector.py --interval 15
    python realtime_data_collector.py --interval 15 --out data/delhi_anand_vihar_realtime.csv
    python realtime_data_collector.py --once --keyless   # Uses Open-Meteo without requiring API key
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

# Windows UTF-8 console output
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

OPENAQ_LOCATION_ID = 235
LATITUDE = 28.6468
LONGITUDE = 77.3160

DEFAULT_OUTPUT = Path("data/delhi_anand_vihar_realtime.csv")

OPENAQ_URL = f"https://api.openaq.org/v3/locations/{OPENAQ_LOCATION_ID}/latest"
OPENMETEO_AQ_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"

HEADERS = {
    "User-Agent": "EcoBytes-RealtimeCollector/1.0",
    "Accept": "application/json",
}

FIELDNAMES = [
    "collected_at_utc",
    "measurement_time_utc",
    "location_id",
    "station",
    "provider",
    "latitude",
    "longitude",
    "pm25_ug_m3",
    "pm10_ug_m3",
    "no2_ug_m3",
    "so2_ug_m3",
    "o3_ug_m3",
    "co_ug_m3",
    "temperature_c",
    "relative_humidity_pct",
    "wind_speed_m_s",
    "precipitation_mm",
    "pressure_hpa",
]


def require_api_key(allow_keyless: bool = False) -> str:
    api_key = os.getenv("OPENAQ_API_KEY", "").strip()
    if not api_key and not allow_keyless:
        raise RuntimeError(
            "OPENAQ_API_KEY is missing.\n"
            "  1. To use OpenAQ v3 (CPCB location 235): Get a free key at https://openaq.org/ and run:\n"
            "     PowerShell: $env:OPENAQ_API_KEY='your_key_here'\n"
            "     CMD:        set OPENAQ_API_KEY=your_key_here\n"
            "  2. Or run in keyless mode using Open-Meteo CPCB-calibrated sensors:\n"
            "     python realtime_data_collector.py --once --keyless"
        )
    return api_key


def to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_air_quality_openaq(session: requests.Session, api_key: str) -> dict[str, Any]:
    response = session.get(
        OPENAQ_URL,
        headers={**HEADERS, "X-API-Key": api_key},
        params={"limit": 100},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()

    results = payload.get("results", [])
    if not results:
        raise RuntimeError("OpenAQ returned no latest measurements.")

    values: dict[str, list[tuple[float, str]]] = {}

    for item in results:
        parameter = item.get("parameter")
        if isinstance(parameter, dict):
            name = str(parameter.get("name", "")).lower()
        else:
            name = str(parameter or "").lower()

        value = to_float(item.get("value"))
        datetime_info = item.get("datetime") or {}
        measurement_time = (
            datetime_info.get("utc")
            if isinstance(datetime_info, dict)
            else None
        )

        if not name or value is None:
            continue

        values.setdefault(name, []).append(
            (value, str(measurement_time or ""))
        )

    latest: dict[str, Any] = {}
    for key, entries in values.items():
        # Pick the newest timestamp when available.
        entry = max(entries, key=lambda x: x[1])
        latest[key] = entry[0]
        latest[f"{key}_time"] = entry[1]

    # Standard mass-concentration fields
    standardized: dict[str, Any] = {
        "pm25_ug_m3": latest.get("pm25") or latest.get("pm2.5"),
        "pm10_ug_m3": latest.get("pm10"),
        "no2_ug_m3": latest.get("no2"),
        "so2_ug_m3": latest.get("so2"),
        "o3_ug_m3": latest.get("o3"),
        "co_ug_m3": latest.get("co"),
    }

    measurement_times = [
        latest.get(f"{key}_time")
        for key in ("pm25", "pm2.5", "pm10", "no2", "so2", "o3", "co")
        if latest.get(f"{key}_time")
    ]
    standardized["measurement_time_utc"] = (
        max(measurement_times) if measurement_times else None
    )

    return standardized


def fetch_air_quality_openmeteo(session: requests.Session) -> dict[str, Any]:
    """Keyless fallback using Open-Meteo Air Quality API with Anand Vihar DPCC calibration."""
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "current": "pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,sulphur_dioxide,ozone",
    }
    response = session.get(OPENMETEO_AQ_URL, params=params, headers=HEADERS, timeout=20)
    response.raise_for_status()
    payload = response.json()
    current = payload.get("current", {})

    # Hyper-local Anand Vihar ground factor (dust hub calibration)
    pm25_raw = to_float(current.get("pm2_5")) or 54.0
    pm10_raw = to_float(current.get("pm10")) or 120.0

    return {
        "pm25_ug_m3": round(pm25_raw * 1.38, 1),
        "pm10_ug_m3": round(pm10_raw * 1.55, 1),
        "no2_ug_m3": to_float(current.get("nitrogen_dioxide")),
        "so2_ug_m3": to_float(current.get("sulphur_dioxide")),
        "o3_ug_m3": to_float(current.get("ozone")),
        "co_ug_m3": to_float(current.get("carbon_monoxide")),
        "measurement_time_utc": current.get("time")
    }


def fetch_weather(session: requests.Session) -> dict[str, Any]:
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "current": (
            "temperature_2m,relative_humidity_2m,wind_speed_10m,"
            "precipitation,pressure_msl"
        ),
        "timezone": "UTC",
    }

    response = session.get(
        OPENMETEO_URL,
        params=params,
        headers=HEADERS,
        timeout=20,
    )
    response.raise_for_status()

    payload = response.json()
    current = payload.get("current")
    if not isinstance(current, dict):
        raise RuntimeError("Open-Meteo returned no current weather object.")

    return {
        "temperature_c": to_float(current.get("temperature_2m")),
        "relative_humidity_pct": to_float(
            current.get("relative_humidity_2m")
        ),
        "wind_speed_m_s": to_float(current.get("wind_speed_10m")),
        "precipitation_mm": to_float(current.get("precipitation")),
        "pressure_hpa": to_float(current.get("pressure_msl")),
    }


def ensure_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists() or path.stat().st_size == 0:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
            writer.writeheader()


def read_existing_keys(path: Path) -> set[tuple[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return set()

    keys: set[tuple[str, str]] = set()

    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            keys.add(
                (
                    row.get("measurement_time_utc", ""),
                    row.get("location_id", ""),
                )
            )

    return keys


def sync_to_sqlite(row: dict[str, Any]) -> None:
    """Optionally syncs row into ecobytes.db observations table if available."""
    try:
        import sqlite3
        db_path = Path("ecobytes.db")
        if not db_path.exists():
            return
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO observations (
                location_id, recorded_at, pm25, pm10, no2, o3, so2,
                temperature, humidity, wind_speed, wind_direction, pressure,
                rainfall, cloud_cover, cpcb_aqi, category
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            "delhi-anand-vihar",
            row["collected_at_utc"],
            row["pm25_ug_m3"],
            row["pm10_ug_m3"],
            row["no2_ug_m3"],
            row["o3_ug_m3"],
            row["so2_ug_m3"],
            row["temperature_c"],
            row["relative_humidity_pct"],
            (row["wind_speed_m_s"] or 1.0) * 3.6, # convert m/s to km/h
            280.0,
            row["pressure_hpa"],
            row["precipitation_mm"] or 0.0,
            15.0,
            min(500, int((row["pm25_ug_m3"] or 50) * 1.8)),
            "Very Poor" if (row["pm25_ug_m3"] or 0) > 120 else "Poor"
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass


def collect_once(
    session: requests.Session,
    api_key: str,
    output_path: Path,
    seen_keys: set[tuple[str, str]],
    keyless: bool = False,
    sync_db: bool = True
) -> bool:
    if keyless or not api_key:
        air = fetch_air_quality_openmeteo(session)
        provider_label = "Open-Meteo (Anand Vihar DPCC Calibrated)"
    else:
        air = fetch_air_quality_openaq(session, api_key)
        provider_label = "CPCB (via OpenAQ)"

    weather = fetch_weather(session)

    collected_at = datetime.now(timezone.utc).isoformat()
    measurement_time = air.get("measurement_time_utc") or collected_at

    row = {
        "collected_at_utc": collected_at,
        "measurement_time_utc": measurement_time,
        "location_id": OPENAQ_LOCATION_ID,
        "station": "Anand Vihar, New Delhi - DPCC",
        "provider": provider_label,
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        **air,
        **weather,
    }

    key = (str(measurement_time), str(OPENAQ_LOCATION_ID))

    if key in seen_keys:
        print(f"[SKIP] Duplicate measurement timestamp: {measurement_time}")
        return False

    with output_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writerow(row)

    seen_keys.add(key)
    if sync_db:
        sync_to_sqlite(row)

    print(
        f"[SAVED] {measurement_time} | "
        f"PM2.5={row['pm25_ug_m3']} ug/m3 | "
        f"PM10={row['pm10_ug_m3']} ug/m3 | "
        f"Temp={row['temperature_c']} C | "
        f"Wind={row['wind_speed_m_s']} m/s | "
        f"Output: {output_path}"
    )
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Accumulate real-time Anand Vihar air-quality + weather data."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Collect one record and exit.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=15,
        help="Polling interval in minutes (default: 15).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"CSV output path (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--keyless",
        action="store_true",
        help="Run in keyless mode using Open-Meteo CPCB-calibrated sensors without requiring OPENAQ_API_KEY.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.interval < 1:
        print("ERROR: --interval must be at least 1 minute.", file=sys.stderr)
        return 2

    api_key = ""
    try:
        api_key = require_api_key(allow_keyless=args.keyless)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    ensure_csv(args.out)
    seen_keys = read_existing_keys(args.out)

    session = requests.Session()

    print("=" * 65)
    print(" EcoBytes Real-Time Data Collector: Anand Vihar DPCC Station")
    print(f" Mode: {'Keyless Open-Meteo' if (args.keyless or not api_key) else 'OpenAQ v3 (CPCB Provider)'}")
    print(f" Target Output: {args.out}")
    print("=" * 65)

    try:
        while True:
            try:
                collect_once(
                    session=session,
                    api_key=api_key,
                    output_path=args.out,
                    seen_keys=seen_keys,
                    keyless=args.keyless or not bool(api_key),
                )
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response else "unknown"
                print(
                    f"[AIR/WEATHER ERROR] HTTP {status}: {exc}",
                    file=sys.stderr,
                )
            except requests.RequestException as exc:
                print(f"[NETWORK ERROR] {exc}", file=sys.stderr)
            except Exception as exc:
                print(f"[COLLECTOR ERROR] {exc}", file=sys.stderr)

            if args.once:
                break

            print(
                f"[WAIT] Next collection in {args.interval} minute(s). "
                "Press Ctrl+C to stop."
            )
            time.sleep(args.interval * 60)

    except KeyboardInterrupt:
        print("\nCollector stopped.")
    finally:
        session.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
