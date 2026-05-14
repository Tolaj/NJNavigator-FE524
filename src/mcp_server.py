"""mcp_server.py — MCP server with transit tools"""
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv
from fastmcp import FastMCP
from sqlalchemy import create_engine, text

load_dotenv()

# ensure src/ is on path so utils.rt_loader resolves when run as subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "transit.db"
engine  = create_engine(f"sqlite:///{DB_PATH}")
mcp     = FastMCP("NJNavigator")

AGENCIES    = ["mta", "path", "njt", "njtbus"]
_rag_index  = None   # lazy-loaded on first call to search_transit_knowledge


def _get_rag_index():
    global _rag_index
    if _rag_index is None:
        try:
            from utils.rag_builder import load_index
            _rag_index = load_index()
        except Exception:
            _rag_index = False   # mark as unavailable so we don't retry
    return _rag_index if _rag_index else None


# ── Helpers ───────────────────────────────────────────────────────────────────

def sql(query: str, params: dict = {}) -> pd.DataFrame:
    return pd.read_sql(text(query), engine, params=params)


def rows(table: str, **where) -> pd.DataFrame:
    conds = " AND ".join(f"{k}=:{k}" for k in where)
    q     = f"SELECT * FROM {table}" + (f" WHERE {conds}" if conds else "")
    return pd.read_sql(text(q), engine, params=where)


def haversine(lat1, lon1, lat2, lon2) -> float:
    R = 3958.8
    a = math.sin(math.radians(lat2 - lat1) / 2) ** 2 + \
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def expand_stop_ids(agency: str, stop_id: str) -> list[str]:
    """Return platform-level stop IDs, expanding parent stations to their children.
    Also climbs up to parent if given a child/entrance stop with no stop_times."""
    def _children(parent: str) -> list[str]:
        try:
            df = sql(f"SELECT stop_id FROM {agency}_stops "
                     f"WHERE parent_station=:sid AND (location_type IS NULL OR location_type != 1)",
                     {"sid": parent})
            return df["stop_id"].tolist() if not df.empty else []
        except Exception:
            return []

    try:
        row = sql(f"SELECT location_type, parent_station FROM {agency}_stops WHERE stop_id=:sid",
                  {"sid": stop_id})
        if row.empty:
            return [stop_id]
        loc_type = row.iloc[0]["location_type"]
        parent   = row.iloc[0].get("parent_station")

        if loc_type == 1:
            kids = _children(stop_id)
            return kids if kids else [stop_id]

        # Child / entrance — climb to parent and expand from there
        if parent and str(parent).strip():
            kids = _children(str(parent))
            return kids if kids else [stop_id]
    except Exception:
        pass
    return [stop_id]


def active_services(agency: str, stop_ids: list[str]) -> set:
    today = datetime.now().strftime("%Y%m%d")
    dow   = datetime.now().strftime("%A").lower()
    try:
        cal = sql(f"SELECT service_id FROM {agency}_calendar "
                  f"WHERE {dow}=1 AND start_date<=:d AND end_date>=:d", {"d": today})
        active = set(cal["service_id"])
    except Exception:
        active = set()
    try:
        added   = set(sql(f"SELECT service_id FROM {agency}_calendar_dates "
                          f"WHERE date=:d AND exception_type='1'", {"d": today})["service_id"])
        removed = set(sql(f"SELECT service_id FROM {agency}_calendar_dates "
                          f"WHERE date=:d AND exception_type='2'", {"d": today})["service_id"])
        active  = (active | added) - removed
    except Exception:
        pass
    if not active:  # fallback for expired GTFS — sample from actual stop_times
        ph = ",".join(f"'{s}'" for s in stop_ids)
        try:
            active = set(sql(f"SELECT DISTINCT t.service_id FROM {agency}_stop_times s "
                             f"JOIN {agency}_trips t ON s.trip_id=t.trip_id "
                             f"WHERE s.stop_id IN ({ph}) LIMIT 20")["service_id"])
        except Exception:
            pass
    return active


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_current_time() -> str:
    """Return current date and time. Call first when no departure time is given."""
    now = datetime.now()
    return json.dumps({
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "day_of_week": now.strftime("%A"),
    })


