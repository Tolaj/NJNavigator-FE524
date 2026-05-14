# NJNavigator — NJ/NYC Transit Assistant

A conversational transit assistant for the New York–New Jersey metro corridor.
Ask a natural-language question about your commute and get a real-time, data-backed answer with a structured trip plan rendered directly in the terminal.

> **FE524: Prompt Engineering Lab for Business Applications**
> Stevens Institute of Technology | Spring 2026 | Instructor: Edward Loeser

---

## How It Works

```
User query (natural language)
         │
         ▼
smolagents ToolCallingAgent  (GPT-4o-mini)
         │  builds context-aware prompt from conversation history (last 3 turns)
         │  calls tools via FastMCP server (localhost:8000)
         ▼
┌─────────────────────────────────────────────────────┐
│  MCP Tools (9 total)                                │
│                                                     │
│  Knowledge                                          │
│  • search_transit_knowledge ──► ChromaDB RAG        │
│                                                     │
│  Schedule (static)                                  │
│  • search_stops         ──► SQLite GTFS             │
│  • get_departures        ──► SQLite GTFS            │
│  • get_transfers         ──► SQLite GTFS            │
│  • get_interchange       ──► hardcoded interchange  │
│  • geocode_address       ──► Nominatim OSM          │
│                                                     │
│  Real-time                                          │
│  • get_realtime_status   ──► GTFS-RT feeds          │
│  • get_service_alerts    ──► MTA Alerts feed        │
│                                                     │
│  Utility                                            │
│  • get_current_time                                 │
└─────────────────────────────────────────────────────┘
         │
         ▼
  Structured trip plan rendered with Rich
  (Summary panel · Legs table · Alerts · Notes)
```

1. **Static GTFS** — MTA Subway, PATH, NJ Transit Rail, NJ Transit Bus (routes 100–199) loaded into SQLite at setup
2. **GTFS-Realtime** — MTA delay feeds and PATH arrival feed polled live per query
3. **RAG knowledge base** — ChromaDB index of route descriptions and station summaries, built from the GTFS data itself (first run only)
4. **Agent** — reasons step-by-step over tool results using chain-of-thought prompting and few-shot examples, then renders a structured answer

---

## Tech Stack

| Layer | Tool |
|-------|------|
| Agent framework | `smolagents` — ToolCallingAgent |
| LLM | OpenAI `gpt-4o-mini` via `OpenAIServerModel` |
| MCP server | `FastMCP` (streamable-HTTP on localhost:8000) |
| Vector store | `ChromaDB` via `langchain-chroma` |
| Embeddings | OpenAI `text-embedding-3-small` |
| Schedule DB | SQLite — populated from GTFS ZIPs via `gtfs_kit` |
| RT feeds | `gtfs-realtime-bindings` + `protobuf` |
| Terminal UI | `rich` — panels, tables, spinners |
| Geocoding | Nominatim (OpenStreetMap) — no API key required |

---

## Project Structure

```
NJNavigator-FE524/
├── main.py                        # entry point — chat loop, rich renderer, RAG check
├── requirements.txt
├── .env                           # OPENAI_API_KEY
├── data/
│   ├── transit.db                 # SQLite — all GTFS static data
│   ├── chroma_db/                 # ChromaDB vector index (built on first run)
│   └── zip/                       # downloaded GTFS ZIPs (mta, path, njt, njtbus)
└── src/
    ├── agent.py                   # ToolCallingAgent + system prompt (CoT + few-shot)
    ├── mcp_server.py              # FastMCP server — all 9 MCP tools
    └── utils/
        ├── setup.py               # downloads GTFS ZIPs → transit.db (run once)
        ├── rt_loader.py           # MTA delays, MTA alerts, PATH arrivals (GTFS-RT)
        └── rag_builder.py         # builds / loads ChromaDB index from GTFS data
```

---

## Data Sources

### Static GTFS — downloaded once by `src/utils/setup.py`

