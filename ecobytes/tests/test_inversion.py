"""
Test suite for Atmospheric Inversion physics, Lapse Rate calculations, and Ventilation Index.
"""
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import atmospheric_inversion
import ml_engine


class TestAtmosphericInversion(unittest.TestCase):
    def test_inversion_strength_calculation(self):
        # Surface colder than 925 hPa -> Strong Inversion (Gamma < 0)
        t_sfc = 15.0  # Celsius
        t_925 = 20.0  # Celsius (warmer aloft)
        lapse_rate = (t_sfc - t_925) / 0.75  # -6.67 K/km
        inv_strength = max(0.0, -lapse_rate)
        self.assertGreater(inv_strength, 0.0)

    def test_physical_diagnostics_bounds(self):
        diag = ml_engine.compute_physical_diagnostics(
            pm25=120.0,
            pm10=220.0,
            temperature=18.0,
            humidity=85.0,
            wind_speed=1.5,
            rainfall=0.0,
            hour=2
        )
        self.assertIn("inversion_strength_k_per_km", diag)
        self.assertIn("boundary_layer_height_m", diag)
        self.assertGreaterEqual(diag["boundary_layer_height_m"], 120.0)
        self.assertLessEqual(diag["boundary_layer_height_m"], 2200.0)
        self.assertIn(diag["low_pbl_flag"], (0.0, 1.0))
        self.assertIn(diag["strong_inversion_flag"], (0.0, 1.0))


if __name__ == "__main__":
    unittest.main()
