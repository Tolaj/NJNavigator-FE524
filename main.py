# main.py
#
# NJNavigator entry point.
# Starts the MCP server as a background process, builds the RAG knowledge base,
# then runs an interactive chat loop.
#
# Usage:
#   python main.py
#
# First-time setup (run once before main.py):
#   python src/loaders/static_loader.py

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.theme import Theme

_theme = Theme(
    {
        "info": "bold cyan",
        "success": "bold green",
        "warn": "bold yellow",
        "error": "bold red",
    }
)
_console = Console(theme=_theme, highlight=False)

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "transit.db"


def check_prerequisites() -> bool:
    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set. Add it to your .env file.")
        return False
    if not DB_PATH.exists():
        print("ERROR: transit.db not found.")
        print("Run this first:  python src/loaders/static_loader.py")
        return False
    return True


def _kill_port(port: int) -> None:
    """Kill any process already listening on the given port."""
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"], capture_output=True, text=True
        )
        pids = result.stdout.strip().split()
        for pid in pids:
            try:
                subprocess.run(["kill", "-9", pid], check=False)
            except Exception:
                pass
        if pids:
            time.sleep(0.5)
    except Exception:
        pass


def start_mcp_server() -> subprocess.Popen:
    print("[Startup] Starting MCP server ...")
    _kill_port(8000)  # clear any leftover process from a previous run
    proc = subprocess.Popen(
        [sys.executable, str(BASE_DIR / "src" / "mcp_server.py")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)  # give it time to bind to port 8000
    print("[Startup] MCP server ready on http://localhost:8000/mcp")
    return proc


def build_rag() -> object:
    from src.loaders.rt_loader import get_mta_alerts
    from src.rag.ingest import build_vectorstore

    print("[Startup] Fetching live MTA alerts ...")
    try:
        alerts = get_mta_alerts()
        print(f"  {len(alerts)} active alerts fetched")
    except Exception as e:
        print(f"  Alert fetch failed ({e}) — continuing without live alerts")
        alerts = []

    return build_vectorstore(alerts)


_STREET_RE = re.compile(
    r"\b\d+\s+(?:[A-Za-z]+\s+){1,4}(?:Ave(?:nue)?|St(?:reet)?|Rd|Road|Blvd|Boulevard|Dr(?:ive)?|Ln|Lane|Way|Pl(?:ace)?|Ct|Court|Pkwy)\b",
    re.IGNORECASE,
)


def _extract_address(question: str, m: re.Match) -> str:
    """Extract a fuller address string by grabbing context after the street match."""
    # Take from match start to up to 40 chars after (captures city like "Jersey City, NJ")
    # but stop at trip-direction words like "to", "toward", "->", "and get to"
    tail = question[m.start() :]
    # cut at transition words that introduce the destination
    cut = re.search(r"\s+(?:to\b|toward|->|and\s+get)", tail, re.IGNORECASE)
    addr = tail[: cut.start()].strip() if cut else tail.strip()
    # append NJ/NY region hint if no state is mentioned
    if not re.search(r"\b(?:NJ|NY|New\s+Jersey|New\s+York)\b", addr, re.IGNORECASE):
        addr += ", NJ, USA"
    return addr


def enrich_with_geocode(question: str) -> str:
    """If the question contains a street address, pre-resolve it to nearby stops."""
    m = _STREET_RE.search(question)
    if not m:
        return question
    try:
        from src.mcp_server import geocode_address

        address = _extract_address(question, m)
        raw = json.loads(geocode_address(address))
        if "nearest_stops" not in raw:
            return question
        stops_str = "; ".join(
            f"{s['stop_name']} ({s['agency']}, {s['distance_miles']} mi away, stop_id={s['stop_id']})"
            for s in raw["nearest_stops"]
        )
        return (
            f"{question}\n\n"
            f"[System note: the origin address was geocoded to lat={raw['lat']}, lon={raw['lon']}. "
            f"Nearest transit stops: {stops_str}. "
            f"Use the closest stop as the origin — do NOT call geocode_address or search_stops for the origin.]"
        )
    except Exception:
        return question


def main():
    print("=" * 52)
    print("  NJNavigator — NJ/NYC Transit Assistant")
    print("  MTA Subway · PATH Train · NJ Transit Rail")
    print("=" * 52 + "\n")

    if not check_prerequisites():
        sys.exit(1)

    mcp_proc = start_mcp_server()

    try:
        vectorstore = build_rag()

        import agent as agent_module

        agent_module.set_vectorstore(vectorstore)
        ag, mcp_client = agent_module.build_agent()

        print("Ready. Ask me anything about your commute.\n")

        history: list[tuple[str, str]] = []  # [(user_question, assistant_answer), ...]
        MAX_HISTORY = 3  # keep last 3 exchanges as context

        def build_prompt(question: str) -> str:
            enriched = enrich_with_geocode(question)
            if not history:
                return f"Question: {enriched}"
            ctx = "\n".join(
                f"User: {q}\nAssistant: {a}" for q, a in history[-MAX_HISTORY:]
            )
            return (
                f"Conversation so far:\n{ctx}\n\n"
                f"User follow-up: {enriched}\n"
                f"Answer the follow-up using context from the conversation above."
            )

        try:
            while True:
                try:
                    question = input("You: ").strip()
                except (KeyboardInterrupt, EOFError):
                    print("\nGoodbye!")
                    break

                if not question:
                    continue
                if question.lower() in {"exit", "quit", "q"}:
                    print("Goodbye!")
                    break

                print()
                try:
                    prompt = build_prompt(question)
                    answer = ag.run(prompt)
                    history.append((question, str(answer)))
                    print(f"NJNavigator: {answer}\n")
                except Exception as e:
                    print(f"NJNavigator: Sorry, something went wrong — {e}\n")
        finally:
            mcp_client.__exit__(None, None, None)

    finally:
        mcp_proc.terminate()


if __name__ == "__main__":
    main()
