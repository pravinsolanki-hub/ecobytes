#!/usr/bin/env python3
"""
EcoBytes - Real-Time Weather Data Collector

Fetches current + hourly atmospheric/weather data for Anand Vihar, Delhi
from the Open-Meteo API and continuously accumulates it into CSV/JSON.

API:
    https://api.open-meteo.com/v1/forecast

No API key is normally required for non-commercial use.

Why these fields?
-----------------
The dataset is designed for EcoBytes air-quality forecasting, inversion
analysis, plume dispersion, and weather-chemistry coupling.

Collected fields include:
    temperature_2m
    relative_humidity_2m
    dew_point_2m
    apparent_temperature
    pressure_msl
    surface_pressure
    wind_speed_10m
    wind_direction_10m
    wind_gusts_10m
    precipitation
    rain
    cloud_cover
    visibility
    shortwave_radiation
    boundary_layer_height
    vapour_pressure_deficit
    weather_code

Run one fetch:
    python weather_realtime.py --once

Accumulate every 15 minutes:
    python weather_realtime.py --interval 15

Output:
    data/anand_vihar_weather_realtime.csv
    data/anand_vihar_weather_latest.json
"""

from __future__ import annotations

import argparse
import csv
import json
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
# Location
# ---------------------------------------------------------------------------

LOCATION_NAME = "Anand Vihar, Delhi"
LATITUDE = 28.6468
LONGITUDE = 77.3160

API_URL = "https://api.open-meteo.com/v1/forecast"

DEFAULT_CSV = Path(
    "data/anand_vihar_weather_realtime.csv"
)

DEFAULT_JSON = Path(
    "data/anand_vihar_weather_latest.json"
)

HEADERS = {
    "User-Agent": "EcoBytes-WeatherRealtime/1.0",
    "Accept": "application/json",
}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

CURRENT_VARIABLES = (
    "temperature_2m,"
    "relative_humidity_2m,"
    "dew_point_2m,"
    "apparent_temperature,"
    "pressure_msl,"
    "surface_pressure,"
    "wind_speed_10m,"
    "wind_direction_10m,"
    "wind_gusts_10m,"
    "precipitation,"
    "rain,"
    "cloud_cover,"
    "visibility,"
    "shortwave_radiation,"
    "vapour_pressure_deficit,"
    "weather_code"
)

HOURLY_VARIABLES = (
    "boundary_layer_height,"
    "temperature_2m,"
    "relative_humidity_2m,"
    "wind_speed_10m,"
    "wind_direction_10m,"
    "precipitation,"
    "shortwave_radiation"
)


