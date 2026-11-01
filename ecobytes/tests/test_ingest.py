"""
Test suite for CPCB CCR CSV parsing, provenance guards, and test-fixture rejection.
Ensures model/reanalysis data cannot masquerade as station ground truth.
"""
import unittest
import os
import sys
import tempfile
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import db
import train
import evaluate_horizons
from data.ingest_real import check_source_provenance_guard, ingest_cpcb_csv


class TestIngestAndGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        cls.fixture_path = os.path.join(BASE_DIR, "tests", "fixtures", "format_fixture_NOT_REAL_DATA.csv")

    def test_fixture_parsing_and_na_handling(self):
        """Verify the robust CCR CSV parser correctly parses valid rows and skips NA particulates."""
        self.assertTrue(os.path.exists(self.fixture_path), "Fixture file must exist")
        # Ingest using a designated test label
        inserted = ingest_cpcb_csv(
            self.fixture_path,
            default_station_id="delhi-anand-vihar",
            source_label="format_fixture_test"
        )
        # Fixture has 4 rows: 3 have valid PM2.5/PM10, 1 has NA for both (should be skipped)
        self.assertEqual(inserted, 3, "Parser should insert exactly 3 valid rows and skip row with NA particulates")

        # Clean up test rows
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM observations WHERE source = 'format_fixture_test'")
        conn.commit()
        conn.close()

    def test_guard_rejects_openmeteo_filename(self):
        """Verify that files with 'open-meteo' in filename are refused for station ground truth."""
        with self.assertRaises(ValueError) as ctx:
            check_source_provenance_guard(
                source_label="cpcb_station",
                filename="open-meteo-delhi_forecast.csv"
            )
        self.assertIn("MISLABELED DATA GUARD", str(ctx.exception))
        self.assertIn("open-meteo", str(ctx.exception).lower())

    def test_guard_rejects_cams_filename(self):
        """Verify that files with 'cams' in filename are refused for station ground truth."""
        with self.assertRaises(ValueError) as ctx:
            check_source_provenance_guard(
                source_label="real_openaq",
                filename="cams_chemistry_grid.csv"
            )
        self.assertIn("MISLABELED DATA GUARD", str(ctx.exception))
        self.assertIn("cams", str(ctx.exception).lower())

    def test_guard_rejects_reanalysis_filename(self):
        """Verify that files with 'reanalysis' in filename are refused for station ground truth."""
        with self.assertRaises(ValueError) as ctx:
            check_source_provenance_guard(
                source_label="station",
                filename="copernicus_reanalysis_hourly.csv"
            )
        self.assertIn("MISLABELED DATA GUARD", str(ctx.exception))
        self.assertIn("reanalysis", str(ctx.exception).lower())

    def test_guard_rejects_model_provider_in_csv(self):
        """Verify that CSV rows reporting model providers are refused for station ground truth."""
        with self.assertRaises(ValueError) as ctx:
            check_source_provenance_guard(
                source_label="cpcb_station",
                filename="delhi_ground_sensors.csv",
                provider="Open-Meteo CAMS (model output)"
            )
        self.assertIn("MISLABELED DATA GUARD", str(ctx.exception))
        self.assertIn("model/reanalysis output", str(ctx.exception))

    def test_guard_rejects_mislabeled_source_label(self):
        """Verify that source labels containing 'reanalysis' cannot masquerade as station data."""
        with self.assertRaises(ValueError):
            check_source_provenance_guard(
                source_label="cpcb_reanalysis"
            )

    def test_provenance_guard_accepts_openaq_archive_provider(self):
        """Verify that the provenance guard accepts genuine OpenAQ archive provider and source."""
        # Should not raise
        check_source_provenance_guard(
            source_label="real_openaq",
            filename="location-235-2023.csv.gz",
            provider="OpenAQ archive"
        )

    def test_openaq_archive_unit_conversions_and_impossible_values(self):
        """Verify OpenAQ archive unit conversions, out-of-bounds, and negative filters."""
        from data.ingest_openaq_archive import convert_unit

        # Valid PM2.5 and PM10
        val, reason = convert_unit("pm25", 82.5, "ug/m3")
        self.assertEqual(val, 82.5)
        self.assertEqual(reason, "")

        # Negative reading rejected
        val, reason = convert_unit("pm25", -4.0, "ug/m3")
        self.assertIsNone(val)
        self.assertEqual(reason, "negative_value")

        # PM2.5 > 1500 rejected
        val, reason = convert_unit("pm25", 1650.0, "ug/m3")
        self.assertIsNone(val)
        self.assertEqual(reason, "pm25_exceeds_1500")

        # NO2 ppb to ug/m3 (20 ppb * 1.88 = 37.6)
        val, reason = convert_unit("no2", 20.0, "ppb")
        self.assertAlmostEqual(val, 37.6, places=1)

        # SO2 ppb to ug/m3 (10 ppb * 2.62 = 26.2)
        val, reason = convert_unit("so2", 10.0, "ppb")
        self.assertAlmostEqual(val, 26.2, places=1)

        # O3 ppb to ug/m3 (15 ppb * 2.0 = 30.0)
        val, reason = convert_unit("o3", 15.0, "ppb")
        self.assertAlmostEqual(val, 30.0, places=1)

        # CO ppm to mg/m3 (2.0 ppm * 1.145 = 2.29 mg/m3)
        val, reason = convert_unit("co", 2.0, "ppm")
        self.assertAlmostEqual(val, 2.29, places=2)

    def test_openaq_archive_ingestion_and_pivoting(self):
        """Verify end-to-end reading, pivoting, and database storage from OpenAQ archive .csv.gz file."""
        import gzip
        import csv
        from data.ingest_openaq_archive import ingest_openaq_archive_directory

        with tempfile.TemporaryDirectory() as tmpdir:
            gz_path = os.path.join(tmpdir, "openaq_test_archive.csv.gz")
            fieldnames = ["location_id", "sensors_id", "location", "datetime", "lat", "lon", "parameter", "value", "unit"]
            rows = [
                # Hour 1 readings at Anand Vihar coordinates (28.6469, 77.3160)
                {"location_id": "235", "sensors_id": "1", "location": "Anand Vihar, Delhi", "datetime": "2023-11-01T10:00:00Z", "lat": "28.6469", "lon": "77.3160", "parameter": "pm25", "value": "110.0", "unit": "ug/m3"},
                {"location_id": "235", "sensors_id": "2", "location": "Anand Vihar, Delhi", "datetime": "2023-11-01T10:00:00Z", "lat": "28.6469", "lon": "77.3160", "parameter": "pm10", "value": "220.0", "unit": "ug/m3"},
                {"location_id": "235", "sensors_id": "3", "location": "Anand Vihar, Delhi", "datetime": "2023-11-01T10:00:00Z", "lat": "28.6469", "lon": "77.3160", "parameter": "no2", "value": "25.0", "unit": "ppb"},
                # Impossible value (should be dropped)
                {"location_id": "235", "sensors_id": "1", "location": "Anand Vihar, Delhi", "datetime": "2023-11-01T11:00:00Z", "lat": "28.6469", "lon": "77.3160", "parameter": "pm25", "value": "1900.0", "unit": "ug/m3"},
                # Hour 2 valid reading
                {"location_id": "235", "sensors_id": "2", "location": "Anand Vihar, Delhi", "datetime": "2023-11-01T11:00:00Z", "lat": "28.6469", "lon": "77.3160", "parameter": "pm10", "value": "195.0", "unit": "ug/m3"}
            ]

            with gzip.open(gz_path, "wt", encoding="utf-8", newline="") as gz_file:
                writer = csv.DictWriter(gz_file, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

            count = ingest_openaq_archive_directory(
                archive_dir=tmpdir,
                source_label="real_openaq_test",
                provider_name="OpenAQ archive",
                fetch_cams=False
            )
            self.assertEqual(count, 2, "Should create 2 hourly records (10:00 with PM2.5/PM10, 11:00 with PM10 after dropping impossible PM2.5)")

            # Verify stored provider and source
            conn = db.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT provider, source, location_id, pm25, pm10 FROM observations WHERE source = 'real_openaq_test' ORDER BY recorded_at ASC;")
            saved = cur.fetchall()
            self.assertEqual(len(saved), 2)
            self.assertEqual(saved[0][0], "OpenAQ archive")
            self.assertEqual(saved[0][1], "real_openaq_test")
            self.assertEqual(saved[0][2], "delhi-anand-vihar") # Mapped to known Anand Vihar station
            self.assertEqual(saved[0][3], 110.0)
            self.assertEqual(saved[1][3], None) # 1900 was dropped

            # Cleanup
            cur.execute("DELETE FROM observations WHERE source = 'real_openaq_test';")
            conn.commit()
            conn.close()

    def test_seed_train_eval_demo_flag_enforcement(self):
        """Verify that seed_data, train.py, and evaluate_horizons refuse demo mode without explicit --demo flag."""
        import seed_data

        # seed_data aborts if demo=False
        with self.assertRaises(SystemExit):
            seed_data.seed_database(days=1, demo=False)

        # train.py aborts on synthetic_demo without is_demo=True
        with self.assertRaises(SystemExit):
            train.run_training_pipeline("synthetic_demo", is_demo=False)

        # evaluate_horizons aborts on synthetic_demo without is_demo=True
        with self.assertRaises(SystemExit):
            evaluate_horizons.evaluate_horizon_benchmarks("synthetic_demo", is_demo=False)

    def test_train_and_eval_refuse_fixture_source(self):
        """Verify that train.py and evaluate_horizons.py refuse test fixtures for training/evaluation."""
        with self.assertRaises(SystemExit):
            train.run_training_pipeline("fixture_not_real_data")

        with self.assertRaises(SystemExit):
            evaluate_horizons.evaluate_horizon_benchmarks("fixture_not_real_data")


if __name__ == "__main__":
    unittest.main()
