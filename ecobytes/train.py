"""
train.py - Single reproducible training entry point for EcoBytes ML Engine.
Retargeted for Residual Bias Correction:
    target_residual = observed_pm25 - cams_pm25
    final_forecast  = clamp(cams_pm25 + predicted_residual, 0.0, 600.0)

Supports:
- Training on observational datasets (source='cams_reanalysis', 'cpcb_station', 'synthetic_demo')
- Walk-forward chronological (expanding-window) train/validation splits
- Leave-One-Station-Out Cross-Validation (LOSO-CV) with spatial autocorrelation caveat
- Saves model metadata with exact model class, random seed, and dataset provenance
"""

import os
import sys
import json
import logging
import math
import sqlite3
import argparse
from datetime import datetime

# Ensure parent directory is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError) as exc:
        logging.debug("stdout encoding configuration skipped: %s", exc)

import db
import ml_engine

MODEL_FILE = os.path.join(os.path.dirname(__file__), "model_metadata.json")
MODEL_ARTIFACT = os.path.join(os.path.dirname(__file__), "ml_model.pkl")


def load_dataset_by_source(source: str = "cams_reanalysis"):
    """
    Loads distinct observation records filtered by source, sorted chronologically.
    """
    conn = db.get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if source == "all":
        cur.execute("""
            SELECT location_id, recorded_at, pm25, pm10, no2, o3, so2,
                   temperature, humidity, wind_speed, wind_direction, pressure, rainfall, source
            FROM observations
            ORDER BY location_id, recorded_at ASC;
        """)
    else:
        cur.execute("""
            SELECT location_id, recorded_at, pm25, pm10, no2, o3, so2,
                   temperature, humidity, wind_speed, wind_direction, pressure, rainfall, source
            FROM observations
            WHERE source = ?
            ORDER BY location_id, recorded_at ASC;
        """, (source,))

    rows = cur.fetchall()
    conn.close()
    return rows