def fetch_weather(
    session: requests.Session,
) -> dict[str, Any]:
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "timezone": "UTC",
        "wind_speed_unit": "ms",
        "temperature_unit": "celsius",
        "precipitation_unit": "mm",
        "current": CURRENT_VARIABLES,
        "hourly": HOURLY_VARIABLES,
        "forecast_hours": 3,
    }

    response = session.get(
        API_URL,
        params=params,
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, dict):
        raise RuntimeError(
            "Open-Meteo returned an unexpected response."
        )

    current = payload.get("current")

    if not isinstance(current, dict):
        raise RuntimeError(
            "Open-Meteo response does not contain current weather."
        )

    hourly = payload.get("hourly") or {}

    return {
        "current": current,
        "hourly": hourly,
        "source": "Open-Meteo",
        "location": LOCATION_NAME,
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "fetched_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
    }


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def number(
    mapping: dict[str, Any],
    key: str,
) -> float | None:
    value = mapping.get(key)

    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_current_row(
    payload: dict[str, Any],
) -> dict[str, Any]:
    current = payload["current"]
    timestamp = str(
        current.get("time")
        or payload["fetched_at_utc"]
    )

    # Match the PBL from the hourly response to the current timestamp
    # where possible.
    pbl = None

    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    pbl_values = hourly.get(
        "boundary_layer_height"
    ) or []

    try:
        idx = times.index(timestamp)
        pbl = pbl_values[idx]
    except (ValueError, IndexError, TypeError):
        pass

    row = {
        "timestamp_utc": timestamp,
        "collected_at_utc": payload[
            "fetched_at_utc"
        ],
        "location": LOCATION_NAME,
        "latitude": LATITUDE,
        "longitude": LONGITUDE,

        "temperature_2m_c": number(
            current,
            "temperature_2m",
        ),
        "relative_humidity_2m_pct": number(
            current,
            "relative_humidity_2m",
        ),
        "dew_point_2m_c": number(
            current,
            "dew_point_2m",
        ),
        "apparent_temperature_c": number(
            current,
            "apparent_temperature",
        ),
        "pressure_msl_hpa": number(
            current,
            "pressure_msl",
        ),
        "surface_pressure_hpa": number(
            current,
            "surface_pressure",
        ),
        "wind_speed_10m_m_s": number(
            current,
            "wind_speed_10m",
        ),
        "wind_direction_10m_deg": number(
            current,
            "wind_direction_10m",
        ),
        "wind_gusts_10m_m_s": number(
            current,
            "wind_gusts_10m",
        ),
        "precipitation_mm": number(
            current,
            "precipitation",
        ),
        "rain_mm": number(
            current,
            "rain",
        ),
        "cloud_cover_pct": number(
            current,
            "cloud_cover",
        ),
        "visibility_m": number(
            current,
            "visibility",
        ),
        "shortwave_radiation_w_m2": number(
            current,
            "shortwave_radiation",
        ),
        "vapour_pressure_deficit_kpa": number(
            current,
            "vapour_pressure_deficit",
        ),
        "weather_code": current.get(
            "weather_code"
        ),
        "boundary_layer_height_m": (
            float(pbl)
            if pbl is not None
            else None
        ),
    }

    return row


def extract_hourly_forecast(
    payload: dict[str, Any],
    hours: int = 3,
) -> list[dict[str, Any]]:
    hourly = payload.get("hourly") or {}

    times = hourly.get("time") or []

    rows: list[dict[str, Any]] = []

    for i, timestamp in enumerate(
        times[:hours]
    ):
        row = {
            "timestamp_utc": timestamp,
            "location": LOCATION_NAME,
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            "temperature_2m_c": (
                hourly.get("temperature_2m", [None])[i]
                if i < len(
                    hourly.get(
                        "temperature_2m",
                        [],
                    )
                )
                else None
            ),
            "relative_humidity_2m_pct": (
                hourly.get(
                    "relative_humidity_2m",
                    [None],
                )[i]
                if i < len(
                    hourly.get(
                        "relative_humidity_2m",
                        [],
                    )
                )
                else None
            ),
            "wind_speed_10m_m_s": (
                hourly.get(
                    "wind_speed_10m",
                    [None],
                )[i]
                if i < len(
                    hourly.get(
                        "wind_speed_10m",
                        [],
                    )
                )
                else None
            ),
            "wind_direction_10m_deg": (
                hourly.get(
                    "wind_direction_10m",
                    [None],
                )[i]
                if i < len(
                    hourly.get(
                        "wind_direction_10m",
                        [],
                    )
                )
                else None
            ),
            "precipitation_mm": (
                hourly.get(
                    "precipitation",
                    [None],
                )[i]
                if i < len(
                    hourly.get(
                        "precipitation",
                        [],
                    )
                )
                else None
            ),
            "shortwave_radiation_w_m2": (
                hourly.get(
                    "shortwave_radiation",
                    [None],
                )[i]
                if i < len(
                    hourly.get(
                        "shortwave_radiation",
                        [],
                    )
                )
                else None
            ),
            "boundary_layer_height_m": (
                hourly.get(
                    "boundary_layer_height",
                    [None],
                )[i]
                if i < len(
                    hourly.get(
                        "boundary_layer_height",
                        [],
                    )
                )
                else None
            ),
        }

        rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

