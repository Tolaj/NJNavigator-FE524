"""
rag_builder.py — Build ChromaDB index from GTFS route and station data.

Run once:  python src/utils/rag_builder.py
"""
import sqlite3
from pathlib import Path

from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document

DB_PATH      = Path(__file__).resolve().parents[2] / "data" / "transit.db"
CHROMA_DIR   = Path(__file__).resolve().parents[2] / "data" / "chroma_db"
COLLECTION   = "transit_knowledge"


def _route_docs(conn: sqlite3.Connection) -> list[Document]:
    docs = []

    # MTA — has rich route_desc
    for row in conn.execute(
        "SELECT route_short_name, route_long_name, route_desc FROM mta_routes"
    ).fetchall():
        short, long_, desc = row
        text = (
            f"MTA Subway Line {short} — {long_}.\n"
            f"{desc or 'No description available.'}"
        )
        docs.append(Document(page_content=text,
                             metadata={"agency": "MTA", "type": "route", "route": short}))

    # PATH
    for row in conn.execute(
        "SELECT route_long_name, route_desc FROM path_routes"
    ).fetchall():
        long_, desc = row
        text = f"PATH Train — {long_}.\n{desc or ''}"
        docs.append(Document(page_content=text,
                             metadata={"agency": "PATH", "type": "route"}))

    # NJT Rail
    for row in conn.execute(
        "SELECT route_short_name, route_long_name FROM njt_routes"
    ).fetchall():
        short, long_ = row
        text = f"NJ Transit Rail — {long_} ({short}). Connects NJ stations to New York Penn Station."
        docs.append(Document(page_content=text,
                             metadata={"agency": "NJT", "type": "route", "route": short}))

    # NJT Bus (100-199 NYC-bound routes)
    for row in conn.execute(
        "SELECT route_short_name, route_long_name FROM njtbus_routes"
    ).fetchall():
        short, long_ = row
        name = long_ or f"Route {short}"
        text = f"NJ Transit Bus Route {short} — {name}. NYC-bound bus service from New Jersey."
        docs.append(Document(page_content=text,
                             metadata={"agency": "NJTBUS", "type": "route", "route": str(short)}))

    return docs


def _station_docs(conn: sqlite3.Connection) -> list[Document]:
    """Build one document per major MTA station listing all routes that serve it."""
    docs = []
    # Only parent stations (location_type=1) — these are the named hubs
    stations = conn.execute(
        "SELECT stop_id, stop_name FROM mta_stops WHERE location_type=1"
    ).fetchall()

    for stop_id, stop_name in stations:
        routes = conn.execute("""
            SELECT DISTINCT r.route_short_name
            FROM mta_stop_times st
            JOIN mta_trips t   ON st.trip_id  = t.trip_id
            JOIN mta_routes r  ON t.route_id  = r.route_id
            JOIN mta_stops  s  ON st.stop_id  = s.stop_id
            WHERE s.parent_station = ? OR s.stop_id = ?
        """, (stop_id, stop_id)).fetchall()

        if not routes:
            continue
        route_list = ", ".join(r[0] for r in routes)
        text = (
            f"MTA Station: {stop_name}.\n"
            f"Served by lines: {route_list}."
        )
        docs.append(Document(page_content=text,
                             metadata={"agency": "MTA", "type": "station",
                                       "stop_id": stop_id, "stop_name": stop_name}))

    # PATH stations
    for row in conn.execute(
        "SELECT stop_id, stop_name FROM path_stops WHERE location_type=1"
    ).fetchall():
        stop_id, stop_name = row
        routes = conn.execute("""
            SELECT DISTINCT r.route_long_name
            FROM path_stop_times st
            JOIN path_trips t  ON st.trip_id = t.trip_id
            JOIN path_routes r ON t.route_id = r.route_id
            JOIN path_stops  s ON st.stop_id = s.stop_id
            WHERE s.parent_station = ? OR s.stop_id = ?
        """, (stop_id, stop_id)).fetchall()
        route_list = ", ".join(r[0] for r in routes) if routes else "PATH service"
        text = f"PATH Station: {stop_name}.\nServed by: {route_list}."
        docs.append(Document(page_content=text,
                             metadata={"agency": "PATH", "type": "station", "stop_name": stop_name}))

    return docs


def _interchange_docs() -> list[Document]:
    """Static documents about cross-agency transfer points."""
    entries = [
        ("World Trade Center", "MTA E train + PATH Newark/Hoboken trains. Lower Manhattan hub."),
        ("33rd Street", "MTA B/D/F/M/N/Q/R/W + PATH Journal Square/Newark trains. Midtown hub."),
        ("New York Penn Station", "NJ Transit Rail (all lines) + MTA 1/2/3/A/C/E. Main NJ–NYC gateway."),
        ("Port Authority Bus Terminal", "NJ Transit Bus (all routes) + MTA A/C/E subway. 8th Ave & 42nd St."),
        ("Hoboken Terminal", "PATH (33rd St / WTC) + NJ Transit Rail (multiple lines) + NJ Transit Bus."),
        ("Newark Penn Station", "PATH (WTC) + NJ Transit Rail (NEC, Morris & Essex) + NJ Transit Bus."),
        ("Journal Square", "PATH trains to 33rd St / WTC + NJ Transit Bus routes."),
        ("Harrison", "PATH (Newark–WTC) station. Transfer point near Newark."),
        ("Grove Street", "PATH station between Journal Square and Exchange Place, Jersey City."),
        ("Exchange Place", "PATH station, Jersey City. Near WTC-bound PATH trains."),
    ]
    return [
        Document(
            page_content=f"Transfer hub: {name}.\n{desc}",
            metadata={"type": "interchange", "station": name},
        )
        for name, desc in entries
    ]


def build_index() -> Chroma:
    print("Building RAG index ...")
    conn = conn = sqlite3.connect(DB_PATH)

    docs = []
    print("  collecting route documents ...")
    docs += _route_docs(conn)
    print(f"    {len(docs)} route docs")

    station_docs = _station_docs(conn)
    docs += station_docs
    print(f"    {len(station_docs)} station docs")

    interchange_docs = _interchange_docs()
    docs += interchange_docs
    print(f"    {len(interchange_docs)} interchange docs")
    conn.close()

    print(f"  total documents: {len(docs)}")
    print("  embedding with OpenAI text-embedding-3-small ...")

    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    db = Chroma.from_documents(
        documents=docs,
        embedding=embeddings,
        persist_directory=str(CHROMA_DIR),
        collection_name=COLLECTION,
    )
    print(f"Done — index saved to {CHROMA_DIR}")
    return db


def load_index() -> Chroma:
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    return Chroma(
        persist_directory=str(CHROMA_DIR),
        embedding_function=embeddings,
        collection_name=COLLECTION,
    )


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    build_index()
