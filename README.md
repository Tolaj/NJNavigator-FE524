# NJNavigator (NJ/NYC Transit Trip Planner)

An LLM-powered transit assistant for the New York–New Jersey metro corridor, built with GTFS static + GTFS-Realtime feeds and the Claude API.

> **FE524: Prompt Engineering Lab for Business Applications**
> Stevens Institute of Technology | Spring 2026 | Instructor: Edward Loeser

---

## The Problem

Existing trip planning apps (Google Maps, Transit, Moovit) return rigid, list-based itineraries with no explanation of tradeoffs and no ability to handle nuanced queries like:

> *"I have a meeting at 6:30pm in Midtown — when should I leave from Journal Square?"*

This project replaces that experience with a conversational transit assistant that understands natural language, knows what's happening on the network right now, and explains its recommendations in plain English.

---

## How It Works

Given a natural language query, the system:

1. **Loads and indexes GTFS static schedule data** for MTA Subway, PATH Train, and NJ Transit
2. **Polls live GTFS-Realtime feeds** for current delays and service alerts
3. **Computes candidate itineraries** by merging static and real-time data
4. **Passes those itineraries and alerts to Claude**, which returns a clear, human-readable trip plan with the best option and any relevant caveats

---

## Data Sources

### Static GTFS Feeds (schedule data)

| # | Agency | Feed Type | Source |
|---|--------|-----------|--------|
| 1 | MTA Subway | GTFS Static | [MTA Developer Data](http://web.mta.info/developers/data/nyct/subway/google_transit.zip) |
| 2 | PATH Train | GTFS Static | [PANYNJ Schedules](https://www.panynj.gov/path/en/schedules-maps.html) |
| 3 | NJ Transit | GTFS Static | [NJ Transit Developer Portal](https://developer.njtransit.com/registration/) *(registration required)* |

### GTFS-Realtime Feeds (live data)

| # | Agency | Feed Type | Source |
|---|--------|-----------|--------|
| 4 | MTA Subway | TripUpdates | `https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct/gtfs` |
| 5 | MTA | Alerts | `https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys/all-alerts` |
| 6 | PATH Train | GTFS-RT | `https://path.transitdata.nyc/gtfsrt` |
| 7 | NJ Transit Rail | TripUpdates | [NJ Transit Data Source](https://datasource.njtransit.com/) *(registration required)* |

Static feeds are ZIP archives of CSV files. The core files used are: `stops.txt`, `routes.txt`, `trips.txt`, `stop_times.txt`, `calendar.txt`, `calendar_dates.txt`, and `transfers.txt`.

Real-time feeds provide live delay predictions (TripUpdates), service disruption text (Alerts), and vehicle positions. Alerts are passed directly to the LLM as context; TripUpdates are merged with the static schedule to produce adjusted itineraries.

---

## Evaluation

We will build a ground-truth evaluation dataset of **25–30 manually verified trip queries** covering common NJ-to-NYC routes (e.g. Journal Square → Penn Station, Hoboken → Times Square). Each entry includes origin, destination, requested departure time, and a verified correct itinerary cross-checked against official MTA and NJ Transit trip planners.

Model outputs are evaluated across the following dimensions:

| Dimension | Method |
|-----------|--------|
| **Route correctness** | Compare LLM-suggested route against ground truth — correct line(s) for origin/destination pair |
| **Departure time accuracy** | Verify suggested departure is within ±2 min of scheduled or RT-adjusted time |
| **Transfer validity** | Confirm layover time meets minimum from `transfers.txt`; flag invalid connections |
| **Arrival time accuracy** | End-to-end arrival within acceptable tolerance of ground truth |
| **Hallucination detection** | Queries with no valid service — system must say no service exists, not invent a route |
| **RT integration** | Run queries against live RT snapshot — verify delays are reflected in advised departure time |
| **Prompt technique comparison** | Compare accuracy across few-shot, tool use, RAG, and chain-of-thought prompting strategies |

---

## Getting Started

> *Setup instructions will be added as the project develops.*

### Prerequisites

- Python 3.10+
- Anthropic API key (Claude)
- MTA API key (for real-time feeds)
- NJ Transit developer registration

### Installation

```bash
git clone https://github.com/<your-org>/nj-nyc-transit-planner.git
cd nj-nyc-transit-planner
pip install -r requirements.txt
```

### Configuration

```bash
cp .env.example .env
# Add your API keys to .env
```

---

## License

For academic use only — Stevens Institute of Technology, FE524, Spring 2026.
