"""
CPCB NAQI (National Air Quality Index) Calculator
Implements the official Indian Central Pollution Control Board standard:
Ip = [(I_HI - I_LO) / (B_HI - B_LO)] * (Cp - B_LO) + I_LO
"""

# CPCB Continuous Breakpoints (24-hr average in ug/m3, CO in mg/m3)
BREAKPOINTS = {
    "pm25": [
        (0.0, 30.0, 0, 50),        # Good
        (30.0, 60.0, 51, 100),     # Satisfactory
        (60.0, 90.0, 101, 200),    # Moderate
        (90.0, 120.0, 201, 300),   # Poor
        (120.0, 250.0, 301, 400),  # Very Poor
        (250.0, 500.0, 401, 500),  # Severe
    ],
    "pm10": [
        (0.0, 50.0, 0, 50),
        (50.0, 100.0, 51, 100),
        (100.0, 250.0, 101, 200),
        (250.0, 350.0, 201, 300),
        (350.0, 430.0, 301, 400),
        (430.0, 600.0, 401, 500),
    ],
    "no2": [
        (0.0, 40.0, 0, 50),
        (40.0, 80.0, 51, 100),
        (80.0, 180.0, 101, 200),
        (180.0, 280.0, 201, 300),
        (280.0, 400.0, 301, 400),
        (400.0, 600.0, 401, 500),
    ],
    "o3": [
        (0.0, 50.0, 0, 50),
        (50.0, 100.0, 51, 100),
        (100.0, 168.0, 101, 200),
        (168.0, 208.0, 201, 300),
        (208.0, 748.0, 301, 400),
        (748.0, 1000.0, 401, 500),
    ],
    "so2": [
        (0.0, 40.0, 0, 50),
        (40.0, 80.0, 51, 100),
        (80.0, 380.0, 101, 200),
        (380.0, 800.0, 201, 300),
        (800.0, 1600.0, 301, 400),
        (1600.0, 2000.0, 401, 500),
    ],
    "co": [
        (0.0, 1.0, 0, 50),
        (1.0, 2.0, 51, 100),
        (2.0, 10.0, 101, 200),
        (10.0, 17.0, 201, 300),
        (17.0, 34.0, 301, 400),
        (34.0, 50.0, 401, 500),
    ]
}

CATEGORIES = [
    (0, 50, "Good", "#00B050", "Minimal health impact. Air quality is considered clean and healthy."),
    (51, 100, "Satisfactory", "#92D050", "Minor breathing discomfort to sensitive people."),
    (101, 200, "Moderate", "#E5A93C", "Breathing discomfort to people with asthma, lungs, and heart diseases."),
    (201, 300, "Poor", "#FF7E00", "Breathing discomfort to most people on prolonged exposure."),
    (301, 400, "Very Poor", "#FF0000", "Respiratory illness on prolonged exposure. Significant risk to cardiac/respiratory patients."),
    (401, 500, "Severe", "#800000", "Affects healthy people and seriously impacts those with existing respiratory or cardiac diseases.")
]

import math

def _round_half_up(value: float) -> int:
    """Standard half-up arithmetic rounding matching CPCB specification and JavaScript Math.round."""
    if value is None:
        return 0
    if value < 0:
        return int(math.ceil(value - 0.5))
    return int(math.floor(value + 0.5))

def calculate_sub_index(pollutant: str, concentration: float) -> int:
    """Calculate sub-index for a single pollutant using linear piecewise interpolation."""
    if concentration is None or concentration < 0:
        return 0
    
    pollutant = pollutant.lower()
    if pollutant not in BREAKPOINTS:
        return 0
        
    table = BREAKPOINTS[pollutant]
    for b_lo, b_hi, i_lo, i_hi in table:
        if b_lo <= concentration <= b_hi:
            sub = ((i_hi - i_lo) / (b_hi - b_lo)) * (concentration - b_lo) + i_lo
            return _round_half_up(sub)
            
    # If higher than highest breakpoint, cap at 500 (Severe)
    if concentration > table[-1][1]:
        return 500
    return 0

def invert_aqi_to_concentration(pollutant: str, aqi: float) -> float:
    """Invert a CPCB sub-index back to its statutory pollutant concentration (Cp)."""
    if aqi is None or aqi <= 0:
        return 0.0
    pollutant = pollutant.lower()
    if pollutant not in BREAKPOINTS:
        return 0.0
    table = BREAKPOINTS[pollutant]
    for b_lo, b_hi, i_lo, i_hi in table:
        if i_lo <= aqi <= i_hi:
            cp = b_lo + ((aqi - i_lo) / (i_hi - i_lo)) * (b_hi - b_lo)
            return round(cp, 2)
    if aqi > table[-1][3]:
        return float(table[-1][1])
    return 0.0

def get_category_info(aqi: int):
    """Return category name, hex color code, and official CPCB advisory."""
    if aqi is None or aqi < 0:
        return {"category": "Good", "color": "#00B050", "advisory": CATEGORIES[0][4]}
    
    for lo, hi, cat, color, advisory in CATEGORIES:
        if lo <= aqi <= hi:
            return {"category": cat, "color": color, "advisory": advisory}
    return {"category": "Severe", "color": "#800000", "advisory": CATEGORIES[-1][4]}

def compute_cpcb_aqi(pollutants: dict) -> dict:
    """Computes overall CPCB AQI from a dict of pollutant concentrations."""
    sub_indices = {}
    dominant_pollutant = "PM2.5"
    max_index = -1

    for pol, val in pollutants.items():
        if val is not None:
            sub = calculate_sub_index(pol, float(val))
            sub_indices[pol] = sub
            if sub > max_index:
                max_index = sub
                dominant_pollutant = pol

    final_aqi = max_index if max_index >= 0 else 50
    cat_info = get_category_info(final_aqi)

    return {
        "aqi": final_aqi,
        "category": cat_info["category"],
        "color": cat_info["color"],
        "advisory": cat_info["advisory"],
        "dominant_pollutant": dominant_pollutant.upper(),
        "sub_indices": sub_indices
    }


def calculate_cpcb_aqi(pm25=None, pm10=None, no2=None, so2=None, co=None, o3=None) -> dict:
    """Calculate overall CPCB AQI from individual concentrations or dict."""
    if isinstance(pm25, dict):
        return compute_cpcb_aqi(pm25)
    
    pollutants = {
        "pm25": pm25,
        "pm10": pm10,
        "no2": no2,
        "so2": so2,
        "co": co,
        "o3": o3,
    }
    return compute_cpcb_aqi({k: v for k, v in pollutants.items() if v is not None})
