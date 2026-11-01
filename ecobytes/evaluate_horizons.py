"""
evaluate_horizons.py - Multi-Horizon Validation Suite for EcoBytes

Evaluates the Residual-Targeted Hybrid Physics-ML Engine across recursive lead horizons:
- t+1 hour   (Immediate Next Step)
- t+6 hours  (Intra-Day Rush Hour Radar)
- t+24 hours (1-Day Ahead Outlook)
- t+48 hours (2-Day Ahead Outlook)
- t+72 hours (3-Day Ahead Episode Trajectory)

Evaluates on:
- 'cpcb_station' / 'real_openaq' (In-situ ground station measurements)
- 'cams_reanalysis' (Copernicus Atmospheric Reanalysis)
- 'synthetic_demo' (Physics-inspired synthetic simulator)

Baselines Compared:
1. Persistence Baseline (y_t+k = y_t)
2. Daily Climatology Baseline (diurnal hour mean from training history)

Metrics Computed:
- MAE, RMSE, Bias, R2 Score
- Skill Score vs Persistence: (RMSE_pers - RMSE_ml) / RMSE_pers * 100%
- Skill Score vs Climatology: (RMSE_clim - RMSE_ml) / RMSE_clim * 100%
- Event Detection for High Pollution / Smog (AQI > 200 / PM2.5 >= 90 ug/m3):
  * Probability of Detection (POD / Recall = TP / (TP + FN))
  * False Alarm Ratio (FAR = FP / (TP + FP))
  * F1 Score (2*TP / (2*TP + FP + FN))
"""

import os
import sys
import json
import math
import sqlite3
import argparse
from datetime import datetime

# Ensure local module access
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import db
import ml_engine
import cpcb
import coupled_forecast

REPORT_PATH = os.path.join(BASE_DIR, "horizon_evaluation_report.json")


def load_evaluation_data(dataset_source: str = "station"):
    """Load chronologically ordered, deduplicated station data from SQLite ecobytes.db."""
    conn = db.get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if dataset_source in ("station", "real_station"):
        cursor.execute("""
            SELECT location_id, recorded_at, pm25, pm10, temperature, humidity, wind_speed, wind_direction, pressure, rainfall, source
            FROM observations
            WHERE source IN ('cpcb_station', 'real_openaq')
            ORDER BY location_id, recorded_at ASC;
        """)
    elif dataset_source == "all":
        cursor.execute("""
            SELECT location_id, recorded_at, pm25, pm10, temperature, humidity, wind_speed, wind_direction, pressure, rainfall, source
            FROM observations
            ORDER BY location_id, recorded_at ASC;
        """)
    else:
        cursor.execute("""
            SELECT location_id, recorded_at, pm25, pm10, temperature, humidity, wind_speed, wind_direction, pressure, rainfall, source
            FROM observations
            WHERE source = ?
            ORDER BY location_id, recorded_at ASC;
        """, (dataset_source,))

    rows = cursor.fetchall()
    conn.close()

    stations = {}
    for r in rows:
        stn = r["location_id"]
        ts = r["recorded_at"]
        if stn not in stations:
            stations[stn] = {}
        if ts not in stations[stn]:  # Deduplicate exact timestamp
            stations[stn][ts] = {
                "timestamp": ts,
                "pm25": float(r["pm25"]) if r["pm25"] is not None else 60.0,
                "pm10": float(r["pm10"]) if r["pm10"] is not None else 120.0,
                "temperature": float(r["temperature"]) if r["temperature"] is not None else 25.0,
                "humidity": float(r["humidity"]) if r["humidity"] is not None else 60.0,
                "wind_speed": float(r["wind_speed"]) if r["wind_speed"] is not None else 4.0,
                "wind_direction": float(r["wind_direction"]) if r["wind_direction"] is not None else 290.0,
                "pressure": float(r["pressure"]) if r["pressure"] is not None else 1010.0,
                "rainfall": float(r["rainfall"]) if r["rainfall"] is not None else 0.0,
                "source": r["source"]
            }

    # Convert to sorted lists
    return {stn: [stations[stn][ts] for ts in sorted(stations[stn].keys())] for stn in stations}