@mcp.tool()
def geocode_address(address: str) -> str:
    """Convert a street address to the nearest transit stop per agency."""
    resp = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": address, "format": "json", "limit": 1},
        headers={"User-Agent": "NJNavigator/1.0"},
        timeout=8,
    )
    data = resp.json()
    if not data:
        return json.dumps({"error": "Address not found"})

    lat, lon = float(data[0]["lat"]), float(data[0]["lon"])
    nearest  = []

    for ag in AGENCIES:
        try:
            df   = sql(f"SELECT stop_id, stop_name, stop_lat, stop_lon FROM {ag}_stops "
                       f"WHERE stop_lat IS NOT NULL AND stop_lat != '' "
                       f"AND (location_type IS NULL OR location_type != 2)")
            df   = df.dropna(subset=["stop_lat", "stop_lon"])
            df["dist"] = df.apply(lambda r: haversine(lat, lon, float(r.stop_lat), float(r.stop_lon)), axis=1)
            best = df.nsmallest(1, "dist").iloc[0]
            nearest.append({"agency": ag.upper(), "stop_id": best.stop_id,
                            "stop_name": best.stop_name, "distance_miles": round(best.dist, 3)})
        except Exception:
            pass

    return json.dumps({"lat": lat, "lon": lon, "nearest_stops": nearest})


@mcp.tool()
def search_stops(query: str, agency: str = "all") -> str:
    """Search transit stops by name. agency: mta / path / njt / njtbus / all."""
    agencies = AGENCIES if agency.lower() == "all" else [agency.lower()]
    results  = []
    for ag in agencies:
        try:
            df = sql(f"SELECT stop_id, stop_name FROM {ag}_stops "
                     f"WHERE UPPER(stop_name) LIKE :q LIMIT 6", {"q": f"%{query.upper()}%"})
            for _, r in df.iterrows():
                results.append({"agency": ag.upper(), "stop_id": r.stop_id, "stop_name": r.stop_name})
        except Exception:
            pass
    return json.dumps({"stops": results} if results else {"message": f"No stops found for '{query}'"})


@mcp.tool()
def get_departures(stop_id: str, agency: str, after_time: str, toward: str = "") -> str:
    """
    Next scheduled departures from a stop.
    agency: mta / path / njt / njtbus.  after_time: HH:MM:SS.
    toward: optional headsign keyword to filter direction.
    Pass the canonical stop_id (parent station or platform) — parent stations are expanded automatically.
    """
    ag       = agency.lower()
    stop_ids = expand_stop_ids(ag, stop_id)
    active   = active_services(ag, stop_ids)
    if not active:
        return json.dumps({"message": "No active services today."})

    svc_ph  = ",".join(f"'{s}'" for s in active)
    stop_ph = ",".join(f"'{s}'" for s in stop_ids)
    df = sql(f"""
        SELECT st.departure_time, t.trip_headsign,
               COALESCE(r.route_long_name, r.route_short_name, t.route_id) AS route_name
        FROM   {ag}_stop_times st
        JOIN   {ag}_trips t  ON st.trip_id  = t.trip_id
        LEFT JOIN {ag}_routes r ON t.route_id = r.route_id
        WHERE  st.stop_id IN ({stop_ph}) AND t.service_id IN ({svc_ph})
        ORDER  BY st.departure_time
    """)

    df = df[df["departure_time"] >= after_time]
    if toward:
        mask = df["trip_headsign"].str.contains(toward, case=False, na=False)
        if mask.any():
            df = df[mask]
        else:
            note = f"No trains toward '{toward}' — showing all directions."
            return json.dumps({"note": note, "departures": df.head(6).to_dict("records")})

    if df.empty:
        return json.dumps({"message": "No upcoming departures."})
    return json.dumps({"departures": df.head(6).to_dict("records")})


@mcp.tool()
def get_realtime_status(routes: str) -> str:
    """
    Live MTA delays and PATH arrivals.
    routes: comma-separated route letters e.g. 'A,C,E' or 'PATH'.
    """
    from utils.rt_loader import get_mta_delays, get_path_arrivals

    route_list = [r.strip().upper() for r in routes.split(",")]
    result: dict = {}

    if "PATH" in route_list:
        raw = get_path_arrivals()
        formatted = {}
        for stop_id, arrivals in list(raw.items())[:6]:
            readable = []
            for a in arrivals[:3]:
                ts = a.get("arrival_time")
                time_str = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime("%H:%M") if ts else "?"
                readable.append({
                    "arrival_time": time_str,
                    "headsign": a.get("headsign", ""),
                    "trip_id": a.get("trip_id", ""),
                })
            formatted[stop_id] = readable
        result["path_arrivals"] = formatted

    mta = [r for r in route_list if r != "PATH"]
    if mta:
        delays = get_mta_delays(mta)
        result["mta_delays"] = {"delayed_trip_count": len(delays)}

    return json.dumps(result or {"message": "No data."})


