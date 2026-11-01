"""
Database layer for EcoBytes (SQLite local instance + Schema setup)
"""
import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "ecobytes.db")

DEFAULT_LOCATIONS = [
    {"id": "delhi-cp", "name": "Connaught Place (Central Delhi)", "lat": 28.6328, "lon": 77.2197, "zone": "Central"},
    {"id": "delhi-anand-vihar", "name": "Anand Vihar (East Delhi - High Dust)", "lat": 28.6469, "lon": 77.3160, "zone": "East"},
    {"id": "delhi-igi", "name": "IGI Airport T3 (South-West Delhi)", "lat": 28.5562, "lon": 77.1000, "zone": "South-West"},
    {"id": "delhi-punjabi-bagh", "name": "Punjabi Bagh (West Delhi)", "lat": 28.6692, "lon": 77.1264, "zone": "West"},
    {"id": "delhi-noida", "name": "Sector 62 Noida (NCR East)", "lat": 28.6280, "lon": 77.3649, "zone": "NCR East"},
    {"id": "delhi-gurugram", "name": "Cyber Hub Gurugram (NCR South)", "lat": 28.4950, "lon": 77.0895, "zone": "NCR South"},
]

def get_connection():
    """Open a database connection with SQLite foreign-key enforcement enabled."""
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def location_exists(cur, location_id):
    """Return True when the requested location exists."""
    if not location_id:
        return False
    cur.execute("SELECT 1 FROM locations WHERE id = ? LIMIT 1;", (location_id,))
    return cur.fetchone() is not None


def init_db():
    conn = get_connection()
    try:
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS locations (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            zone TEXT NOT NULL
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location_id TEXT,
            recorded_at TEXT NOT NULL,
            pm25 REAL,
            pm10 REAL,
            no2 REAL,
            o3 REAL,
            so2 REAL,
            co REAL,
            temperature REAL,
            humidity REAL,
            wind_speed REAL,
            wind_direction REAL,
            pressure REAL,
            rainfall REAL,
            cloud_cover REAL,
            cpcb_aqi INTEGER,
            category TEXT,
            source TEXT DEFAULT 'synthetic_demo',
            provider TEXT DEFAULT 'CPCB / CAAQMS',
            FOREIGN KEY (location_id) REFERENCES locations(id)
        );
        """)

        # Migration guard: ensure source, provider, and co columns exist in observations
        cur.execute("PRAGMA table_info(observations);")
        obs_cols = [row[1] for row in cur.fetchall()]
        if "source" not in obs_cols:
            cur.execute("ALTER TABLE observations ADD COLUMN source TEXT DEFAULT 'synthetic_demo';")
        if "provider" not in obs_cols:
            cur.execute("ALTER TABLE observations ADD COLUMN provider TEXT DEFAULT 'CPCB / CAAQMS';")
        if "co" not in obs_cols:
            cur.execute("ALTER TABLE observations ADD COLUMN co REAL;")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS sms_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location_id TEXT,
            triggered_at TEXT NOT NULL,
            rule_triggered TEXT NOT NULL,
            pm25_val REAL,
            pm10_val REAL,
            message TEXT NOT NULL,
            recipient_phone TEXT,
            status TEXT DEFAULT 'SENT',
            FOREIGN KEY (location_id) REFERENCES locations(id)
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            phone TEXT PRIMARY KEY,
            location_id TEXT,
            subscribed_at TEXT NOT NULL,
            FOREIGN KEY (location_id) REFERENCES locations(id)
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS gateway_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)

        # Performance indexes for faster time-series and alert lookups
        cur.execute("CREATE INDEX IF NOT EXISTS idx_observations_loc_time ON observations(location_id, recorded_at);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sms_alerts_loc ON sms_alerts(location_id);")

        # Seed default locations if empty
        cur.execute("SELECT COUNT(*) FROM locations;")
        if cur.fetchone()[0] == 0:
            for loc in DEFAULT_LOCATIONS:
                cur.execute("INSERT INTO locations VALUES (?, ?, ?, ?, ?);",
                            (loc["id"], loc["name"], loc["lat"], loc["lon"], loc["zone"]))
            conn.commit()
    finally:
        conn.close()

def get_locations():
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM locations;")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

def log_observation(data):
    conn = get_connection()
    try:
        cur = conn.cursor()
        location_id = data.get("location_id")
        if location_id is not None and not location_exists(cur, location_id):
            raise ValueError(f"Unknown location_id: {location_id}")

        cur.execute("""
        INSERT INTO observations (
            location_id, recorded_at, pm25, pm10, no2, o3, so2, co,
            temperature, humidity, wind_speed, wind_direction, pressure,
            rainfall, cloud_cover, cpcb_aqi, category, source, provider
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            location_id,
            data.get("recorded_at"),
            data.get("pm25"),
            data.get("pm10"),
            data.get("no2"),
            data.get("o3"),
            data.get("so2"),
            data.get("co"),
            data.get("temperature"),
            data.get("humidity"),
            data.get("wind_speed"),
            data.get("wind_direction"),
            data.get("pressure"),
            data.get("rainfall"),
            data.get("cloud_cover"),
            data.get("cpcb_aqi"),
            data.get("category"),
            data.get("source", "synthetic_demo"),
            data.get("provider", "CPCB / CAAQMS")
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()

def log_alert(location_id, rule_triggered, pm25_val, pm10_val, message, phone="SIMULATED-BROADCAST", status="SENT"):
    conn = get_connection()
    try:
        cur = conn.cursor()
        if location_id is not None and not location_exists(cur, location_id):
            raise ValueError(f"Unknown location_id: {location_id}")

        now_str = datetime.now().isoformat()
        cur.execute("""
        INSERT INTO sms_alerts (location_id, triggered_at, rule_triggered, pm25_val, pm10_val, message, recipient_phone, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """, (location_id, now_str, rule_triggered, pm25_val, pm10_val, message, phone, status))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()

def get_recent_observations(location_id, limit=24):
    """Return the latest observations in chronological order for forecasting."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT recorded_at, pm25, pm10, temperature, humidity, wind_speed,
                   pressure, rainfall
            FROM observations
            WHERE location_id = ?
            ORDER BY recorded_at DESC
            LIMIT ?;
        """, (location_id, int(limit)))
        rows = [dict(r) for r in cur.fetchall()]
        rows.reverse()
        return rows
    finally:
        conn.close()

