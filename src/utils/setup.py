"""
setup.py — Download GTFS feeds using gtfs_kit and load into data/transit.db

Run: python setup.py
"""
import sqlite3
from pathlib import Path

import requests
import gtfs_kit as gk

HEADERS = {"User-Agent": "NJNavigator/1.0"}

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
ZIP_DIR  = DATA_DIR / "zip"
DB_PATH  = DATA_DIR / "transit.db"

FEEDS = {
    "mta":    "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_supplemented.zip",
    "path":   "https://data.trilliumtransit.com/gtfs/path-nj-us/path-nj-us.zip",
    "njt":    "https://www.njtransit.com/rail_data.zip",
    "njtbus": "https://www.njtransit.com/bus_data.zip",
}

TABLES = ["stops", "routes", "trips", "stop_times", "calendar", "calendar_dates", "transfers"]


def download(agency: str, url: str) -> Path:
    dest = ZIP_DIR / f"{agency}.zip"
    if dest.exists():
        print(f"  {agency}.zip already exists, skipping download")
        return dest
    print(f"  Downloading {agency} ...")
    resp = requests.get(url, headers=HEADERS, stream=True, timeout=120)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
    print(f"  Saved {agency}.zip ({dest.stat().st_size / 1024 / 1024:.1f} MB)")
    return dest


def load(agency: str, zip_path: Path, conn: sqlite3.Connection) -> None:
    print(f"  Loading {agency} into DB ...")
    feed = gk.read_feed(zip_path, dist_units="km")

    if agency == "njtbus":
        # Keep only NYC-bound routes 100–199
        routes = feed.routes
        routes = routes[routes["route_short_name"].apply(
            lambda n: 100 <= int(n) <= 199 if str(n).isdigit() else False
        )]
        trips      = feed.trips[feed.trips["route_id"].isin(routes["route_id"])]
        stop_times = feed.stop_times[feed.stop_times["trip_id"].isin(trips["trip_id"])]
        stops      = feed.stops[feed.stops["stop_id"].isin(stop_times["stop_id"])]
        svc        = set(trips["service_id"])
        calendar   = feed.calendar[feed.calendar["service_id"].isin(svc)]         if feed.calendar        is not None else None
        cal_dates  = feed.calendar_dates[feed.calendar_dates["service_id"].isin(svc)] if feed.calendar_dates is not None else None

        tables = [("routes", routes), ("trips", trips), ("stop_times", stop_times),
                  ("stops", stops), ("calendar", calendar), ("calendar_dates", cal_dates)]
    else:
        tables = [(t, getattr(feed, t, None)) for t in TABLES]

    for name, df in tables:
        if df is not None and not df.empty:
            df.to_sql(f"{agency}_{name}", conn, if_exists="replace", index=False)
            print(f"    {agency}_{name}: {len(df):,} rows")


def index(agency: str, conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    for table, col in [
        ("stops",          "stop_id"),
        ("trips",          "trip_id"),
        ("trips",          "service_id"),
        ("stop_times",     "trip_id"),
        ("stop_times",     "stop_id"),
        ("calendar",       "service_id"),
        ("calendar_dates", "service_id"),
    ]:
        full = f"{agency}_{table}"
        if cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (full,)).fetchone():
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{full}_{col} ON {full}({col})")
    conn.commit()
    print(f"  Indexes created for {agency}")


if __name__ == "__main__":
    DATA_DIR.mkdir(exist_ok=True)
    ZIP_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    for agency, url in FEEDS.items():
        print(f"\n[{agency.upper()}]")
        try:
            zip_path = download(agency, url)
            load(agency, zip_path, conn)
            index(agency, conn)
        except Exception as e:
            print(f"  FAILED: {e}")

    conn.close()
    print(f"\nDone → {DB_PATH}")
