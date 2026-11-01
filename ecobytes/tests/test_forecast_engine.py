"""
Test suite for 72-hour forecast engine output integrity and structure.
Compatible with both pytest and python -m unittest.
"""
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
import coupled_forecast


class TestForecastEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def test_forecast_structure_and_horizons(self):
        location = {"id": "delhi-cp", "name": "Connaught Place", "latitude": 28.6328, "longitude": 77.2197, "lat": 28.6328, "lon": 77.2197}
        data = coupled_forecast.build_72h_forecast(location)

        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("total_hours"), 72)
        self.assertEqual(len(data.get("forecast", [])), 72)

        # Verify required keys in first step
        step0 = data["forecast"][0]
        required_keys = [
            "forecast_pm25", "forecast_pm10", "forecast_no2", "forecast_o3",
            "forecast_so2", "forecast_co", "cpcb_screening_aqi", "dominant_pollutant",
            "inversion_strength_k_per_km", "boundary_layer_height_m"
        ]
        for k in required_keys:
            self.assertIn(k, step0, f"Missing key: {k}")
            self.assertIsNotNone(step0[k], f"Null value for: {k}")

    def test_forecast_values_never_negative(self):
        location = {"id": "delhi-igi", "name": "IGI Airport", "latitude": 28.5562, "longitude": 77.1000, "lat": 28.5562, "lon": 77.1000}
        data = coupled_forecast.build_72h_forecast(location)
        self.assertTrue(data.get("success"))

        for i, step in enumerate(data.get("forecast", [])):
            self.assertGreaterEqual(step["forecast_pm25"], 0.0, f"Negative PM2.5 at hour {i}")
            self.assertGreaterEqual(step["forecast_pm10"], 0.0, f"Negative PM10 at hour {i}")
            self.assertGreaterEqual(step["forecast_no2"], 0.0, f"Negative NO2 at hour {i}")
            self.assertGreaterEqual(step["forecast_o3"], 0.0, f"Negative O3 at hour {i}")
            self.assertGreaterEqual(step["forecast_so2"], 0.0, f"Negative SO2 at hour {i}")
            self.assertGreaterEqual(step["forecast_co"], 0.0, f"Negative CO at hour {i}")
            self.assertGreaterEqual(step["cpcb_screening_aqi"], 0, f"Negative AQI at hour {i}")
            self.assertLessEqual(step["cpcb_screening_aqi"], 500, f"AQI exceeding 500 at hour {i}")

    def test_daily_summaries_and_grap(self):
        location = {"id": "delhi-anand-vihar", "name": "Anand Vihar", "latitude": 28.6469, "longitude": 77.3160, "lat": 28.6469, "lon": 77.3160}
        data = coupled_forecast.build_72h_forecast(location)

        for day in ("day1", "day2", "day3"):
            self.assertIn(day, data)
            self.assertIn("peak_aqi", data[day])
            self.assertIn("category", data[day])
            self.assertIn("grap_stage", data[day])

    def test_ml_residual_prediction_with_cams_guidance(self):
        """Verify that predict_future_pm25 accepts cams_pm25 and returns non-negative bounded PM2.5."""
        import ml_engine
        pred = ml_engine.predict_future_pm25(
            cams_pm25=75.0,
            lag1_pm25=80.0,
            lag3_pm25=85.0,
            lag1_pm10=160.0,
            temp=26.0,
            humidity=65.0,
            wind_speed=3.0,
            pressure=1012.0,
            hour=8,
            day_of_week=2,
            pm25_history=[80.0] * 24,
            pm10_history=[160.0] * 24
        )
        self.assertIsInstance(pred, float)
        self.assertGreaterEqual(pred, 0.0)
        self.assertLessEqual(pred, 600.0)


if __name__ == "__main__":
    unittest.main()
