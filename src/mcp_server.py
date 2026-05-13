"""
FastMCP server — exposes 7 tools to the smolagents ToolCallingAgent.

Tools:
  get_current_time        — current date/time (always call when no time specified)
  geocode_address         — street address → nearest transit stops (Nominatim + Haversine)
  search_stops            — fuzzy stop name lookup across MTA / PATH / NJT
  get_departures          — next scheduled departures from a stop
  get_realtime_status     — live MTA delays + PATH next arrivals
  search_knowledge        — RAG search (Wikipedia + MTA alerts via ChromaDB)
  get_interchange_stations — cross-agency transfer points

Run:  python src/mcp_server.py
      → HTTP server on http://localhost:8000/mcp
"""

import csv
import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastmcp import FastMCP

# Ensure project root is on sys.path so 'src' package is importable
_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

load_dotenv()

try:
    from src.loaders.rt_loader import get_mta_delays, get_path_arrivals
    _RT_AVAILABLE = True
except Exception as _rt_err:
    _RT_AVAILABLE = False
    _RT_ERROR = str(_rt_err)
    def get_mta_delays(*a, **kw): return {}   # type: ignore[misc]
    def get_path_arrivals(*a, **kw): return {} # type: ignore[misc]

BASE_DIR         = Path(__file__).resolve().parents[1]
DB_PATH          = BASE_DIR / "data" / "transit.db"
INTERCHANGE_PATH = BASE_DIR / "data" / "interchange.csv"

mcp = FastMCP("NJNavigator")

# ── ChromaDB vectorstore (injected by main.py before server starts) ──────────

_vectorstore = None

def set_vectorstore(vs) -> None:
    global _vectorstore
    _vectorstore = vs

# ── DB helper ─────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

# ── Time helper ───────────────────────────────────────────────────────────────

def _gtfs_to_secs(t: str) -> int:
    """HH:MM:SS (possibly >24h) → total seconds since midnight."""
    try:
        h, m, s = t.strip().split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except Exception:
        return 0

# ── Tool 1: get_current_time ─────────────────────────────────────────────────

@mcp.tool()
def get_current_time() -> str:
    """
    Return the current date and time.
    Always call this first when the user has not specified a departure time.
    Returns JSON: {date, time (HH:MM:SS), day_of_week, unix_ts}
    """
    now = datetime.now()
    return json.dumps({
        "date":        now.strftime("%Y-%m-%d"),
        "time":        now.strftime("%H:%M:%S"),
        "day_of_week": now.strftime("%A"),
        "unix_ts":     int(now.timestamp()),
    })

# ── Tool 2: geocode_address ───────────────────────────────────────────────────

@mcp.tool()
def geocode_address(address: str) -> str:
    """
    Convert a street address into the 3 nearest transit stops across all agencies.
    Uses Nominatim (OpenStreetMap) for geocoding — no API key needed.
    address: e.g. '31 Hopkins Ave, Jersey City, NJ'
    Returns JSON: {lat, lon, nearest_stops: [{stop_id, stop_name, agency, distance_miles}]}
    Call this whenever the user gives a street address instead of a stop name.
    """
    import urllib.request
    import urllib.parse

    params = urllib.parse.urlencode({"q": address, "format": "json", "limit": 1})
    url = f"https://nominatim.openstreetmap.org/search?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "NJNavigator/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return json.dumps({"error": f"Geocoding failed: {e}"})

    if not data:
        return json.dumps({"error": f"Address not found: {address}"})

    lat = float(data[0]["lat"])
    lon = float(data[0]["lon"])

    def _haversine(lat1, lon1, lat2, lon2) -> float:
        R = 3958.8
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    conn = _conn()
    all_stops = []
    for ag in ("mta", "path", "njt"):
        try:
            rows = conn.execute(
                f"SELECT stop_id, stop_name, stop_lat, stop_lon FROM {ag}_stops "
                f"WHERE stop_lat IS NOT NULL AND stop_lat != '' "
                f"AND (location_type IS NULL OR location_type = '0' OR location_type = 0)"
            ).fetchall()
            for r in rows:
                try:
                    slat, slon = float(r["stop_lat"]), float(r["stop_lon"])
                except (TypeError, ValueError):
                    continue
                dist = _haversine(lat, lon, slat, slon)
                all_stops.append({
                    "stop_id":        r["stop_id"],
                    "stop_name":      r["stop_name"],
                    "agency":         ag.upper(),
                    "distance_miles": round(dist, 3),
                })
        except Exception:
            pass
    conn.close()

    all_stops.sort(key=lambda x: x["distance_miles"])
    return json.dumps({"lat": lat, "lon": lon, "nearest_stops": all_stops[:3]})

# ── Tool 3: search_stops ─────────────────────────────────────────────────────

