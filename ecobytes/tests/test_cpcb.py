"""
Test suite for official CPCB NAQI calculation formulas and statutory Table 1 compliance.
Compatible with both pytest and python -m unittest.
"""
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cpcb

OFFICIAL_BENCHMARKS = [
    ("pm25", 30.0, 50, "Good"),
    ("pm25", 60.0, 100, "Satisfactory"),
    ("pm25", 90.0, 200, "Moderate"),
    ("pm25", 120.0, 300, "Poor"),
    ("pm25", 250.0, 400, "Very Poor"),
    ("pm25", 380.0, 452, "Severe"),
    ("pm10", 50.0, 50, "Good"),
    ("pm10", 100.0, 100, "Satisfactory"),
    ("pm10", 250.0, 200, "Moderate"),
    ("pm10", 350.0, 300, "Poor"),
    ("pm10", 430.0, 400, "Very Poor"),
    ("no2", 40.0, 50, "Good"),
    ("no2", 80.0, 100, "Satisfactory"),
    ("no2", 180.0, 200, "Moderate"),
    ("no2", 280.0, 300, "Poor"),
    ("no2", 400.0, 400, "Very Poor"),
    ("o3", 50.0, 50, "Good"),
    ("o3", 100.0, 100, "Satisfactory"),
    ("o3", 168.0, 200, "Moderate"),
    ("o3", 208.0, 300, "Poor"),
    ("o3", 748.0, 400, "Very Poor"),
    ("so2", 40.0, 50, "Good"),
    ("so2", 80.0, 100, "Satisfactory"),
    ("so2", 380.0, 200, "Moderate"),
    ("so2", 800.0, 300, "Poor"),
    ("co", 1.0, 50, "Good"),
    ("co", 2.0, 100, "Satisfactory"),
    ("co", 10.0, 200, "Moderate"),
    ("co", 17.0, 300, "Poor"),
    ("co", 34.0, 400, "Very Poor"),
]


class TestCPCBStandards(unittest.TestCase):
    def test_official_table_1_breakpoints(self):
        for pollutant, conc, expected_sub_index, expected_category in OFFICIAL_BENCHMARKS:
            idx = cpcb.calculate_sub_index(pollutant, conc)
            self.assertEqual(idx, expected_sub_index, f"Failed for {pollutant}={conc}")
            cat = cpcb.get_category_info(idx)["category"]
            self.assertEqual(cat, expected_category, f"Category mismatch for {pollutant}={conc}")

    def test_dominant_pollutant_selection(self):
        # PM2.5 dominates
        res = cpcb.compute_cpcb_aqi({"pm25": 120.0, "pm10": 100.0, "no2": 40.0})
        self.assertEqual(res["aqi"], 300)
        self.assertIn(res["dominant_pollutant"], ("PM25", "PM2.5"))
        self.assertEqual(res["category"], "Poor")

        # PM10 dominates
        res2 = cpcb.compute_cpcb_aqi({"pm25": 30.0, "pm10": 250.0, "no2": 40.0})
        self.assertEqual(res2["aqi"], 200)
        self.assertIn(res2["dominant_pollutant"], ("PM10", "PM10"))
        self.assertEqual(res2["category"], "Moderate")


if __name__ == "__main__":
    unittest.main()
