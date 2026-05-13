# agent.py
#
# NJNavigator — smolagents CodeAgent with RAG + MCP tools.
# The MCP server (src/mcp_server.py) must be running before calling build_agent().
# main.py handles starting the server automatically.

import os
from datetime import datetime

from dotenv import load_dotenv
from langchain_chroma import Chroma
from smolagents import OpenAIServerModel, ToolCallingAgent, tool
from smolagents.agents import EMPTY_PROMPT_TEMPLATES, PromptTemplates

load_dotenv()

MCP_URL = {"url": "http://localhost:8000/mcp", "transport": "streamable-http"}

# ── CoT system prompt ─────────────────────────────────────────────────────────
# Chain-of-thought is chosen because trip planning is a multi-step reasoning task:
# resolve stops → check interchange (cross-agency) → fetch schedule →
# check RT delays → check alerts → synthesise a recommendation.

SYSTEM_PROMPT = """\
You are NJNavigator, a transit assistant for the NJ/NYC metro corridor.
You help users plan trips on MTA Subway, PATH Train, and NJ Transit Rail.
Current date and time: {now}

Never guess stop names, times, or routes — always call the tools first.

When a user asks about a trip, follow these steps:
1. If the user gave a street address (not a stop name), call geocode_address() first
   to find the nearest transit stops to that location.
2. If no departure time is specified, call get_current_time() to get the live time
   and use its 'time' field as after_time when calling get_departures().
3. If origin or destination is still unclear, ask ONE clarifying question.
4. Call search_stops() for the origin (agency='all') if not already resolved.
5. Call search_stops() for the destination if not already resolved.
6. If origin and destination are on DIFFERENT agencies (e.g. NJT → MTA):
   a. Call get_interchange_stations(from_agency, to_agency) to find transfer points.
   b. Look up departures for each leg separately.
7. Call get_departures(stop_id, agency, after_time) for the origin stop.
8. Call get_realtime_status(routes) to check live delays and alerts.
9. Give the user a clear, plain-English recommendation:
   - Which train(s) to take, toward what direction
   - Departure and estimated arrival time
   - Any active delays or service alerts
   - A backup option if the first train is delayed

For general questions ("what is PATH", "how many lines does MTA have"):
  → call search_transit_knowledge() instead of the schedule tools.
"""

# ── RAG tool (defined here so it has access to the vectorstore) ───────────────

_vectorstore: Chroma | None = None


def set_vectorstore(vs: Chroma) -> None:
    global _vectorstore
    _vectorstore = vs


@tool
def search_transit_knowledge(query: str) -> str:
    """
    Search background knowledge about NJ/NYC transit systems.
    Covers MTA Subway, PATH Train, NJ Transit Rail, GTFS, and live service alerts.
    Use for general questions — NOT for live schedules (use the MCP tools for that).

    Args:
        query: What you want to know about transit.

    Returns:
        Relevant text excerpts from Wikipedia articles and MTA service alerts.
    """
    if _vectorstore is None:
        return "Knowledge base not ready."
    docs = _vectorstore.similarity_search(query, k=4)
    return "\n\n".join(f"[{i+1}] {doc.page_content}" for i, doc in enumerate(docs))


# ── Agent builder ─────────────────────────────────────────────────────────────


def build_agent() -> tuple["ToolCallingAgent", object]:
    """
    Connects to the running MCP server and returns (agent, mcp_client).
    The caller MUST call mcp_client.__exit__(None, None, None) when done
    to cleanly close the MCP connection.
    Call set_vectorstore() before calling this.
    """
    from smolagents.mcp_client import MCPClient

    now = datetime.now().strftime("%A %B %d %Y, %H:%M")

    mcp_client = MCPClient([MCP_URL], structured_output=False)
    mcp_client.__enter__()
    tools = mcp_client.get_tools()

    agent = ToolCallingAgent(
        tools=[*tools, search_transit_knowledge],
        model=OpenAIServerModel(model_id="gpt-4o-mini"),
        prompt_templates=PromptTemplates(
            **{**EMPTY_PROMPT_TEMPLATES, "system_prompt": SYSTEM_PROMPT.format(now=now)}
        ),
    )
    return agent, mcp_client
