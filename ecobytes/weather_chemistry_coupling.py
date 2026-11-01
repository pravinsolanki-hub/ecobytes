#!/usr/bin/env python3
"""
EcoBytes - Reduced-Order Weather–Chemistry Coupling

Purpose
-------
Build a two-way weather/chemistry coupling feature layer for EcoBytes.

This module is intentionally a REDUCED-ORDER surrogate, not a replacement for
WRF-Chem/CMAQ/CHIMERE.

Coupling directions
-------------------
1. WEATHER -> CHEMISTRY
   - temperature affects reaction/photochemistry potential
   - solar radiation proxy + temperature affect ozone formation potential
   - humidity affects aerosol growth / PM persistence
   - wind controls dilution
   - boundary-layer height controls near-surface mixing
   - precipitation removes part of particulate mass

2. CHEMISTRY -> WEATHER / MIXING
   - aerosol loading produces a bounded "aerosol_feedback_index"
   - that index reduces effective mixing height in the reduced-order model
   - updated mixing height is fed back into the next chemistry iteration

This gives the ML model explicit two-way coupling features while keeping the
system lightweight enough for a student deployment.

IMPORTANT SCIENTIFIC LIMITATION
--------------------------------
This is a physically inspired reduced-order feature model. It does NOT solve
the Navier-Stokes equations, detailed gas/aerosol chemistry, radiative
transfer, or WRF-Chem's coupled PDE system.

For SIH, describe it accurately as:
    "A reduced-order two-way weather–chemistry coupling layer that derives
     weather-driven pollutant chemistry features and aerosol-to-mixing
     feedback features."

For a true atmospheric chemistry simulation, use a validated coupled model
such as WRF-Chem/CMAQ/CHIMERE with appropriate emissions, chemistry
mechanisms, meteorological initialization, and boundary conditions.

Input CSV
---------
Required:
    timestamp_utc
    pm25_ug_m3
    pm10_ug_m3
    temperature_c
    relative_humidity_pct
    wind_speed_m_s
    precipitation_mm
    boundary_layer_height_m

Optional:
    no2_ug_m3
    o3_ug_m3
    so2_ug_m3
    co_ug_m3

Outputs
-------
    weather_chemistry_coupling.csv

Important features
------------------
    weather_to_chemistry_index
    ozone_formation_potential
    aerosol_persistence_index
    wet_scavenging_index
    dilution_index
    aerosol_feedback_index
    effective_boundary_layer_height_m
    coupled_pm25_index
    coupled_ozone_index
    coupled_stability_score
    coupling_iterations

The input values should be concentrations in µg/m³ and meteorology in SI-like
units.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from pathlib import Path
from typing import Any

# Windows console UTF-8 support
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DEFAULT_INPUT = Path("data/delhi_anand_vihar_realtime.csv")
DEFAULT_OUTPUT = Path("data/weather_chemistry_coupling.csv")


# ---------------------------------------------------------------------------
# Numeric helpers
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


def sigmoid(x: float) -> float:
    # Numerically stable enough for this bounded feature use.
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


# ---------------------------------------------------------------------------
# Reduced-order physical proxies
# ---------------------------------------------------------------------------

def daylight_proxy(timestamp_utc: str) -> float:
    """
    Solar-availability proxy from UTC hour.

    Delhi is ~UTC+5:30. This is deliberately approximate and should be
    replaced with actual shortwave radiation / solar zenith data when
    available.
    """
    try:
        hour = int(timestamp_utc[11:13])
    except (ValueError, TypeError):
        hour = 12

    local_hour = (hour + 5.5) % 24
    phase = math.pi * (local_hour - 6.0) / 12.0

    if phase <= 0.0 or phase >= math.pi:
        return 0.0

    return clip(math.sin(phase), 0.0, 1.0)


def temperature_reaction_factor(temp_c: float) -> float:
    """
    Bounded Arrhenius-like proxy.

    Higher temperatures generally increase many gas-phase reaction rates,
    but this is only a generic feature and is not a chemical mechanism.
    """
    # Reference around 20 C.
    factor = math.exp(0.045 * (temp_c - 20.0))
    return clip(factor / 3.0, 0.0, 1.5)


def humidity_aerosol_growth_factor(rh_pct: float) -> float:
    """
    Hygroscopic growth/persistence proxy.

    The response increases strongly at high RH.
    """
    rh = clip(rh_pct, 0.0, 100.0)

    # Little growth below ~50%; strong growth near saturation.
    if rh <= 50.0:
        return 0.1 * rh / 50.0

    return clip(0.1 + ((rh - 50.0) / 50.0) ** 2, 0.1, 1.0)


def wet_scavenging_index(
    precipitation_mm: float,
    humidity_pct: float,
) -> float:
    """
    Particle removal proxy from rain + moist conditions.
    """
    rain_effect = 1.0 - math.exp(-max(precipitation_mm, 0.0) / 2.0)
    humidity_effect = clip((humidity_pct - 70.0) / 30.0, 0.0, 1.0)

    return clip(
        0.8 * rain_effect + 0.2 * humidity_effect,
        0.0,
        1.0,
    )


def dilution_index(
    wind_speed_m_s: float,
    boundary_layer_height_m: float,
) -> float:
    """
    Higher values mean stronger atmospheric dilution/mixing.
    """
    wind_component = clip(wind_speed_m_s / 8.0, 0.0, 1.0)
    pbl_component = clip(boundary_layer_height_m / 1500.0, 0.0, 1.0)

    return clip(
        0.55 * wind_component + 0.45 * pbl_component,
        0.0,
        1.0,
    )


def ozone_formation_potential(
    temperature_c: float,
    no2_ug_m3: float,
    rh_pct: float,
    wind_speed_m_s: float,
    daylight: float,
) -> float:
    """
    Reduced-order O3 formation potential.

    Important:
    This is a feature index, not an O3 concentration forecast.
    """
    temp_factor = temperature_reaction_factor(temperature_c)

    nox_factor = sigmoid(
        (no2_ug_m3 - 20.0) / 25.0
    )

    moisture_penalty = clip(
        1.0 - max(rh_pct - 80.0, 0.0) / 100.0,
        0.65,
        1.0,
    )

    ventilation_penalty = clip(
        1.0 - wind_speed_m_s / 20.0,
        0.5,
        1.0,
    )

    return clip(
        daylight
        * temp_factor
        * nox_factor
        * moisture_penalty
        * ventilation_penalty,
        0.0,
        1.0,
    )


def aerosol_feedback_index(pm25_ug_m3: float, rh_pct: float) -> float:
    """
    Bounded aerosol-loading feedback proxy.

    High PM2.5 + high RH produce a stronger feedback index.
    """
    aerosol_load = sigmoid(
        (pm25_ug_m3 - 60.0) / 35.0
    )

    humidity_factor = clip(
        0.4 + rh_pct / 100.0,
        0.4,
        1.4,
    )

    return clip(
        aerosol_load * humidity_factor,
        0.0,
        1.0,
    )


def effective_boundary_layer_height(
    meteorological_pbl_m: float,
    aerosol_feedback: float,
) -> float:
    """
    Reduced-order chemistry -> meteorology feedback.

    More aerosol loading weakens effective mixing in this feature model.

    This is a proxy, NOT a radiative-transfer or turbulence closure.
    """
    reduction = 0.08 + 0.27 * aerosol_feedback

    return max(
        100.0,
        meteorological_pbl_m * (1.0 - reduction),
    )


# ---------------------------------------------------------------------------
# Two-way coupling
# ---------------------------------------------------------------------------

def coupled_row(
    row: dict[str, Any],
    iterations: int = 4,
) -> dict[str, Any]:
    timestamp = str(row.get("timestamp_utc", "") or row.get("measurement_time_utc", "") or row.get("collected_at_utc", ""))

    pm25 = safe_float(row.get("pm25_ug_m3"), 0.0) or 0.0
    pm10 = safe_float(row.get("pm10_ug_m3"), 0.0) or 0.0
    temp = safe_float(row.get("temperature_c"), 20.0) or 20.0
    rh = safe_float(row.get("relative_humidity_pct"), 60.0) or 60.0
    wind = safe_float(row.get("wind_speed_m_s"), 2.0) or 2.0
    rain = safe_float(row.get("precipitation_mm"), 0.0) or 0.0
    pbl = safe_float(row.get("boundary_layer_height_m"), 800.0) or 800.0

    no2 = safe_float(row.get("no2_ug_m3"), 20.0) or 20.0
    o3 = safe_float(row.get("o3_ug_m3"), 30.0) or 30.0

    daylight = daylight_proxy(timestamp)

    reaction_factor = temperature_reaction_factor(temp)
    humidity_factor = humidity_aerosol_growth_factor(rh)
    scavenging = wet_scavenging_index(rain, rh)

    meteorological_dilution = dilution_index(
        wind,
        pbl,
    )

    weather_to_chemistry = clip(
        (
            0.30 * reaction_factor
            + 0.25 * humidity_factor
            + 0.25 * (1.0 - meteorological_dilution)
            + 0.20 * (1.0 - scavenging)
        ),
        0.0,
        1.5,
    )

    ozone_potential = ozone_formation_potential(
        temperature_c=temp,
        no2_ug_m3=no2,
        rh_pct=rh,
        wind_speed_m_s=wind,
        daylight=daylight,
    )

    effective_pbl = pbl
    feedback_index = 0.0

    for _ in range(iterations):
        feedback_index = aerosol_feedback_index(
            pm25_ug_m3=pm25,
            rh_pct=rh,
        )

        effective_pbl = effective_boundary_layer_height(
            meteorological_pbl_m=pbl,
            aerosol_feedback=feedback_index,
        )

        updated_dilution = dilution_index(
            wind_speed_m_s=wind,
            boundary_layer_height_m=effective_pbl,
        )

        # Recompute the weather-to-chemistry part after chemistry has
        # modified the effective mixing depth.
        weather_to_chemistry = clip(
            (
                0.30 * reaction_factor
                + 0.25 * humidity_factor
                + 0.25 * (1.0 - updated_dilution)
                + 0.20 * (1.0 - scavenging)
            ),
            0.0,
            1.5,
        )

    # Reduced-order coupled concentration tendency:
    # - higher chemistry potential pushes upward
    # - stronger dilution/scavenging pushes downward
    # - current PM2.5 remains the observed state anchor
    coupled_pm25_index = clip(
        (
            0.55 * clip(pm25 / 150.0, 0.0, 1.5)
            + 0.30 * weather_to_chemistry
            + 0.15 * feedback_index
            - 0.20 * scavenging
        ),
        0.0,
        1.5,
    )

    coupled_ozone_index = clip(
        (
            0.35 * clip(o3 / 150.0, 0.0, 1.5)
            + 0.65 * ozone_potential
        ),
        0.0,
        1.5,
    )

    # Stable boundary layer score:
    # high when effective PBL is shallow and wind is weak.
    shallow_pbl = clip(
        (900.0 - effective_pbl) / 700.0,
        0.0,
        1.0,
    )

    weak_wind = clip(
        (4.0 - wind) / 4.0,
        0.0,
        1.0,
    )

    coupled_stability = clip(
        0.6 * shallow_pbl + 0.4 * weak_wind,
        0.0,
        1.0,
    )

    return {
        "timestamp_utc": timestamp,
        "pm25_ug_m3": round(pm25, 3),
        "pm10_ug_m3": round(pm10, 3),
        "no2_ug_m3": round(no2, 3),
        "o3_ug_m3": round(o3, 3),
        "temperature_c": round(temp, 3),
        "relative_humidity_pct": round(rh, 3),
        "wind_speed_m_s": round(wind, 3),
        "precipitation_mm": round(rain, 3),
        "meteorological_boundary_layer_height_m": round(pbl, 3),
        "effective_boundary_layer_height_m": round(
            effective_pbl,
            3,
        ),
        "daylight_proxy": round(daylight, 5),
        "temperature_reaction_factor": round(
            reaction_factor,
            5,
        ),
        "humidity_aerosol_growth_factor": round(
            humidity_factor,
            5,
        ),
        "wet_scavenging_index": round(
            scavenging,
            5,
        ),
        "meteorological_dilution_index": round(
            meteorological_dilution,
            5,
        ),
        "weather_to_chemistry_index": round(
            weather_to_chemistry,
            5,
        ),
        "ozone_formation_potential": round(
            ozone_potential,
            5,
        ),
        "aerosol_feedback_index": round(
            feedback_index,
            5,
        ),
        "coupled_pm25_index": round(
            coupled_pm25_index,
            5,
        ),
        "coupled_ozone_index": round(
            coupled_ozone_index,
            5,
        ),
        "coupled_stability_score": round(
            coupled_stability,
            5,
        ),
        "coupling_iterations": iterations,
    }


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as handle:
        return list(csv.DictReader(handle))


def save_csv(rows: list[dict[str, Any]], output: Path) -> None:
    if not rows:
        raise RuntimeError("No coupling rows generated.")

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fields = list(rows[0].keys())

    with output.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
        )
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def demo() -> None:
    sample = {
        "timestamp_utc": "2026-09-20T12:00",
        "pm25_ug_m3": 115.0,
        "pm10_ug_m3": 230.0,
        "no2_ug_m3": 72.0,
        "o3_ug_m3": 48.0,
        "temperature_c": 31.0,
        "relative_humidity_pct": 78.0,
        "wind_speed_m_s": 2.1,
        "precipitation_mm": 0.0,
        "boundary_layer_height_m": 520.0,
    }

    result = coupled_row(
        sample,
        iterations=4,
    )

    print("EcoBytes two-way weather–chemistry coupling demo")
    print("=" * 58)

    for key, value in result.items():
        print(f"{key}: {value}")

    print()
    print(
        "This demo is a reduced-order coupling layer, "
        "not a full WRF-Chem simulation."
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create reduced-order two-way weather–chemistry coupling "
            "features for EcoBytes."
        )
    )

    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Input CSV (default: {DEFAULT_INPUT})",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output CSV (default: {DEFAULT_OUTPUT})",
    )

    parser.add_argument(
        "--iterations",
        type=int,
        default=4,
        help="Coupling iterations per timestep (default: 4).",
    )

    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run deterministic demo.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not 1 <= args.iterations <= 12:
        print("ERROR: --iterations must be between 1 and 12.")
        return 2

    if args.demo:
        demo()
        return 0

    if not args.input.exists():
        print(f"ERROR: Input CSV not found: {args.input}")
        return 2

    rows = load_csv(args.input)
    output_rows = [
        coupled_row(
            row,
            iterations=args.iterations,
        )
        for row in rows
    ]

    save_csv(
        output_rows,
        args.output,
    )

    print(
        f"Saved {len(output_rows)} weather–chemistry coupling rows "
        f"to {args.output}"
    )
    print(
        "Coupling direction: weather → chemistry → aerosol feedback "
        "→ effective mixing → chemistry."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