def compute_diurnal_climatology(records: list[dict]) -> dict[int, float]:
    """Computes hourly mean PM2.5 (0-23) for daily climatology baseline."""
    hour_buckets = {h: [] for h in range(24)}
    for r in records:
        try:
            h = datetime.fromisoformat(r["timestamp"]).hour
        except Exception:
            h = 0
        hour_buckets[h].append(r["pm25"])
    return {h: (sum(vals) / len(vals) if vals else 60.0) for h, vals in hour_buckets.items()}


def evaluate_horizon_benchmarks(dataset_source: str = "station", is_demo: bool = False):
    print("=" * 115)
    print(f"ECOBYTES MULTI-HORIZON BENCHMARK EVALUATION (Source: {dataset_source})")
    print("Hybrid Residual Engine vs Persistence vs Daily Climatology")
    print("=" * 115)

    if "fixture" in dataset_source.lower() or "not_real_data" in dataset_source.lower():
        print(f"[ERROR] Refusing to evaluate on test fixture source '{dataset_source}'.")
        sys.exit(1)

    if dataset_source in ("cams_reanalysis", "cams"):
        print(f"[ERROR] Refusing to evaluate residual model on '{dataset_source}' (target residual observed - cams is identically zero).")
        print("Please evaluate on ground station observations (source='cpcb_station', 'real_openaq', or 'station').")
        sys.exit(1)

    if dataset_source == "synthetic_demo" and not is_demo:
        print("[ERROR] Refusing to evaluate synthetic_demo without explicit is_demo=True flag.")
        sys.exit(1)

    stations_data = load_evaluation_data(dataset_source)

    if not stations_data:
        if dataset_source in ("station", "real_station", "cpcb_station", "real_openaq"):
            print(f"\n[STATION DATA NOTICE] No in-situ ground station data found (source='{dataset_source}').")
            print("   To evaluate on physical ground station data, import a CPCB CAAQMS CSV export:")
            print("   python data/ingest_real.py --source cpcb_csv --csv path/to/delhi_cpcb.csv")
            print("   Or run OpenAQ archive ingest via data/ingest_openaq_archive.py.\n")

            unrun_report = {
                "generated_at": datetime.now().isoformat(),
                "dataset": "not_evaluated_on_station_data",
                "status": "pending_station_data",
                "message": "No in-situ CAAQMS ground station records currently stored in observations table. Ingest via data/ingest_real.py.",
                "dataset_source": dataset_source,
                "horizons": {}
            }
            with open(REPORT_PATH, "w", encoding="utf-8") as f:
                json.dump(unrun_report, f, indent=2)
            print(f"Marked report as 'not_evaluated_on_station_data' in: {REPORT_PATH}")
            return unrun_report

        print(f"[ERROR] No historical observation data found in database for source='{dataset_source}'.")
        return None

    dataset_tag = "real_station" if dataset_source in ("station", "real_station", "cpcb_station", "real_openaq") else "synthetic"
    dataset_desc = "In-situ continuous ambient air quality monitoring station measurements" if dataset_tag == "real_station" else "Synthetic demonstration dataset"

    horizons = [1, 6, 24, 48, 72]
    results = {h: {
        "actuals": [], "ml_preds": [], "pers_preds": [], "clim_preds": [],
        "ml_errors": [], "pers_errors": [], "clim_errors": []
    } for h in horizons}

    EVENT_THRESHOLD = 90.0  # PM2.5 >= 90 ug/m3 corresponds to CPCB NAQI > 200 (Poor / Very Poor / Severe)

    # Evaluate across held-out segments (chronological expanding window: last 30% per station)
    for stn_id, records in stations_data.items():
        n = len(records)
        if n < 100:
            continue

        test_start = int(n * 0.70)
        climatology = compute_diurnal_climatology(records[:test_start])

        for t_idx in range(test_start, n - 72, 4):
            hist_pm25 = [r["pm25"] for r in records[t_idx - 24:t_idx]]
            hist_pm10 = [r["pm10"] for r in records[t_idx - 24:t_idx]]

            if len(hist_pm25) < 24:
                continue

            y_persistence = hist_pm25[-1]
            future_obs = [records[t_idx + step] for step in range(72)]

            sim_pm25_hist = list(hist_pm25)
            sim_pm10_hist = list(hist_pm10)
            step_predictions = []
            for step in range(72):
                w = future_obs[step]
                try:
                    dt = datetime.fromisoformat(w["timestamp"])
                    target_hour, target_dow = dt.hour, dt.weekday()
                except Exception:
                    target_hour, target_dow = step % 24, 0

                cams_pm_guidance = hist_pm25[-1]

                ml_pred = ml_engine.predict_future_pm25(
                    cams_pm25=cams_pm_guidance,
                    lag1_pm25=sim_pm25_hist[-1],
                    lag3_pm25=sim_pm25_hist[-3] if len(sim_pm25_hist) >= 3 else sim_pm25_hist[-1],
                    lag1_pm10=sim_pm10_hist[-1] if sim_pm10_hist else sim_pm25_hist[-1] * 2.15,
                    temp=w["temperature"],
                    humidity=w["humidity"],
                    wind_speed=w["wind_speed"],
                    pressure=w["pressure"],
                    hour=target_hour,
                    rain_val=w["rainfall"],
                    day_of_week=target_dow,
                    pm25_history=sim_pm25_hist[-24:],
                    pm10_history=sim_pm10_hist[-24:]
                )

                local_blend = max(0.20, 0.70 - 0.007 * step)
                hybrid_pm = (1.0 - local_blend) * cams_pm_guidance + local_blend * max(0.0, ml_pred)

                t2m = w["temperature"]
                wind_ms = w["wind_speed"] / 3.6
                pbl_height = max(120.0, min(2200.0, 450.0 + 800.0 * max(0.0, math.sin(math.pi * target_hour / 24.0))))
                inv_strength = max(0.0, (2.8 * (1.0 if target_hour <= 6 or target_hour >= 20 else 0.0) * max(0.0, 1.0 - wind_ms / 2.0)))
                inv_score = min(1.0, max(0.0, inv_strength / 4.0))

                effective_pbl, _ = coupled_forecast._effective_pbl(pbl_height, hybrid_pm, inv_score, 0.0)
                ventilation = max(0.0, wind_ms * effective_pbl)
                feedback_mult = coupled_forecast._clamp(
                    1.0 + 0.06 * inv_score - 0.03 * coupled_forecast._clamp(ventilation / 6000.0, 0.0, 1.0),
                    0.90, 1.16
                )
                rain_factor = coupled_forecast._clamp(1.0 - 0.015 * max(0.0, w["rainfall"]), 0.82, 1.0)
                coupled_predicted_pm25 = coupled_forecast._clamp(hybrid_pm * feedback_mult * rain_factor, 0.0, 600.0)

                sim_pm25_hist.append(coupled_predicted_pm25)
                sim_pm10_hist.append(coupled_predicted_pm25 * 2.15)
                step_predictions.append(coupled_predicted_pm25)

            for h in horizons:
                pred_pm25 = step_predictions[h - 1]
                actual_pm25 = future_obs[h - 1]["pm25"]
                target_h = (datetime.fromisoformat(future_obs[h - 1]["timestamp"]).hour if "timestamp" in future_obs[h - 1] else (h % 24))
                clim_pm25 = climatology.get(target_h, 60.0)
                results[h]["actuals"].append(actual_pm25)
                results[h]["ml_preds"].append(pred_pm25)
                results[h]["pers_preds"].append(y_persistence)
                results[h]["clim_preds"].append(clim_pm25)

                results[h]["ml_errors"].append(abs(pred_pm25 - actual_pm25))
                results[h]["pers_errors"].append(abs(y_persistence - actual_pm25))
                results[h]["clim_errors"].append(abs(clim_pm25 - actual_pm25))

    summary_report = {}
    print(f"\n{'Horizon':<8} | {'ML MAE':<8} | {'Pers MAE':<8} | {'Clim MAE':<8} | {'ML RMSE':<8} | {'Bias':<7} | {'R2':<6} | {'vs Pers':<9} | {'F1':<5}")
    print("-" * 115)

    for h in horizons:
        data = results[h]
        count = len(data["actuals"])
        if count == 0:
            continue

        ml_mae = sum(data["ml_errors"]) / count
        pers_mae = sum(data["pers_errors"]) / count
        clim_mae = sum(data["clim_errors"]) / count

        ml_mse = sum((p - a) ** 2 for p, a in zip(data["ml_preds"], data["actuals"])) / count
        pers_mse = sum((p - a) ** 2 for p, a in zip(data["pers_preds"], data["actuals"])) / count
        clim_mse = sum((p - a) ** 2 for p, a in zip(data["clim_preds"], data["actuals"])) / count

        ml_rmse = math.sqrt(ml_mse)
        pers_rmse = math.sqrt(pers_mse)
        clim_rmse = math.sqrt(clim_mse)

        bias = sum(p - a for p, a in zip(data["ml_preds"], data["actuals"])) / count

        mean_actual = sum(data["actuals"]) / count
        ss_tot = sum((a - mean_actual) ** 2 for a in data["actuals"]) or 1e-6
        ss_res = sum((p - a) ** 2 for p, a in zip(data["ml_preds"], data["actuals"]))
        r2 = max(0.0, 1.0 - (ss_res / ss_tot))

        skill_pers = ((pers_rmse - ml_rmse) / pers_rmse) * 100.0 if pers_rmse > 0 else 0.0
        skill_clim = ((clim_rmse - ml_rmse) / clim_rmse) * 100.0 if clim_rmse > 0 else 0.0

        tp = sum(1 for p, a in zip(data["ml_preds"], data["actuals"]) if p >= EVENT_THRESHOLD and a >= EVENT_THRESHOLD)
        fp = sum(1 for p, a in zip(data["ml_preds"], data["actuals"]) if p >= EVENT_THRESHOLD and a < EVENT_THRESHOLD)
        fn = sum(1 for p, a in zip(data["ml_preds"], data["actuals"]) if p < EVENT_THRESHOLD and a >= EVENT_THRESHOLD)
        tn = sum(1 for p, a in zip(data["ml_preds"], data["actuals"]) if p < EVENT_THRESHOLD and a < EVENT_THRESHOLD)

        pod = (tp / (tp + fn)) if (tp + fn) > 0 else 1.0
        far = (fp / (tp + fp)) if (tp + fp) > 0 else 0.0
        f1 = (2 * tp / (2 * tp + fp + fn)) if (2 * tp + fp + fn) > 0 else 1.0

        summary_report[f"t+{h}h"] = {
            "horizon_hours": h,
            "samples_evaluated": count,
            "ml_mae": round(ml_mae, 2),
            "persistence_mae": round(pers_mae, 2),
            "climatology_mae": round(clim_mae, 2),
            "ml_rmse": round(ml_rmse, 2),
            "persistence_rmse": round(pers_rmse, 2),
            "climatology_rmse": round(clim_rmse, 2),
            "bias_ug_m3": round(bias, 2),
            "r2_score": round(r2, 4),
            "skill_vs_persistence_pct": round(skill_pers, 2),
            "skill_vs_climatology_pct": round(skill_clim, 2),
            "event_detection_aqi_gt_200": {
                "threshold_pm25_ug_m3": EVENT_THRESHOLD,
                "true_positives": tp,
                "false_positives": fp,
                "false_negatives": fn,
                "true_negatives": tn,
                "pod_recall": round(pod, 3),
                "false_alarm_ratio": round(far, 3),
                "f1_score": round(f1, 3)
            }
        }

        print(f"t+{h:<2}h   | {ml_mae:<8.2f} | {pers_mae:<8.2f} | {clim_mae:<8.2f} | {ml_rmse:<8.2f} | {bias:<+7.2f} | {r2:<6.3f} | {skill_pers:>+8.1f}% | {f1:<5.2f}")

    print("-" * 115)

    final_output = {
        "generated_at": datetime.now().isoformat(),
        "dataset": dataset_tag,
        "dataset_source": dataset_source,
        "dataset_description": dataset_desc,
        "validation_strategy": "Walk-forward chronological expanding holdout (last 30% of timeline)",
        "event_detection_threshold": "PM2.5 >= 90.0 ug/m3 (CPCB NAQI > 200: Poor / Very Poor / Severe)",
        "pipeline": "EcoBytes Hybrid Physics-ML Engine",
        "horizons": summary_report
    }

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(final_output, f, indent=2)

    print(f"\nSaved multi-horizon evaluation report to: {REPORT_PATH}")
    return final_output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate multi-horizon forecast accuracy.")
    parser.add_argument("--source", type=str, default="station", help="Dataset source (station, cpcb_station, real_openaq)")
    parser.add_argument("--demo", action="store_true", help="Explicit flag required if evaluating on synthetic_demo")
    args = parser.parse_args()

    evaluate_horizon_benchmarks(dataset_source=args.source, is_demo=args.demo)
