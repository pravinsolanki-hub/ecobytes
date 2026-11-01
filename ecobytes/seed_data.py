"""
EcoBytes Data Seeder: Populates demonstration / synthetic training dataset
across 6 Active Monitoring Locations in Delhi NCR for prototype validation.
"""

import sqlite3
import os
import sys
import math
import random
from datetime import datetime, timedelta

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

DB_PATH = os.path.join(os.path.dirname(__file__), "ecobytes.db")

ZONES = [
    {"id": "delhi-cp", "name": "Connaught Place (Central Delhi)", "lat": 28.6328, "lon": 77.2197, "base_pm25": 72.0},
    {"id": "delhi-anand-vihar", "name": "Anand Vihar (East Delhi - Dust Hub)", "lat": 28.6469, "lon": 77.3160, "base_pm25": 96.0},
    {"id": "delhi-igi", "name": "IGI Airport T3 (South-West)", "lat": 28.5562, "lon": 77.1000, "base_pm25": 52.0},
    {"id": "delhi-punjabi-bagh", "name": "Punjabi Bagh (West Delhi)", "lat": 28.6692, "lon": 77.1264, "base_pm25": 76.0},
    {"id": "delhi-noida", "name": "Sector 62 Noida (NCR East)", "lat": 28.6280, "lon": 77.3649, "base_pm25": 74.0},
    {"id": "delhi-gurugram", "name": "Cyber Hub Gurugram (NCR South)", "lat": 28.4950, "lon": 77.0895, "base_pm25": 66.0},
]

def seed_database(days=60, demo=True):
    if not demo:
        print("[ERROR] Refusing to seed database without explicit demo=True flag.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Clear existing observations for a clean seed
    cur.execute("DELETE FROM observations;")
    cur.execute("DELETE FROM sms_alerts;")
    cur.execute("DELETE FROM subscriptions;")

    print(f"Seeding {days} days of demonstration / synthetic training dataset across 6 Active Monitoring Locations in Delhi NCR...")

    now = datetime.now()
    total_hours = days * 24
    obs_batch = []
    alerts_batch = []

    for zone in ZONES:
        base_pm25 = zone["base_pm25"]
        for h in range(total_hours):
            rec_time = now - timedelta(hours=(total_hours - h))
            hour_of_day = rec_time.hour
            day_of_week = rec_time.weekday()

            # Diurnal Weather Cycle
            temp = 28.0 + 8.0 * math.sin(2 * math.pi * (hour_of_day - 9) / 24) + random.uniform(-1.0, 1.0)
            humidity = max(25.0, min(92.0, 72.0 - 24.0 * math.sin(2 * math.pi * (hour_of_day - 9) / 24) + random.uniform(-3.0, 3.0)))
            # Wind speed lowest at night (inversion), peaks in afternoon
            wind_speed = max(1.0, 3.2 + 3.5 * math.sin(2 * math.pi * (hour_of_day - 13) / 24) + random.uniform(-0.8, 0.8))
            wind_dir = (270 + int(random.uniform(-40, 40))) % 360 # prevailing North-Westerly
            pressure = 1008.0 - (temp * 0.18) + random.uniform(-0.5, 0.5)
            rain = 0.0
            # Sporadic rain showers on day 12 and 22
            if 280 <= h <= 286 or 520 <= h <= 524:
                rain = round(random.uniform(2.0, 12.0), 1)

            # Atmospheric dispersion factor (Low wind + high humidity traps particulate matter)
            inversion_factor = (humidity / 55.0) * (2.8 / max(wind_speed, 1.2))
            traffic_spike = 22.0 if (8 <= hour_of_day <= 11 or 18 <= hour_of_day <= 21) else (5.0 if 0 <= hour_of_day <= 5 else 12.0)
            weekend_discount = 0.85 if day_of_week >= 5 else 1.0

            pm25 = max(18.0, (base_pm25 * 0.45) + (inversion_factor * 18.0) + (traffic_spike * weekend_discount) - (rain * 7.0) + random.uniform(-4.0, 4.0))
            pm10 = max(pm25 * 1.8, pm25 * 2.15 + random.uniform(5.0, 20.0))
            no2 = max(15.0, 28.0 + traffic_spike * 0.7 + random.uniform(-3.0, 3.0))
            o3 = max(8.0, 15.0 + (temp * 0.9) - (no2 * 0.15) + random.uniform(-2.0, 2.0))
            so2 = max(5.0, 12.0 + random.uniform(-2.0, 3.0))

            # Approximate CPCB NAQI
            cpcb_aqi = int(pm25 * 1.8) if pm25 < 60 else (int(pm25 * 1.6 + 20) if pm25 < 120 else int(pm25 * 1.1 + 80))
            cpcb_aqi = min(500, max(25, cpcb_aqi))

            if cpcb_aqi <= 50:
                cat = "Good"
            elif cpcb_aqi <= 100:
                cat = "Satisfactory"
            elif cpcb_aqi <= 200:
                cat = "Moderate"
            elif cpcb_aqi <= 300:
                cat = "Poor"
            elif cpcb_aqi <= 400:
                cat = "Very Poor"
            else:
                cat = "Severe"

            obs_batch.append((
                zone["id"],
                rec_time.isoformat(),
                round(pm25, 1),
                round(pm10, 1),
                round(no2, 1),
                round(o3, 1),
                round(so2, 1),
                round(temp, 1),
                round(humidity, 1),
                round(wind_speed, 1),
                round(wind_dir, 1),
                round(pressure, 1),
                rain,
                20.0,
                cpcb_aqi,
                cat,
                "synthetic_demo",
                "Synthetic Prototype Generator"
            ))

            # Add sample SMS alert if threshold breached during last 48 hours
            if h >= (total_hours - 48) and (h % 14 == 0) and (pm25 > 50 or pm10 > 120):
                rule = []
                if pm25 > 50: rule.append(f"PM2.5 breached ({pm25:.1f} ug/m3 > 50 limit)")
                if pm10 > 120: rule.append(f"PM10 breached ({pm10:.1f} ug/m3 > 120 limit)")
                rule_str = " & ".join(rule)
                sms_msg = f"EcoBytes CPCB Alert [{zone['name']}]: Air quality alert! {rule_str}. AQI: {cpcb_aqi} ({cat}). Limit outdoor activity."
                alerts_batch.append((
                    zone["id"],
                    rec_time.isoformat(),
                    rule_str,
                    round(pm25, 1),
                    round(pm10, 1),
                    sms_msg,
                    "Citizen Broadcast",
                    "SENT"
                ))

    # Insert batch observations
    cur.executemany("""
    INSERT INTO observations (
        location_id, recorded_at, pm25, pm10, no2, o3, so2,
        temperature, humidity, wind_speed, wind_direction, pressure,
        rainfall, cloud_cover, cpcb_aqi, category, source, provider
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, obs_batch)

    # Insert batch alerts
    cur.executemany("""
    INSERT INTO sms_alerts (
        location_id, triggered_at, rule_triggered, pm25_val, pm10_val, message, recipient_phone, status
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
    """, alerts_batch)

    # Subscriptions start clean for citizen user input
    subscribers = []
    if subscribers:
        cur.executemany("INSERT INTO subscriptions VALUES (?, ?, ?);", subscribers)

    conn.commit()
    conn.close()

    print(f"Database seeded with {len(obs_batch)} demonstration / synthetic records across 6 Active Monitoring Locations.")
    print(f"Generated {len(alerts_batch)} historical demonstration SMS alerts.")

if __name__ == "__main__":
    seed_database(demo=True)