def get_alert_history(limit=20):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("""
        SELECT a.*, l.name as location_name 
        FROM sms_alerts a 
        LEFT JOIN locations l ON a.location_id = l.id 
        ORDER BY a.id DESC LIMIT ?;
        """, (limit,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

def add_subscription(phone, location_id="delhi-cp"):
    conn = get_connection()
    try:
        cur = conn.cursor()
        if not location_exists(cur, location_id):
            raise ValueError(f"Unknown location_id: {location_id}")

        cur.execute("""
        INSERT OR REPLACE INTO subscriptions (phone, location_id, subscribed_at)
        VALUES (?, ?, ?);
        """, (phone, location_id, datetime.now().isoformat()))
        conn.commit()
    finally:
        conn.close()

def get_subscriptions():
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM subscriptions ORDER BY subscribed_at DESC;")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

def get_gateway_config():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM gateway_settings;")
        settings = dict(cur.fetchall())
    finally:
        conn.close()
    
    provider = settings.get("provider") or os.environ.get("ECOBYTES_SMS_PROVIDER", "fast2sms")
    api_key = settings.get("api_key") or os.environ.get("FAST2SMS_API_KEY", "")
    is_live = settings.get("is_live", "0") == "1"
    
    return {
        "provider": provider,
        "api_key": api_key,
        "is_live": is_live,
        "twilio_sid": settings.get("twilio_sid") or os.environ.get("TWILIO_ACCOUNT_SID", ""),
        "twilio_token": settings.get("twilio_token") or os.environ.get("TWILIO_AUTH_TOKEN", ""),
        "twilio_from": settings.get("twilio_from") or os.environ.get("TWILIO_FROM", "")
    }

def save_gateway_config(config):
    conn = get_connection()
    try:
        cur = conn.cursor()
        for k, v in config.items():
            cur.execute("INSERT OR REPLACE INTO gateway_settings (key, value) VALUES (?, ?);", (str(k), str(v)))
        conn.commit()
    finally:
        conn.close()

# Auto-initialize on import
init_db()