| Agency | Feed URL |
|--------|----------|
| MTA Subway | `https://rrgtfsfeeds.s3.amazonaws.com/gtfs_supplemented.zip` |
| PATH Train | `https://data.trilliumtransit.com/gtfs/path-nj-us/path-nj-us.zip` |
| NJ Transit Rail | `https://www.njtransit.com/rail_data.zip` |
| NJ Transit Bus (routes 100–199) | `https://www.njtransit.com/bus_data.zip` |

Loaded tables per agency: `stops`, `routes`, `trips`, `stop_times`, `calendar`, `calendar_dates`, `transfers`

### GTFS-Realtime — polled live per query

| Feed | URL |
|------|-----|
| MTA TripUpdates (7 sub-feeds) | `https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct/gtfs*` |
| MTA Service Alerts | `https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys/all-alerts` |
| PATH Arrivals | `https://path.transitdata.nyc/gtfsrt` |

Requires `MTA_API_KEY` in `.env` for MTA feeds. PATH feed is unauthenticated.

> NJ Transit realtime is out of scope — NJT legs use static scheduled times only.

### RAG Knowledge Base — built from GTFS data on first run

| Document type | Count | Source |
|---------------|-------|--------|
| Route descriptions | 123 | `{agency}_routes` — route name + description |
| Station summaries | 509 | MTA/PATH parent stations + their serving lines |
| Interchange hubs | 10 | Hardcoded cross-agency transfer points |
| **Total** | **642** | |

---

## Setup

### 1. Clone and create virtual environment

```bash
git clone <repo-url>
cd NJNavigator-FE524
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Or with `uv`:
```bash
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
```

### 2. Configure API keys

```bash
# .env
OPENAI_API_KEY=sk-...
MTA_API_KEY=...          # optional — get free at https://api.mta.info
```

### 3. Run

```bash
python main.py
```

On first launch `main.py` automatically:
1. Downloads GTFS ZIPs and builds `data/transit.db` (if missing)
2. Builds the ChromaDB knowledge index — **one-time, ~30 s** (if missing)
3. Starts the MCP server in the background
4. Opens the interactive chat loop

Every subsequent run skips steps 1–2 and starts in ~2 seconds.

---

## Example Queries

```
You: I want to go from 31 Hopkins Ave, Jersey City to NYC
You: How do I get from Princeton to Penn Station?
You: Is the A train delayed right now?
You: What lines serve Times Square?
You: Next PATH train from Journal Square to 33rd Street
You: I need to be at Times Square by 9am, leaving from Hoboken
```

Follow-up questions work too — the last 3 exchanges are kept as context:
```
You: how do I get from Princeton to NYC?
   → [trip plan]
You: what if I leave an hour later?
   → [adjusted plan using prior context]
```

---

## Terminal Output

Each answer is rendered as structured Rich panels:

```
╭────────────────── Trip Summary ──────────────────╮
│  Origin: Princeton                               │
│  Destination: New York Penn Station              │
│  Departs: 5:25 AM   Arrives: ~6:50 AM           │
│  Total time: ~85 min   Transfers: 1              │
╰──────────────────────────────────────────────────╯
┌─────┬───────────────────┬──────────────┬─────────────────────┬────────────┐
│ Leg │ Route             │ Board        │ Alight              │ Travel     │
├─────┼───────────────────┼──────────────┼─────────────────────┼────────────┤
│ 1   │ Princeton Shuttle │ Princeton    │ Princeton Jct.      │ ~5 min     │
│ 2   │ Northeast Corridor│ Princeton Jct│ New York Penn Sta.  │ ~65 min    │
└─────┴───────────────────┴──────────────┴─────────────────────┴────────────┘
╭──────────────────── Notes ───────────────────────╮
│  No delays on NEC. Next NEC train at 5:56 AM.   │
╰──────────────────────────────────────────────────╯
```

---

## Known Limitations

- NJ Transit realtime delays not available (NJT developer portal registration required)
- PATH realtime returns next-arrival times only — no full-trip delay tracking
- MTA bus, LIRR, Metro-North, and NJT Light Rail are out of scope
- Cross-agency routing covers the 10 hardcoded interchange stations in `mcp_server.py`
- GTFS static data may expire — the calendar fallback in `active_services()` handles this gracefully

---

## License

For academic use only — Stevens Institute of Technology, FE524, Spring 2026.
