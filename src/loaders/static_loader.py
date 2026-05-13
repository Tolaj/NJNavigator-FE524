"""
Downloads GTFS static ZIPs for MTA, PATH, and NJ Transit
and loads them into data/transit.db (SQLite).

Run once at setup:  python src/loaders/static_loader.py
Re-run anytime to refresh data.
"""

import io
import sqlite3
import zipfile
from pathlib import Path

import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "transit.db"

FEEDS = {
    "mta": {
        "url": "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_supplemented.zip",
        "dest": DATA_DIR / "static" / "mta_subway",
    },
    "path": {
        "url": "https://data.trilliumtransit.com/gtfs/path-nj-us/path-nj-us.zip",
        "dest": DATA_DIR / "static" / "path",
    },
    "njt": {
        "url": "https://www.njtransit.com/rail_data.zip",
        "dest": DATA_DIR / "static" / "njt_rail",
    },
}

GTFS_TABLES = [
    "stops", "routes", "trips", "stop_times",
    "calendar", "calendar_dates", "transfers",
]

HEADERS = {"User-Agent": "NJNavigator-FE524/1.0"}


def download_and_extract(url: str, dest: Path) -> bool:
    print(f"  Downloading {url} ...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=120)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            zf.extractall(dest)
        print(f"  Extracted to {dest}")
        return True
    except Exception as e:
        print(f"  Download failed: {e}")
        return False


def load_agency(agency: str, src: Path, conn: sqlite3.Connection) -> None:
    for table in GTFS_TABLES:
        csv_path = src / f"{table}.txt"
        if not csv_path.exists():
            continue
        df = pd.read_csv(csv_path, dtype=str, low_memory=False)
        tbl = f"{agency}_{table}"
        df.to_sql(tbl, conn, if_exists="replace", index=False)
        print(f"  [{agency}] {tbl}: {len(df):,} rows")

    # Indexes for fast lookup
    cur = conn.cursor()
    for table, cols in {
        "stops":          ["stop_id"],
        "trips":          ["trip_id", "route_id", "service_id"],
        "stop_times":     ["trip_id", "stop_id"],
        "calendar":       ["service_id"],
        "calendar_dates": ["service_id", "date"],
    }.items():
        tbl = f"{agency}_{table}"
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,))
        if not cur.fetchone():
            continue
        for col in cols:
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{tbl}_{col} ON {tbl}({col})")
    conn.commit()


def setup_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    for agency, cfg in FEEDS.items():
        dest: Path = cfg["dest"]
        dest.mkdir(parents=True, exist_ok=True)

        if any(dest.glob("*.txt")):
            print(f"\n[{agency.upper()}] Already extracted — skipping download")
        else:
            print(f"\n[{agency.upper()}] Fetching GTFS ...")
            if not download_and_extract(cfg["url"], dest):
                print(f"  Skipping {agency.upper()} load.")
                continue

        print(f"[{agency.upper()}] Loading into SQLite ...")
        load_agency(agency, dest, conn)

    conn.close()
    print(f"\nDatabase ready: {DB_PATH}")


if __name__ == "__main__":
    setup_db()