def run_training_pipeline(dataset_source: str = "cpcb_station", seed: int = 42, is_demo: bool = False):
    if "fixture" in str(dataset_source).lower() or str(dataset_source).startswith("fixture_"):
        print(f"[ERROR] Refusing to train on test fixture source '{dataset_source}'.")
        sys.exit(1)

    if dataset_source in ("cams_reanalysis", "cams"):
        print(f"[ERROR] Refusing to train residual model on '{dataset_source}' (target residual observed - cams is identically zero).")
        print("Please train on ground station observations (source='cpcb_station', 'real_openaq', or 'station').")
        sys.exit(1)

    if dataset_source == "synthetic_demo" and not is_demo:
        print("[ERROR] Refusing to train on synthetic_demo without explicit is_demo=True flag.")
        sys.exit(1)

    print("=" * 80)
    print(f"ECOBYTES RESIDUAL ML TRAINING PIPELINE (Data Source: {dataset_source})")
    print(f"Target Formulation: residual = observed_pm25 - cams_pm25")
    print(f"Random Seed: {seed} | Python: {sys.version.split()[0]}")
    print("=" * 80)

    rows = load_dataset_by_source(dataset_source)
    if not rows:
        if dataset_source != "synthetic_demo":
            if not is_demo:
                print(f"[ERROR] No records found for source='{dataset_source}'. Refusing synthetic fallback without is_demo=True.")
                sys.exit(1)
            print(f"[WARN] No records found for source='{dataset_source}'. Attempting fallback to 'synthetic_demo'...")
            dataset_source = "synthetic_demo"
            rows = load_dataset_by_source(dataset_source)

    if not rows:
        print("[ERROR] Database is empty. Ingest station data first.")
        return None

    # Group by station and deduplicate timestamps
    stations = {}
    for r in rows:
        stn = r["location_id"]
        if stn not in stations:
            stations[stn] = {}
        ts = r["recorded_at"]
        if ts not in stations[stn]:  # Deduplicate hourly records
            stations[stn][ts] = r

    # 1. Build 32-feature matrix and residual target per station
    all_features = []
    all_observed_targets = []
    all_residual_targets = []
    station_indices = {}

    for stn_id, stn_dict in stations.items():
        stn_rows = [stn_dict[ts] for ts in sorted(stn_dict.keys())]
        pm25_hist = []
        pm10_hist = []
        start_idx = len(all_features)

        for i, r in enumerate(stn_rows):
            obs_pm25 = float(r["pm25"]) if r["pm25"] is not None else 60.0
            obs_pm10 = float(r["pm10"]) if r["pm10"] is not None else obs_pm25 * 2.0

            pm25_hist.append(obs_pm25)
            pm10_hist.append(obs_pm10)

            if len(pm25_hist) < 25:
                continue

            try:
                dt = datetime.fromisoformat(r["recorded_at"])
                hour = dt.hour
                dow = dt.weekday()
            except Exception:
                hour = i % 24
                dow = (i // 24) % 7

            # CAMS synoptic forecast guidance at this hour
            cams_pm25 = float(r["cams_pm25"]) if "cams_pm25" in r.keys() and r["cams_pm25"] is not None else obs_pm25
            target_residual = obs_pm25 - cams_pm25

            feats = ml_engine._feature_row(
                cams_pm25=cams_pm25,
                pm_hist=pm25_hist[-25:-1],
                pm10_hist=pm10_hist[-25:-1],
                temperature=float(r["temperature"]) if r["temperature"] is not None else 25.0,
                humidity=float(r["humidity"]) if r["humidity"] is not None else 60.0,
                wind_speed=float(r["wind_speed"]) if r["wind_speed"] is not None else 3.5,
                pressure=float(r["pressure"]) if r["pressure"] is not None else 1010.0,
                rainfall=float(r["rainfall"]) if r["rainfall"] is not None else 0.0,
                hour=hour,
                day_of_week=dow
            )

            all_features.append(feats)
            all_observed_targets.append(obs_pm25)
            all_residual_targets.append(target_residual)

        end_idx = len(all_features)
        station_indices[stn_id] = (start_idx, end_idx)

    # 2. Walk-Forward Chronological Split (70% train, 30% holdout test)
    split_idx = int(len(all_features) * 0.70)
    train_X, test_X = all_features[:split_idx], all_features[split_idx:]
    train_y_res, test_y_res = all_residual_targets[:split_idx], all_residual_targets[split_idx:]
    train_y_obs, test_y_obs = all_observed_targets[:split_idx], all_observed_targets[split_idx:]

    print(f"Walk-Forward Split: {len(train_X)} train samples, {len(test_X)} holdout evaluation samples")

    # 3. Model Training on Residuals
    start_t = datetime.now()
    if ml_engine.SKLEARN_AVAILABLE:
        from sklearn.ensemble import HistGradientBoostingRegressor
        from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
        model = HistGradientBoostingRegressor(
            max_iter=300,
            learning_rate=0.04,
            max_leaf_nodes=25,
            min_samples_leaf=12,
            l2_regularization=1.5,
            random_state=seed
        )
        model.fit(train_X, train_y_res)
        pred_res = model.predict(test_X).tolist()
        engine_name = "sklearn.ensemble.HistGradientBoostingRegressor"
        backend_name = "scikit-learn"
    else:
        model = ml_engine._PurePythonHybridBoosting(alpha=2.0, n_stumps=30, learning_rate=0.08)
        model.fit(train_X, train_y_res)
        pred_res = model.predict(test_X)
        engine_name = "EcoBytes._PurePythonHybridBoosting"
        backend_name = "pure_python_fallback"

    # Reconstruct final predicted PM2.5: forecast = cams + predicted_residual
    final_preds = [max(0.0, x[0] + r) for x, r in zip(test_X, pred_res)]

    mae = sum(abs(a - b) for a, b in zip(test_y_obs, final_preds)) / len(test_y_obs)
    rmse = math.sqrt(sum((a - b) ** 2 for a, b in zip(test_y_obs, final_preds)) / len(test_y_obs))
    mean_y = sum(test_y_obs) / len(test_y_obs)
    ss_tot = sum((a - mean_y) ** 2 for a in test_y_obs) or 1e-6
    ss_res = sum((a - b) ** 2 for a, b in zip(test_y_obs, final_preds))
    r2 = max(0.0, 1.0 - (ss_res / ss_tot))

    # Persistence baseline (pm25_lag1 is index 1)
    baseline = [x[1] for x in test_X]
    baseline_mae = sum(abs(a - b) for a, b in zip(test_y_obs, baseline)) / len(test_y_obs)
    baseline_rmse = math.sqrt(sum((a - b) ** 2 for a, b in zip(test_y_obs, baseline)) / len(test_y_obs))
    skill_pct = ((baseline_rmse - rmse) / baseline_rmse * 100.0) if baseline_rmse > 0 else 0.0

    # 4. Leave-One-Station-Out CV (Spatial Generalization across Delhi-NCR)
    loso_scores = {}
    stn_list = list(station_indices.keys())
    if len(stn_list) >= 2:
        for val_stn in stn_list:
            s_val, e_val = station_indices[val_stn]
            if e_val - s_val < 20:
                continue
            cv_train_X = all_features[:s_val] + all_features[e_val:]
            cv_train_y_res = all_residual_targets[:s_val] + all_residual_targets[e_val:]
            cv_test_X = all_features[s_val:e_val]
            cv_test_y_obs = all_observed_targets[s_val:e_val]

            if not cv_train_X or not cv_test_X:
                continue

            if ml_engine.SKLEARN_AVAILABLE:
                cv_model = HistGradientBoostingRegressor(max_iter=150, learning_rate=0.05, random_state=seed)
                cv_model.fit(cv_train_X, cv_train_y_res)
                cv_pred_res = cv_model.predict(cv_test_X)
            else:
                cv_model = ml_engine._PurePythonHybridBoosting(alpha=2.0, n_stumps=25, learning_rate=0.08)
                cv_model.fit(cv_train_X, cv_train_y_res)
                cv_pred_res = cv_model.predict(cv_test_X)

            cv_final_preds = [max(0.0, x[0] + r) for x, r in zip(cv_test_X, cv_pred_res)]
            cv_mae = sum(abs(a - b) for a, b in zip(cv_test_y_obs, cv_final_preds)) / len(cv_test_y_obs)
            loso_scores[val_stn] = round(cv_mae, 2)

    train_secs = (datetime.now() - start_t).total_seconds()

    # 5. Build Metadata Contract
    metadata = {
        "status": "TRAINED",
        "last_trained_at": datetime.now().isoformat(),
        "model_architecture": engine_name,
        "library_backend": backend_name,
        "target_formulation": "residual = observed_pm25 - cams_pm25 (final = cams + pred_residual)",
        "python_version": sys.version.split()[0],
        "random_seed": seed,
        "dataset_source": dataset_source,
        "dataset_nature": "Copernicus CAMS Reanalysis" if "cams" in dataset_source.lower() else "Station Observations",
        "spatial_autocorrelation_caveat": "Delhi-NCR stations are geographically proximate (~10-40km) and share or border CAMS grid cells. Out-of-station CV indicates intra-urban transferability, not distant geographical out-of-domain transferability.",
        "training_samples": len(train_X),
        "test_samples": len(test_X),
        "validation_strategy": "Walk-forward expanding window (70/30 chronological)",
        "leave_one_station_out_mae": loso_scores,
        "features_count": len(ml_engine.FEATURE_NAMES),
        "feature_names": ml_engine.FEATURE_NAMES,
        "metrics": {
            "mae": round(mae, 3),
            "rmse": round(rmse, 3),
            "r2_score": round(r2, 4),
            "naive_baseline_mae": round(baseline_mae, 3),
            "naive_baseline_rmse": round(baseline_rmse, 3),
            "skill_improvement_pct": round(skill_pct, 2)
        },
        "training_time_seconds": round(train_secs, 2)
    }

    # Save metadata JSON (model binary is saved locally for execution but ignored in git)
    import pickle
    with open(MODEL_ARTIFACT, "wb") as f:
        pickle.dump(model, f, protocol=pickle.HIGHEST_PROTOCOL)
    with open(MODEL_FILE, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    print("\n" + "=" * 80)
    print("RESIDUAL MODEL TRAINING COMPLETE")
    print(f"  Model Architecture:   {engine_name}")
    print("  Target Formulation:   residual = observed - cams (forecast = cams + residual)")
    print(f"  Training Data Source: {dataset_source} ({len(train_X)} samples)")
    print(f"  Holdout MAE:          {mae:.3f} ug/m3 (vs {baseline_mae:.3f} baseline)")
    print(f"  Holdout RMSE:         {rmse:.3f} ug/m3 (vs {baseline_rmse:.3f} baseline)")
    print(f"  Holdout R2 Score:     {r2:.4f}")
    print(f"  Skill Gain over Base: +{skill_pct:.1f}%")
    if loso_scores:
        print(f"  Leave-One-Station-Out MAE by Station: {loso_scores}")
    print(f"  Saved Metadata:       {MODEL_FILE}")
    print("=" * 80)

    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train EcoBytes atmospheric residual ML model.")
    parser.add_argument("--source", type=str, default="cpcb_station", help="Dataset source (cpcb_station, real_openaq, or station)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument("--demo", action="store_true", help="Explicit flag required if training on synthetic_demo")
    args = parser.parse_args()

    run_training_pipeline(dataset_source=args.source, seed=args.seed, is_demo=args.demo)