@mcp.tool()
def search_stops(query: str, agency: str = "all") -> str:
    """
    Search for transit stops by STOP NAME only (e.g. 'Grove Street', 'Penn Station').
    Do NOT pass street addresses here — use geocode_address() for that.
    agency: 'mta', 'path', 'njt', or 'all' (default).
    Returns JSON list: [{stop_id, stop_name, agency, lat, lon}]
    """
    agencies = ["mta", "path", "njt"] if agency.lower() == "all" else [agency.lower()]
    like = f"%{query.upper()}%"
    results = []
    conn = _conn()
    for ag in agencies:
        try:
            rows = conn.execute(
                f"SELECT stop_id, stop_name, stop_lat, stop_lon "
                f"FROM {ag}_stops WHERE UPPER(stop_name) LIKE ? LIMIT 8",
                (like,)
            ).fetchall()
            for r in rows:
                results.append({
                    "stop_id":   r["stop_id"],
                    "stop_name": r["stop_name"],
                    "agency":    ag.upper(),
                    "lat":       r["stop_lat"],
                    "lon":       r["stop_lon"],
                })
        except Exception:
            pass
    conn.close()
    if not results:
        return json.dumps({"message": f"No stops found matching '{query}'"})
    return json.dumps({"stops": results})

# ── Tool 4: get_departures ────────────────────────────────────────────────────

@mcp.tool()
def get_departures(stop_id: str, agency: str, after_time: str) -> str:
    """
    Get next scheduled departures from a stop.
    agency: 'mta', 'path', or 'njt'.
    after_time: HH:MM:SS (e.g. '18:00:00'). Use 24-hour format.
    Returns up to 6 next departures: [{departure_time, route, headsign, trip_id}]
    """
    ag = agency.lower()
    conn = _conn()
    target_secs = _gtfs_to_secs(after_time)

    # If stop_id looks like a stop name (not numeric/code), resolve it first
    if not stop_id.lstrip("-").replace("_", "").isdigit() and not any(c.isdigit() for c in stop_id[:4]):
        try:
            hit = conn.execute(
                f"SELECT stop_id FROM {ag}_stops WHERE UPPER(stop_name) LIKE ? "
                f"AND (location_type IS NULL OR location_type='0' OR location_type=0) LIMIT 1",
                (f"%{stop_id.upper()}%",)
            ).fetchone()
            if hit:
                stop_id = hit["stop_id"]
        except Exception:
            pass

    # Resolve parent station → child stops (location_type=0 stops used in stop_times)
    try:
        loc = conn.execute(
            f"SELECT location_type FROM {ag}_stops WHERE stop_id=?", (stop_id,)
        ).fetchone()
        if loc and str(loc["location_type"]) == "1":
            children = conn.execute(
                f"SELECT stop_id FROM {ag}_stops WHERE parent_station=? AND (location_type='0' OR location_type IS NULL)",
                (stop_id,)
            ).fetchall()
            if children:
                stop_id = children[0]["stop_id"]  # use first child platform
    except Exception:
        pass

    # Get today's active service_ids
    today = datetime.now().strftime("%Y%m%d")
    dow   = datetime.now().strftime("%A").lower()

    try:
        active = set()

        # Regular calendar
        try:
            rows = conn.execute(
                f"SELECT service_id FROM {ag}_calendar "
                f"WHERE {dow}='1' AND start_date<=? AND end_date>=?",
                (today, today)
            ).fetchall()
            active = {r["service_id"] for r in rows}
        except Exception:
            pass

        # calendar_dates additions / removals
        try:
            added = {r["service_id"] for r in conn.execute(
                f"SELECT service_id FROM {ag}_calendar_dates WHERE date=? AND exception_type='1'",
                (today,)
            ).fetchall()}
            removed = {r["service_id"] for r in conn.execute(
                f"SELECT service_id FROM {ag}_calendar_dates WHERE date=? AND exception_type='2'",
                (today,)
            ).fetchall()}
            active = (active | added) - removed
        except Exception:
            pass

        # Fallback: GTFS calendar may be expired — use the most recent service_ids
        # that actually have stop_times for this stop (covers stale static data).
        if not active:
            try:
                fb = conn.execute(
                    f"""
                    SELECT DISTINCT t.service_id
                    FROM   {ag}_stop_times st
                    JOIN   {ag}_trips t ON st.trip_id = t.trip_id
                    WHERE  st.stop_id = ?
                    LIMIT  20
                    """,
                    (stop_id,)
                ).fetchall()
                active = {r["service_id"] for r in fb}
            except Exception:
                pass

        if not active:
            conn.close()
            return json.dumps({"message": "No active services found for today."})

        ph = ",".join("?" * len(active))
        rows = conn.execute(
            f"""
            SELECT MIN(st.trip_id) AS trip_id,
                   st.departure_time,
                   t.route_id, t.trip_headsign
            FROM   {ag}_stop_times st
            JOIN   {ag}_trips t ON st.trip_id = t.trip_id
            WHERE  st.stop_id = ?
              AND  t.service_id IN ({ph})
            GROUP  BY st.departure_time, t.route_id, t.trip_headsign
            ORDER  BY st.departure_time
            """,
            [stop_id] + list(active)
        ).fetchall()
        conn.close()

    except Exception as e:
        conn.close()
        return json.dumps({"error": str(e)})

    deps = []
    for r in rows:
        if _gtfs_to_secs(r["departure_time"]) >= target_secs:
            deps.append({
                "departure_time": r["departure_time"],
                "route":          r["route_id"],
                "headsign":       r["trip_headsign"],
            })
            if len(deps) >= 6:
                break

    if not deps:
        return json.dumps({"message": "No upcoming departures found from this stop."})
    return json.dumps({"departures": deps})

