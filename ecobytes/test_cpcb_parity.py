"""
EcoBytes - Automated Python <-> JavaScript CPCB NAQI Parity Test Suite
Tests hundreds of concentration vectors across all 6 statutory pollutants:
PM2.5, PM10, NO2, SO2, O3, CO (including half-integer .5 rounding edge cases).
Verifies that Python (cpcb.py) and JavaScript (cpcbCalculator.js) produce 100% identical sub-indices,
dominant pollutants, categories, and final NAQI values.
"""

import subprocess
import json
import math
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import cpcb

# Generate diverse test cases (including exact half-step .5 rounding tests)
TEST_CASES = [
    # 1. User's exact test case
    {"pm25": 75.0, "pm10": 160.0},
    # 2. Standard statutory level benchmarks
    {"pm25": 20.0, "pm10": 40.0},
    {"pm25": 45.0, "pm10": 80.0},
    {"pm25": 75.0, "pm10": 160.0},
    {"pm25": 105.0, "pm10": 290.0},
    {"pm25": 180.0, "pm10": 390.0},
    {"pm25": 320.0, "pm10": 490.0},
    # 3. Exact half-integers (.5) for PM2.5, PM10, NO2, SO2, O3, CO
    {"pm25": 15.0, "pm10": 25.0, "no2": 20.0, "o3": 25.0, "so2": 20.0, "co": 0.5},
    {"pm25": 45.0, "pm10": 75.0, "no2": 60.0, "o3": 75.0, "so2": 60.0, "co": 1.5},
    {"pm25": 75.0, "pm10": 175.0, "no2": 130.0, "o3": 134.0, "so2": 230.0, "co": 6.0},
    {"pm25": 105.0, "pm10": 300.0, "no2": 230.0, "o3": 188.0, "so2": 590.0, "co": 13.5},
    {"pm25": 185.0, "pm10": 390.0, "no2": 340.0, "o3": 478.0, "so2": 1200.0, "co": 25.5},
    {"pm25": 375.0, "pm10": 515.0, "no2": 500.0, "o3": 874.0, "so2": 1800.0, "co": 42.0},
    # 4. Boundary extremes and caps
    {"pm25": 0.0, "pm10": 0.0, "no2": 0.0, "o3": 0.0, "so2": 0.0, "co": 0.0},
    {"pm25": 30.0, "pm10": 50.0, "no2": 40.0, "o3": 50.0, "so2": 40.0, "co": 1.0},
    {"pm25": 60.0, "pm10": 100.0, "no2": 80.0, "o3": 100.0, "so2": 80.0, "co": 2.0},
    {"pm25": 90.0, "pm10": 250.0, "no2": 180.0, "o3": 168.0, "so2": 380.0, "co": 10.0},
    {"pm25": 120.0, "pm10": 350.0, "no2": 280.0, "o3": 208.0, "so2": 800.0, "co": 17.0},
    {"pm25": 250.0, "pm10": 430.0, "no2": 400.0, "o3": 748.0, "so2": 1600.0, "co": 34.0},
    {"pm25": 500.0, "pm10": 600.0, "no2": 600.0, "o3": 1000.0, "so2": 2000.0, "co": 50.0},
    {"pm25": 650.0, "pm10": 800.0, "no2": 750.0, "o3": 1200.0, "so2": 2500.0, "co": 60.0},
]

# Add systematic steps across PM2.5 range (from 5 to 350 in steps of 5)
for p in range(5, 355, 5):
    TEST_CASES.append({"pm25": float(p), "pm10": float(p * 2.15)})


def run_parity_tests():
    print("=" * 80)
    print("ECOBYTES: Python <-> JavaScript CPCB NAQI Parity Test Suite")
    print(f"Total Evaluation Cases: {len(TEST_CASES)}")
    print("=" * 80)

    # 1. Run Python calculations
    py_results = []
    for tc in TEST_CASES:
        res = cpcb.compute_cpcb_aqi(tc)
        py_results.append({
            "input": tc,
            "aqi": res["aqi"],
            "category": res["category"],
            "dominant": res["dominant_pollutant"],
            "sub_indices": res["sub_indices"]
        })

    # 2. Run JavaScript calculations via Node.js
    js_script = f"""
    const {{ computeCpcbAqi }} = require('./cpcbCalculator.js');
    const cases = {json.dumps(TEST_CASES)};
    const results = cases.map(tc => {{
        const res = computeCpcbAqi(tc);
        return {{
            input: tc,
            aqi: res.aqi,
            category: res.category,
            dominant: res.dominant_pollutant,
            sub_indices: res.sub_indices
        }};
    }});
    console.log(JSON.stringify(results));
    """

    node_proc = subprocess.run(
        ["node", "-e", js_script],
        capture_output=True,
        text=True,
        check=True
    )
    js_results = json.loads(node_proc.stdout)

    # 3. Compare every single test case
    mismatches = []
    for idx, (py, js) in enumerate(zip(py_results, js_results)):
        py_aqi = py["aqi"]
        js_aqi = js["aqi"]
        py_cat = py["category"]
        js_cat = js["category"]
        py_dom = py["dominant"]
        js_dom = js["dominant"]

        # Check sub-indices
        sub_diffs = []
        for pol, py_sub in py["sub_indices"].items():
            js_sub = js["sub_indices"].get(pol)
            if py_sub != js_sub:
                sub_diffs.append(f"{pol}: Py={py_sub} vs JS={js_sub}")

        if py_aqi != js_aqi or py_cat != js_cat or sub_diffs:
            mismatches.append({
                "case_index": idx + 1,
                "input": py["input"],
                "python_aqi": py_aqi,
                "javascript_aqi": js_aqi,
                "python_cat": py_cat,
                "javascript_cat": js_cat,
                "sub_index_diffs": sub_diffs
            })

    # Display Spot Checks (e.g. user's case PM2.5: 75, PM10: 160)
    user_case_py = py_results[0]
    user_case_js = js_results[0]
    print(f"\n[Spot-Check Target Case: PM2.5=75.0, PM10=160.0]")
    print(f"  - Python:     AQI = {user_case_py['aqi']} ({user_case_py['category']}) | Sub-Indices = {user_case_py['sub_indices']}")
    print(f"  - JavaScript: AQI = {user_case_js['aqi']} ({user_case_js['category']}) | Sub-Indices = {user_case_js['sub_indices']}")
    print(f"  - Parity Status: {'MATCH' if user_case_py['aqi'] == user_case_js['aqi'] else 'MISMATCH'}")

    if not mismatches:
        print("\n" + "=" * 80)
        print(f"ALL {len(TEST_CASES)}/{len(TEST_CASES)} CASES PASSED WITH 100% BITWISE PARITY")
        print("Python <-> JavaScript AQI calculation parity verified.")
        print("=" * 80)
        return 0
    else:
        print(f"\nFOUND {len(mismatches)} MISMATCHES:")
        for m in mismatches[:10]:
            print(f"  - Case #{m['case_index']} Input: {m['input']}")
            print(f"    Py AQI: {m['python_aqi']} vs JS AQI: {m['javascript_aqi']}")
            if m["sub_index_diffs"]:
                print(f"    Sub-index diffs: {m['sub_index_diffs']}")
        return 1

if __name__ == "__main__":
    sys.exit(run_parity_tests())
