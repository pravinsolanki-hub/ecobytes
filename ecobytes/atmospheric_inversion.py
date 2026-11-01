#!/usr/bin/env python3
"""
EcoBytes - Atmospheric Inversion Feature Generator

Computes model-based atmospheric-inversion features for Delhi NCR.

Why this is a proxy:
- A true atmospheric inversion is diagnosed from a vertical temperature
  profile/sounding.
- This module uses Open-Meteo model pressure-level temperatures and
  geopotential heights plus near-surface temperature and boundary-layer
  height to create ML-friendly inversion indicators.

Location:
    Anand Vihar, New Delhi
    Latitude: 28.6468
    Longitude: 77.3160

Outputs:
    inversion_features.csv

Main features:
    - theta_2m
    - theta_925hPa
    - inversion_strength_k_per_km
    - inversion_temperature_difference_c
    - boundary_layer_height_m
    - low_pbl_flag
    - strong_inversion_flag
    - stable_boundary_layer_score

Use:
    python atmospheric_inversion.py
    python atmospheric_inversion.py --hours 72
    python atmospheric_inversion.py --output data/inversion_features.csv

The resulting CSV can be merged with the EcoBytes PM2.5/weather dataset
using the timestamp column.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
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

LATITUDE = 28.6468
LONGITUDE = 77.3160

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

OUTPUT_DEFAULT = Path("data/inversion_features.csv")

PRESSURE_LEVELS = (
    "925hPa",
    "850hPa",
)

HEADERS = {
    "User-Agent": "EcoBytes-Atmospheric-Inversion/1.0",
    "Accept": "application/json",
}

# Dry-air potential temperature exponent kappa = R_d / c_p.
KAPPA = 0.2854

FIELDNAMES = [
    "timestamp_utc",
    "latitude",
    "longitude",
    "temperature_2m_c",
    "temperature_925hPa_c",
    "temperature_850hPa_c",
    "height_925hPa_m",
    "height_850hPa_m",
    "boundary_layer_height_m",
    "theta_2m_k",
    "theta_925hPa_k",
    "theta_850hPa_k",
    "inversion_temperature_difference_c",
    "inversion_strength_k_per_km",
    "lower_tropospheric_stability_k_per_km",
    "low_pbl_flag",
    "strong_inversion_flag",
    "stable_boundary_layer_score",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate atmospheric-inversion features for EcoBytes."
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=72,
        help="Number of forecast hours to keep (default: 72).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_DEFAULT,
        help=f"CSV output path (default: {OUTPUT_DEFAULT}).",
    )
    return parser.parse_args()


def safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def potential_temperature(temp_c: float, pressure_hpa: float) -> float:
    """
    Dry-air potential temperature in Kelvin:

        theta = T(K) * (1000 / p)^kappa
    """
    return (temp_c + 273.15) * ((1000.0 / pressure_hpa) ** KAPPA)


def fetch_forecast(hours: int) -> dict[str, Any]:
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "forecast_days": 4,
        "timezone": "UTC",
        "hourly": ",".join(
            [
                "temperature_2m",
                "boundary_layer_height",
                "temperature_925hPa",
                "temperature_850hPa",
                "geopotential_height_925hPa",
                "geopotential_height_850hPa",
            ]
        ),
    }

    response = requests.get(
        OPEN_METEO_URL,
        params=params,
        headers=HEADERS,
        timeout=20,
    )
    response.raise_for_status()

    payload = response.json()
    hourly = payload.get("hourly")

    if not isinstance(hourly, dict) or "time" not in hourly:
        raise RuntimeError("Open-Meteo response did not contain hourly data.")

    return hourly


def calculate_features(hourly: dict[str, Any], hours: int) -> list[dict[str, Any]]:
    times = hourly["time"]

    arrays = {
        key: hourly.get(key, [])
        for key in (
            "temperature_2m",
            "boundary_layer_height",
            "temperature_925hPa",
            "temperature_850hPa",
            "geopotential_height_925hPa",
            "geopotential_height_850hPa",
        )
    }

    rows: list[dict[str, Any]] = []

    for i, timestamp in enumerate(times[:hours]):
        t2m = safe_float(arrays["temperature_2m"][i])
        t925 = safe_float(arrays["temperature_925hPa"][i])
        t850 = safe_float(arrays["temperature_850hPa"][i])
        z925 = safe_float(arrays["geopotential_height_925hPa"][i])
        z850 = safe_float(arrays["geopotential_height_850hPa"][i])
        pbl = safe_float(arrays["boundary_layer_height"][i])

        values = (t2m, t925, t850, z925, z850, pbl)
        if any(v is None for v in values):
            continue

        theta2m = potential_temperature(t2m, 1000.0)
        theta925 = potential_temperature(t925, 925.0)
        theta850 = potential_temperature(t850, 850.0)

        # Pressure-level temperatures are above the surface. The sign of
        # (T925 - T2m) is a useful inversion proxy:
        # positive => warmer air aloft than at the surface.
        temp_difference = t925 - t2m

        # Use the actual model geopotential height rather than approximate
        # pressure-level altitudes.
        layer_height_km = max((z925 - 0.0) / 1000.0, 0.1)

        inversion_strength = temp_difference / layer_height_km

        # Lower-tropospheric potential-temperature stability between 925 and
        # 850 hPa. Positive values mean potential temperature increases upward.
        delta_z_km = max((z850 - z925) / 1000.0, 0.1)
        lower_trop_stability = (theta850 - theta925) / delta_z_km

        # Practical ML features. Thresholds are deliberately simple proxies,
        # not a formal radiosonde inversion classification.
        low_pbl = 1 if pbl < 500 else 0
        strong_inv = 1 if inversion_strength >= 2.0 else 0

        pbl_score = max(0.0, min(1.0, (800.0 - pbl) / 600.0))
        inversion_score = max(
            0.0,
            min(1.0, inversion_strength / 4.0),
        )

        stable_score = 0.6 * inversion_score + 0.4 * pbl_score

        rows.append(
            {
                "timestamp_utc": timestamp,
                "latitude": LATITUDE,
                "longitude": LONGITUDE,
                "temperature_2m_c": round(t2m, 3),
                "temperature_925hPa_c": round(t925, 3),
                "temperature_850hPa_c": round(t850, 3),
                "height_925hPa_m": round(z925, 2),
                "height_850hPa_m": round(z850, 2),
                "boundary_layer_height_m": round(pbl, 2),
                "theta_2m_k": round(theta2m, 3),
                "theta_925hPa_k": round(theta925, 3),
                "theta_850hPa_k": round(theta850, 3),
                "inversion_temperature_difference_c": round(
                    temp_difference,
                    3,
                ),
                "inversion_strength_k_per_km": round(
                    inversion_strength,
                    3,
                ),
                "lower_tropospheric_stability_k_per_km": round(
                    lower_trop_stability,
                    3,
                ),
                "low_pbl_flag": low_pbl,
                "strong_inversion_flag": strong_inv,
                "stable_boundary_layer_score": round(
                    stable_score,
                    3,
                ),
            }
        )

    return rows


def save_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()

    if not 1 <= args.hours <= 96:
        print("ERROR: --hours must be between 1 and 96.", file=sys.stderr)
        return 2

    try:
        hourly = fetch_forecast(args.hours)
        rows = calculate_features(hourly, args.hours)
        save_csv(rows, args.output)
    except requests.RequestException as exc:
        print(f"ERROR: Open-Meteo request failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Saved {len(rows)} inversion-feature rows to: {args.output}")

    if rows:
        latest = rows[0]
        print(
            "Sample:",
            f"inversion_strength={latest['inversion_strength_k_per_km']} K/km,",
            f"PBL={latest['boundary_layer_height_m']} m,",
            f"strong_inversion_flag={latest['strong_inversion_flag']}",
        )

    print(
        "Note: These are model-based inversion/stability proxies, "
        "not direct radiosonde observations."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