# ── Tool 5: get_realtime_status ───────────────────────────────────────────────

@mcp.tool()
def get_realtime_status(routes: str) -> str:
    """
    Fetch live delay and arrival data from MTA and PATH realtime feeds.
    routes: comma-separated route IDs, e.g. 'A,C,E' or '1,2,3' or 'PATH'.
            Use 'PATH' to get next PATH arrivals by station.
    Returns delays in seconds (positive = late) and PATH next arrivals.
    """
    if not _RT_AVAILABLE:
        return json.dumps({"error": f"Realtime feed unavailable: {_RT_ERROR}"})
    route_list = [r.strip().upper() for r in routes.split(",")]
    result: dict = {"fetched_at": time.strftime("%H:%M:%S")}

    if "PATH" in route_list:
        arrivals = get_path_arrivals()
        result["path_arrivals"] = {
            sid: arr[:3] for sid, arr in list(arrivals.items())[:8]
        }

    mta_routes = [r for r in route_list if r != "PATH"]
    if mta_routes:
        delays = get_mta_delays(mta_routes)
        result["mta_delays"] = {
            "delayed_trip_count": len(delays),
            "sample_delays":      dict(list(delays.items())[:6]),
            "note": "delay values in seconds; positive = late",
        }

    return json.dumps(result)

# ── Tool 6: search_knowledge ──────────────────────────────────────────────────

@mcp.tool()
def search_knowledge(query: str) -> str:
    """
    Search the knowledge base for transit information.
    Covers: MTA Subway, PATH Train, NJ Transit, GTFS, service alerts, general info.
    Use for background questions like 'how many PATH lines are there' or
    'what lines serve Penn Station' or 'is there a delay on the A train'.
    Returns relevant text excerpts from Wikipedia articles and live MTA alerts.
    """
    if _vectorstore is None:
        return json.dumps({"error": "Knowledge base not ready. Try again in a moment."})
    from src.rag.retrieve import search_knowledge as _rag_search
    chunks = _rag_search(_vectorstore, query, k=5)
    return json.dumps({"results": chunks})

# ── Tool 7: get_interchange_stations ─────────────────────────────────────────

@mcp.tool()
def get_interchange_stations(from_agency: str, to_agency: str) -> str:
    """
    Get transfer stations between two agencies.
    from_agency / to_agency: 'MTA', 'PATH', or 'NJT'.
    Returns station names, stop IDs for each agency, and minimum transfer time.
    Use this when planning a cross-agency trip before calling get_departures.
    """
    fa, ta = from_agency.upper(), to_agency.upper()
    col = {"MTA": "mta_stop_id", "PATH": "path_stop_id", "NJT": "njt_stop_id"}
    if fa not in col or ta not in col:
        return json.dumps({"error": f"Invalid agency. Use MTA, PATH, or NJT."})

    if not INTERCHANGE_PATH.exists():
        return json.dumps({"error": "interchange.csv not found."})

    matches = []
    with open(INTERCHANGE_PATH) as f:
        for row in csv.DictReader(f):
            fid = row.get(col[fa], "").strip()
            tid = row.get(col[ta], "").strip()
            if fid and tid:
                matches.append({
                    "station":                    row["station_name"],
                    f"{fa.lower()}_stop_id":      fid,
                    f"{ta.lower()}_stop_id":      tid,
                    "min_transfer_minutes":       row.get("min_transfer_minutes", ""),
                    "notes":                      row.get("notes", ""),
                })

    if not matches:
        return json.dumps({"message": f"No interchange found between {fa} and {ta}."})
    return json.dumps({"interchange_stations": matches})

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("[MCP] Server starting on http://localhost:8000/mcp")
    mcp.run(transport="streamable-http", port=8000)
