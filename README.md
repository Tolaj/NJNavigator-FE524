# NJNavigator — NJ/NYC Transit Assistant

A conversational transit assistant for the New York–New Jersey metro corridor.
Ask a natural-language question about your commute and get a real-time, data-backed answer.

> **FE524: Prompt Engineering Lab for Business Applications**
> Stevens Institute of Technology | Spring 2026 | Instructor: Edward Loeser

---

## How It Works

```
User query (natural language)
         │
         ▼
smolagents ToolCallingAgent  (GPT-4o-mini)
         │
         │  calls tools via FastMCP server
         ▼
┌──────────────────────────────────────┐
│  MCP Tools                           │
│  • search_stops                      │
│  • get_departures  ──► SQLite GTFS   │
│  • get_realtime_status ──► GTFS-RT   │
│  • search_knowledge ──► ChromaDB RAG │
│  • get_interchange_stations          │
└──────────────────────────────────────┘
         │
         ▼
  Plain-English trip recommendation
```

1. **Static GTFS** (MTA Subway, PATH, NJ Transit) loaded into SQLite at setup
2. **GTFS-Realtime** feeds polled live per query for MTA delays and PATH arrivals
3. **RAG knowledge base** (ChromaDB) loaded at startup with Wikipedia transit articles + live MTA service alerts
4. **Agent** reasons over tool results and synthesises a recommendation

---

## Tech Stack

| Layer | Tool |
|-------|------|
| Agent framework | `smolagents` — ToolCallingAgent |
| LLM | OpenAI `gpt-4o-mini` via `OpenAIServerModel` |
| MCP server | `FastMCP` (HTTP on localhost:8000) |
| Vector store | `ChromaDB` via `langchain-chroma` |
| Embeddings | OpenAI `text-embedding-3-small` |
| Schedule DB | SQLite (populated from GTFS ZIPs) |
| RT feeds | `gtfs-realtime-bindings` + `protobuf` |

---

## Project Structure

```
NJNavigator-FE524/
├── agent.py                  # ToolCallingAgent + RAG tool definition
├── main.py                   # entry point — starts server, builds RAG, chat loop
├── evaluate.py               # prompt strategy evaluation harness
├── requirements.txt
├── .env.example
├── data/
│   └── interchange.csv       # hand-mapped cross-agency transfer points
└── src/
    ├── loaders/
    │   ├── static_loader.py  # downloads GTFS ZIPs → SQLite (run once)
    │   └── rt_loader.py      # MTA delays, MTA alerts, PATH arrivals
    ├── rag/
    │   ├── ingest.py         # Wikipedia + MTA alerts → ChromaDB
    │   └── retrieve.py       # semantic search helpers
    └── mcp_server.py         # FastMCP server with 5 tools
```

---

## Data Sources

### Static GTFS (downloaded once by `static_loader.py`)

| Agency | URL |
|--------|-----|
| MTA Subway (supplemented) | `https://rrgtfsfeeds.s3.amazonaws.com/gtfs_supplemented.zip` |
| PATH Train | `https://data.trilliumtransit.com/gtfs/path-nj-us/path-nj-us.zip` |
| NJ Transit Rail | `https://www.njtransit.com/rail_data.zip` |

### GTFS-Realtime (polled live per query)

| Feed | URL |
|------|-----|
| MTA TripUpdates (8 feeds) | `https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct/gtfs*` |
| MTA Service Alerts | `https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys/all-alerts` |
| PATH Arrivals | `https://path.transitdata.nyc/gtfsrt` |

> NJ Transit realtime is out of scope — NJT legs use scheduled times only.

### RAG Knowledge Base (built at startup)

- Wikipedia: NYC Subway, PATH, NJ Transit, MTA, GTFS
- Live MTA service alerts (refreshed each run)

---

## Setup

### 1. Clone and create virtual environment

```bash
git clone <repo-url>
cd NJNavigator-FE524
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

### 2. Configure API key

```bash
cp .env.example .env
# Add your OpenAI API key to .env
```

### 3. Load static GTFS data (run once)

```bash
python src/loaders/static_loader.py
```

This downloads ~35 MB of GTFS ZIPs and builds `data/transit.db`.

### 4. Run the assistant

```bash
python main.py
```

`main.py` automatically:
- Starts the MCP server in the background
- Fetches live MTA alerts and builds the ChromaDB knowledge base
- Opens an interactive chat loop

---

## Example Queries

```
You: best way from Hoboken to Penn Station at 6pm today?
You: is the A train delayed right now?
You: how many lines does PATH have?
You: next NJT train from Hoboken to New York?
```

---

## Known Limitations

- NJ Transit realtime delays not available (no developer portal access)
- PATH realtime has no trip continuity — next-arrival only, not full trip tracking
- MTA bus, LIRR, Metro-North, NJT Light Rail out of scope
- Cross-agency trip routing limited to the 5 interchange stations in `data/interchange.csv`

---

## License

For academic use only — Stevens Institute of Technology, FE524, Spring 2026.