@mcp.tool()
def get_service_alerts(agency: str = "mta") -> str:
    """
    Active service disruption alerts.
    agency: 'mta' (subway alerts) or 'path' (PATH has no separate alert feed — use get_realtime_status).
    Returns up to 10 current alerts with affected routes and description.
    """
    agency = agency.lower()
    if agency != "mta":
        return json.dumps({"message": "Only MTA alerts are available. For PATH use get_realtime_status."})

    from utils.rt_loader import get_mta_alerts
    alerts = get_mta_alerts()
    if not alerts:
        return json.dumps({"message": "No active MTA service alerts."})
    return json.dumps({"alerts": alerts[:10]})


@mcp.tool()
def get_transfers(stop_id: str, agency: str) -> str:
    """Same-agency transfers available at a stop (from GTFS transfers.txt)."""
    ag = agency.lower()
    try:
        df = sql(f"""
            SELECT t.to_stop_id, t.transfer_type, t.min_transfer_time,
                   s.stop_name
            FROM   {ag}_transfers t
            JOIN   {ag}_stops s ON t.to_stop_id = s.stop_id
            WHERE  t.from_stop_id = :sid
        """, {"sid": stop_id})
        if df.empty:
            return json.dumps({"message": "No transfers found at this stop."})
        return json.dumps({"transfers": df.to_dict("records")})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_interchange(from_agency: str, to_agency: str) -> str:
    """Cross-agency transfer stations between two agencies. Use: MTA, PATH, NJT, NJTBUS."""
    INTERCHANGE = [
        # MTA ↔ PATH
        {"station": "World Trade Center",      "mta": "E01",   "path": "26734"},
        {"station": "33rd Street",             "mta": "D17",   "path": "26724"},
        # MTA ↔ NJT Rail
        {"station": "New York Penn Station",   "mta": "128N",  "njt": "105"},
        # MTA ↔ NJT Bus
        {"station": "Port Authority Bus Term", "mta": "A27",   "njtbus": "3511"},
        # PATH ↔ NJT Rail
        {"station": "Hoboken Terminal",        "path": "26730", "njt": "63"},
        {"station": "Newark Penn Station",     "path": "26733", "njt": "105"},
        # PATH ↔ NJT Bus
        {"station": "Journal Square",          "path": "26731", "njtbus": "2916"},
        {"station": "Hoboken Terminal",        "path": "26730", "njtbus": "17082"},
        {"station": "Newark Penn Station",     "path": "26733", "njtbus": "43283"},
        # Three-way
        {"station": "Hoboken Terminal",        "path": "26730", "njt": "63",  "njtbus": "17082"},
        {"station": "Newark Penn Station",     "path": "26733", "njt": "105", "njtbus": "43283"},
    ]

    fa, ta = from_agency.lower(), to_agency.lower()
    matches = [
        {
            "station":    s["station"],
            f"{fa}_stop": s.get(fa, ""),
            f"{ta}_stop": s.get(ta, ""),
        }
        for s in INTERCHANGE if s.get(fa) and s.get(ta)
    ]

    return json.dumps({"interchange": matches} if matches else {"message": "No interchange found."})


@mcp.tool()
def search_transit_knowledge(query: str) -> str:
    """
    Semantic search over transit route descriptions, station summaries, and transfer hubs.
    Use this for questions like:
      - "what lines serve Times Square?"
      - "which PATH station is near WTC?"
      - "tell me about the Northeast Corridor"
      - "what NJT bus routes go to NYC?"
    Do NOT use for live schedules or departure times — use get_departures for those.
    """
    idx = _get_rag_index()
    if idx is None:
        return json.dumps({"message": "Knowledge index not available."})
    try:
        docs = idx.similarity_search(query, k=4)
        results = [{"content": d.page_content, "source": d.metadata} for d in docs]
        return json.dumps({"results": results})
    except Exception as e:
        return json.dumps({"error": str(e)})


if __name__ == "__main__":
    print("MCP server running on http://localhost:8000/mcp")
    mcp.run(transport="streamable-http", port=8000)
