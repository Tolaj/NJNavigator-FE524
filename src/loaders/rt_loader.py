"""
Fetches and parses GTFS-Realtime protobuf feeds.
Returns plain Python dicts — no protobuf objects outside this module.
"""

import time
from typing import Any

import requests
from google.transit import gtfs_realtime_pb2

HEADERS = {"User-Agent": "NJNavigator-FE524/1.0"}

# MTA has 8 separate feeds — one per line group
MTA_RT_FEEDS = {
    "123456S": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs",
    "ACE":     "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-ace",
    "BDFM":    "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-bdfm",
    "G":       "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-g",
    "JZ":      "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-jz",
    "NQRW":    "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-nqrw",
    "L":       "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-l",
    "SIR":     "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-si",
}

# Route letter → feed group mapping
ROUTE_TO_GROUP = {
    **{r: "123456S" for r in ["1","2","3","4","5","6","7","S"]},
    **{r: "ACE"     for r in ["A","C","E"]},
    **{r: "BDFM"    for r in ["B","D","F","M"]},
    "G": "G",
    **{r: "JZ"      for r in ["J","Z"]},
    **{r: "NQRW"    for r in ["N","Q","R","W"]},
    "L": "L",
}

MTA_ALERTS_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fall-alerts"
PATH_RT_URL    = "https://path.transitdata.nyc/gtfsrt"


def _fetch(url: str) -> gtfs_realtime_pb2.FeedMessage | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(resp.content)
        return feed
    except Exception as e:
        print(f"  RT fetch error ({url}): {e}")
        return None


def get_mta_delays(routes: list[str] | None = None) -> dict[str, int]:
    """
    Returns {trip_id: delay_seconds} for MTA trips with an active delay.
    Pass route letters (e.g. ["A","C","E"]) to only poll relevant feeds.
    Polls all 8 feeds if routes is None.
    """
    if routes:
        groups = list({ROUTE_TO_GROUP[r] for r in routes if r in ROUTE_TO_GROUP})
    else:
        groups = list(MTA_RT_FEEDS.keys())

    delays: dict[str, int] = {}
    for group in groups:
        feed = _fetch(MTA_RT_FEEDS[group])
        if not feed:
            continue
        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue
            tu = entity.trip_update
            for stu in tu.stop_time_update:
                delay = 0
                if stu.HasField("arrival") and stu.arrival.delay:
                    delay = stu.arrival.delay
                elif stu.HasField("departure") and stu.departure.delay:
                    delay = stu.departure.delay
                if delay:
                    delays[tu.trip.trip_id] = delay
                    break
    return delays


def get_mta_alerts() -> list[dict[str, Any]]:
    """
    Returns active MTA service alerts as plain dicts.
    """
    feed = _fetch(MTA_ALERTS_URL)
    if not feed:
        return []

    alerts = []
    for entity in feed.entity:
        if not entity.HasField("alert"):
            continue
        a = entity.alert
        header = a.header_text.translation[0].text if a.header_text.translation else ""
        desc   = a.description_text.translation[0].text if a.description_text.translation else ""
        routes = list({ie.route_id for ie in a.informed_entity if ie.route_id})
        stops  = list({ie.stop_id  for ie in a.informed_entity if ie.stop_id})
        if header:
            alerts.append({
                "header":          header,
                "description":     desc,
                "affected_routes": routes,
                "affected_stops":  stops,
                "fetched_at":      int(time.time()),
            })
    return alerts


def get_path_arrivals() -> dict[str, list[dict[str, Any]]]:
    """
    Returns {stop_id: [arrivals]} for PATH stations.
    Each arrival: {trip_id, arrival_time (unix), headsign}.
    Note: PATH RT has no trip continuity — each prediction is per-stop only.
    """
    feed = _fetch(PATH_RT_URL)
    if not feed:
        return {}

    arrivals: dict[str, list] = {}
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        headsign = getattr(tu.trip, "trip_headsign", "") or ""
        for stu in tu.stop_time_update:
            t = None
            if stu.HasField("arrival") and stu.arrival.time:
                t = stu.arrival.time
            elif stu.HasField("departure") and stu.departure.time:
                t = stu.departure.time
            if t:
                arrivals.setdefault(stu.stop_id, []).append({
                    "trip_id":      tu.trip.trip_id,
                    "arrival_time": t,
                    "headsign":     headsign,
                })

    for sid in arrivals:
        arrivals[sid].sort(key=lambda x: x["arrival_time"])
    return arrivals
