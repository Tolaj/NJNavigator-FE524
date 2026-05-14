"""rt_loader.py — Live MTA delays and PATH arrivals via GTFS-RT"""
import os
import requests
from google.transit import gtfs_realtime_pb2

HEADERS = {"x-api-key": os.environ.get("MTA_API_KEY", "")}

MTA_FEEDS = {
    "123456S": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs",
    "ACE":     "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-ace",
    "BDFM":    "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-bdfm",
    "G":       "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-g",
    "JZ":      "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-jz",
    "NQRW":    "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-nqrw",
    "L":       "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-l",
}

ROUTE_TO_FEED = {
    **{r: "123456S" for r in "1234567S"},
    **{r: "ACE"     for r in "ACE"},
    **{r: "BDFM"    for r in "BDFM"},
    **{r: "JZ"      for r in "JZ"},
    **{r: "NQRW"    for r in "NQRW"},
    "G": "G", "L": "L",
}

PATH_RT_URL    = "https://path.transitdata.nyc/gtfsrt"
MTA_ALERTS_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fall-alerts"


def _fetch(url: str, headers: dict = {}) -> gtfs_realtime_pb2.FeedMessage | None:
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(resp.content)
        return feed
    except Exception:
        return None


def get_mta_delays(routes: list[str]) -> dict[str, int]:
    """Returns {trip_id: delay_seconds} for the given route letters."""
    feeds  = {ROUTE_TO_FEED[r] for r in routes if r in ROUTE_TO_FEED}
    delays = {}
    for feed_key in feeds:
        feed = _fetch(MTA_FEEDS[feed_key], HEADERS)
        if not feed:
            continue
        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue
            tu = entity.trip_update
            for stu in tu.stop_time_update:
                delay = (stu.arrival.delay   if stu.HasField("arrival")   else 0) or \
                        (stu.departure.delay if stu.HasField("departure") else 0)
                if delay:
                    delays[tu.trip.trip_id] = delay
                    break
    return delays


def get_mta_alerts() -> list[dict]:
    """Returns active MTA service alerts."""
    feed = _fetch(MTA_ALERTS_URL, HEADERS)
    if not feed:
        return []
    alerts = []
    for entity in feed.entity:
        if not entity.HasField("alert"):
            continue
        a      = entity.alert
        header = a.header_text.translation[0].text      if a.header_text.translation      else ""
        desc   = a.description_text.translation[0].text if a.description_text.translation else ""
        if header:
            alerts.append({
                "header":      header,
                "description": desc,
                "routes":      [ie.route_id for ie in a.informed_entity if ie.route_id],
            })
    return alerts


def get_path_arrivals() -> dict[str, list[dict]]:
    """Returns {stop_id: [{trip_id, arrival_time, headsign}]} for PATH stations."""
    feed = _fetch(PATH_RT_URL)
    if not feed:
        return {}
    arrivals: dict[str, list] = {}
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu       = entity.trip_update
        headsign = getattr(tu.trip, "trip_headsign", "") or ""
        for stu in tu.stop_time_update:
            t = (stu.arrival.time   if stu.HasField("arrival")   else None) or \
                (stu.departure.time if stu.HasField("departure") else None)
            if t:
                arrivals.setdefault(stu.stop_id, []).append(
                    {"trip_id": tu.trip.trip_id, "arrival_time": t, "headsign": headsign}
                )
    for sid in arrivals:
        arrivals[sid].sort(key=lambda x: x["arrival_time"])
    return arrivals
