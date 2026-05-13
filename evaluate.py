# evaluate.py
# Python 3.12.7
#
# FE524 evaluation harness.
# Compares 4 prompting strategies internally for the project report.
# The user never sees or chooses strategies — this is purely for grading analysis.
#
# Run: python evaluate.py

import re
import sys
from datetime import datetime
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma
from smolagents import CodeAgent, OpenAIServerModel, tool
from smolagents.mcp_client import MCPClient
from agent import vector_store  # reuse the already-loaded vector store

load_dotenv(".env", override=True)

MCP_URL = {"url": "http://localhost:8000/mcp", "transport": "streamable-http"}

# ── Ground-truth dataset ──────────────────────────────────────────────────────

GROUND_TRUTH = [
    {
        "id": "GT-01",
        "question": "When should I leave Times Square to reach Atlantic Av by 9:30am?",
        "expected_routes": ["A", "C"],
        "expected_time": "09:08",
        "transfers": 0,
        "no_service": False,
    },
    {
        "id": "GT-02",
        "question": "What subway from 14 St-Union Sq to Fulton St?",
        "expected_routes": ["4", "5"],
        "expected_time": "",
        "transfers": 0,
        "no_service": False,
    },
    {
        "id": "GT-03",
        "question": "How do I get from 59 St Columbus Circle to Jay St-MetroTech?",
        "expected_routes": ["A", "C"],
        "expected_time": "",
        "transfers": 0,
        "no_service": False,
    },
    {
        "id": "GT-04",
        "question": "Is there a direct subway from 125 St to Coney Island?",
        "expected_routes": ["A"],
        "expected_time": "",
        "transfers": 0,
        "no_service": False,
    },
    {
        "id": "GT-05",
        "question": "How do I get from Penn Station to JFK by subway?",
        "expected_routes": ["A", "E"],
        "expected_time": "",
        "transfers": 1,
        "no_service": False,
    },
    {
        "id": "GT-06",
        "question": "Is there a direct train from 34 St-Herald Sq to LaGuardia Airport?",
        "expected_routes": [],
        "expected_time": "",
        "transfers": 0,
        "no_service": True,
    },
    {
        "id": "GT-07",
        "question": "Morning rush trains from Grand Central to Wall St?",
        "expected_routes": ["4", "5"],
        "expected_time": "",
        "transfers": 0,
        "no_service": False,
    },
]

# ── Prompting strategies (internal — not user-facing) ────────────────────────
# The production agent (agent.py) uses CoT only.
# This file tests all four to justify that choice in the project report.


def make_system_prompt(strategy, now):
    base = (
        f"You are a transit assistant for NJ/NYC. Current time: {now}\n"
        "Never guess times or routes — always call the tools.\n"
    )

    if strategy == "direct":
        return base

    if strategy == "few_shot":
        return base + """
--- Example ---
User: How do I get from Times Square to Atlantic Av?
Action: search_stops("Times Sq") → stop_id "127"
Action: search_stops("Atlantic Av") → stop_id "A34"
Action: get_departures("127", after_time="09:00:00")
Action: get_service_alerts(route_filter="A,C")
Answer: Take the A at 09:04 toward Far Rockaway — arrives ~09:22. No active alerts.
--- End Example ---"""

    if strategy == "cot":
        return base + """
Step-by-step process:
1. If origin/destination/time unclear, ask ONE clarifying question first.
2. search_stops() for origin → stop_id.
3. search_stops() for destination → stop_id.
4. get_departures(origin_stop_id, after_time=HH:MM:SS).
5. get_service_alerts(route_filter=<routes from step 4>).
6. get_realtime_delays(origin_stop_id).
7. Give a plain-English recommendation with times, route, delays, and a fallback."""

    if strategy == "rag":
        return (
            base
            + """
- For background questions (what is PATH, which lines serve JFK): use search_transit_knowledge first.
- For live trip planning: use search_stops → get_departures → get_service_alerts → get_realtime_delays."""
        )

    return base