CSV_FIELDS = [
    "timestamp_utc",
    "collected_at_utc",
    "location",
    "latitude",
    "longitude",
    "temperature_2m_c",
    "relative_humidity_2m_pct",
    "dew_point_2m_c",
    "apparent_temperature_c",
    "pressure_msl_hpa",
    "surface_pressure_hpa",
    "wind_speed_10m_m_s",
    "wind_direction_10m_deg",
    "wind_gusts_10m_m_s",
    "precipitation_mm",
    "rain_mm",
    "cloud_cover_pct",
    "visibility_m",
    "shortwave_radiation_w_m2",
    "vapour_pressure_deficit_kpa",
    "weather_code",
    "boundary_layer_height_m",
]


def ensure_csv(
    path: Path,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not path.exists() or path.stat().st_size == 0:
        with path.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=CSV_FIELDS,
            )
            writer.writeheader()


def existing_timestamps(
    path: Path,
) -> set[str]:
    if not path.exists() or path.stat().st_size == 0:
        return set()

    timestamps: set[str] = set()

    with path.open(
        "r",
        newline="",
        encoding="utf-8",
    ) as handle:
        reader = csv.DictReader(handle)

        for row in reader:
            ts = row.get(
                "timestamp_utc"
            )

            if ts:
                timestamps.add(ts)

    return timestamps


def append_current_row(
    row: dict[str, Any],
    output: Path,
    seen: set[str],
) -> bool:
    timestamp = str(
        row["timestamp_utc"]
    )

    if timestamp in seen:
        print(
            f"[SKIP] Weather timestamp already stored: {timestamp}"
        )
        return False

    ensure_csv(output)

    with output.open(
        "a",
        newline="",
        encoding="utf-8",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=CSV_FIELDS,
        )
        writer.writerow(row)

    seen.add(timestamp)

    print(
        "[SAVED]",
        timestamp,
        f"Temp={row['temperature_2m_c']}°C",
        f"RH={row['relative_humidity_2m_pct']}%",
        f"Wind={row['wind_speed_10m_m_s']} m/s",
        f"Dir={row['wind_direction_10m_deg']}°",
        f"PBL={row['boundary_layer_height_m']} m",
    )

    return True


def save_latest_json(
    payload: dict[str, Any],
    path: Path,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            payload,
            indent=2,
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

def collect_once(
    session: requests.Session,
    csv_output: Path,
    json_output: Path,
    seen: set[str],
) -> None:
    payload = fetch_weather(
        session
    )

    save_latest_json(
        payload,
        json_output,
    )

    row = extract_current_row(
        payload
    )

    append_current_row(
        row,
        csv_output,
        seen,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch and accumulate real-time Open-Meteo weather data "
            "for Anand Vihar, Delhi."
        )
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Fetch once and exit.",
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=15,
        help=(
            "Collection interval in minutes "
            "(default: 15)."
        ),
    )

    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
    )

    parser.add_argument(
        "--json",
        type=Path,
        default=DEFAULT_JSON,
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.interval < 1:
        print(
            "ERROR: --interval must be at least 1 minute."
        )
        return 2

    ensure_csv(
        args.csv
    )

    seen = existing_timestamps(
        args.csv
    )

    session = requests.Session()

    try:
        while True:
            try:
                collect_once(
                    session=session,
                    csv_output=args.csv,
                    json_output=args.json,
                    seen=seen,
                )
            except requests.RequestException as exc:
                print(
                    f"[API ERROR] {exc}"
                )
            except Exception as exc:
                print(
                    f"[COLLECTOR ERROR] {exc}"
                )

            if args.once:
                break

            print(
                f"[WAIT] Next weather fetch in "
                f"{args.interval} minute(s). Press Ctrl+C to stop."
            )

            time.sleep(
                args.interval * 60
            )

    except KeyboardInterrupt:
        print(
            "\nWeather collector stopped."
        )
    finally:
        session.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