@tool
def search_transit_knowledge(query: str) -> str:
    """
    Search background knowledge about NJ/NYC transit systems.
    Use for general questions, not for live schedules.

    Args:
        query: What you want to know.

    Returns:
        Relevant text from transit Wikipedia articles.
    """
    docs = vector_store.similarity_search(query, k=4)
    return "\n\n".join(
        f"[{i}] ({doc.metadata.get('source', '?')})\n{doc.page_content}"
        for i, doc in enumerate(docs)
    )


def build_eval_agent(strategy):
    now = datetime.now().strftime("%A %B %d %Y, %H:%M")
    with MCPClient([MCP_URL], structured_output=False) as mcp_tools:
        return CodeAgent(
            tools=[*mcp_tools, search_transit_knowledge],
            model=OpenAIServerModel(model_id="gpt-4o"),
            system_prompt=make_system_prompt(strategy, now),
            add_base_tools=False,
            additional_authorized_imports=["json"],
        )


# ── Scoring ───────────────────────────────────────────────────────────────────


def score_answer(answer, query):
    a = answer.lower()
    scores = {}

    if not query["no_service"] and query["expected_routes"]:
        scores["route_correct"] = all(r.lower() in a for r in query["expected_routes"])

    if query["expected_time"]:
        h, m = map(int, query["expected_time"].split(":"))
        target = h * 60 + m
        found = re.findall(r"\b(\d{1,2}):(\d{2})\b", answer)
        scores["departure_accurate"] = any(
            abs(int(fh) * 60 + int(fm) - target) <= 3 for fh, fm in found
        )

    mentions_transfer = any(w in a for w in ["transfer", "change trains", "switch"])
    if not query["no_service"]:
        scores["transfer_valid"] = (
            mentions_transfer if query["transfers"] > 0 else not mentions_transfer
        )

    if query["no_service"]:
        scores["hallucination_ok"] = any(
            s in a
            for s in [
                "no direct",
                "no service",
                "no subway",
                "not available",
                "cannot",
                "doesn't go",
                "does not go",
                "no train",
            ]
        )

    scores["mentions_alerts"] = any(
        w in a for w in ["alert", "service change", "no active", "on schedule"]
    )
    scores["mentions_rt"] = any(
        w in a for w in ["delay", "real-time", "live", "on time", "on schedule"]
    )

    vals = [v for v in scores.values() if v is not None]
    scores["overall"] = sum(vals) / len(vals) if vals else 0.0
    return scores


def run_strategy(strategy):
    print(f"\n{'='*60}\n Strategy: {strategy.upper()}\n{'='*60}")
    agent = build_eval_agent(strategy)
    results = []
    for q in GROUND_TRUTH:
        print(f"  [{q['id']}] {q['question'][:60]}")
        try:
            answer = agent.run(f"Question: {q['question']}")
            s = score_answer(answer, q)
        except Exception as e:
            print(f"    ERROR: {e}")
            answer, s = f"ERROR: {e}", {"overall": 0.0}
        print(f"    overall: {s['overall']:.0%}")
        results.append((q, answer, s))
    return results


def print_summary(all_results):
    print(f"\n{'='*60}\n EVALUATION SUMMARY\n{'='*60}")
    print(
        f"{'Strategy':<12} {'Overall':>8}  {'Route':>6}  {'Xfer':>6}  {'Halluc':>8}  {'RT':>6}"
    )
    print("-" * 56)

    for strategy, results in all_results.items():
        scores_list = [s for _, _, s in results]
        n = len(scores_list)

        def pct(key):
            vals = [s[key] for s in scores_list if key in s and s[key] is not None]
            return f"{sum(vals)/len(vals):.0%}" if vals else " N/A"

        avg = sum(s["overall"] for s in scores_list) / n
        print(
            f"{strategy:<12} {avg:>8.0%}  {pct('route_correct'):>6}  "
            f"{pct('transfer_valid'):>6}  {pct('hallucination_ok'):>8}  {pct('mentions_rt'):>6}"
        )

    print("\nNote: expand GROUND_TRUTH to 25-30 queries for the final report.")


def main():
    strategies = ["direct", "few_shot", "cot", "rag"]
    all_results = {s: run_strategy(s) for s in strategies}
    print_summary(all_results)


if __name__ == "__main__":
    main()
