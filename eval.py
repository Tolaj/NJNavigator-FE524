"""
eval.py — NJNavigator Evaluation
Queries transit.db for ground truth, runs the agent, compares the text answer.
Usage:  python eval.py
"""
import re, sys, json, sqlite3, subprocess, time
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
DB_PATH  = BASE_DIR / "data" / "transit.db"
sys.path.insert(0, str(BASE_DIR / "src"))

# ── Ground truth: pull first departure directly from transit.db ───────────────

def gt_departure(agency, stop_id, after="08:00:00", toward=""):
    """Return the first scheduled departure time (HH:MM:SS) from transit.db."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    today = datetime.now().strftime("%Y%m%d")
    dow   = datetime.now().strftime("%A").lower()

    # Expand parent station → child platform stops (PATH/MTA only)
    if agency in ("path", "mta"):
        row = conn.execute(f"SELECT location_type FROM {agency}_stops WHERE stop_id=?", (stop_id,)).fetchone()
        if row and row["location_type"] == 1:
            kids = [r[0] for r in conn.execute(
                f"SELECT stop_id FROM {agency}_stops WHERE parent_station=? AND (location_type IS NULL OR location_type!=1)", (stop_id,)
            ).fetchall()]
            if kids:
                stop_id = kids  # list of platform stop_ids
    if isinstance(stop_id, str):
        stop_id = [stop_id]

    # Active services
    active = set()
    try:
        active = {r[0] for r in conn.execute(
            f"SELECT service_id FROM {agency}_calendar WHERE {dow}=1 AND start_date<=? AND end_date>=?", (today, today)
        ).fetchall()}
    except Exception:
        pass
    if not active:
        ph = ",".join(f"'{s}'" for s in stop_id)
        active = {r[0] for r in conn.execute(
            f"SELECT DISTINCT t.service_id FROM {agency}_stop_times s JOIN {agency}_trips t ON s.trip_id=t.trip_id WHERE s.stop_id IN ({ph}) LIMIT 20"
        ).fetchall()}

    svc_ph  = ",".join(f"'{s}'" for s in active)
    stop_ph = ",".join(f"'{s}'" for s in stop_id)
    rows = conn.execute(f"""
        SELECT st.departure_time, t.trip_headsign
        FROM   {agency}_stop_times st
        JOIN   {agency}_trips t ON st.trip_id=t.trip_id
        WHERE  st.stop_id IN ({stop_ph}) AND t.service_id IN ({svc_ph})
          AND  st.departure_time >= ?
        ORDER  BY st.departure_time LIMIT 10
    """, (after,)).fetchall()
    conn.close()

    if toward:
        rows = [r for r in rows if toward.upper() in (r["trip_headsign"] or "").upper()] or rows
    return rows[0]["departure_time"] if rows else None


# ── Scoring helpers ────────────────────────────────────────────────────────────

def to_mins(t):
    t = t.strip()
    m = re.match(r'^(\d{1,2}):(\d{2})(?::\d{2})?$', t)
    if m: return int(m.group(1))*60 + int(m.group(2))
    m = re.match(r'^(\d{1,2}):(\d{2})\s*(AM|PM)$', t, re.I)
    if m:
        h, mn, ap = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        if ap == "PM" and h != 12: h += 12
        if ap == "AM" and h == 12: h = 0
        return h*60 + mn
    return None

def time_close(gt_time, answer, tolerance=3):
    """True if any time mentioned in answer is within tolerance minutes of gt_time."""
    gt = to_mins(gt_time)
    if gt is None: return None
    for t in re.findall(r'\d{1,2}:\d{2}(?:\s*[AaPp][Mm])?', answer):
        v = to_mins(t)
        if v is not None and min(abs(v-gt), 24*60-abs(v-gt)) <= tolerance:
            return True
    return False

def contains(keywords, answer):
    """True if any keyword appears in the answer (case-insensitive)."""
    low = answer.lower()
    return any(k.lower() in low for k in keywords)


# ── Test cases ─────────────────────────────────────────────────────────────────
# Each case: query sent to agent + what to verify in the answer.
# gt_*  fields drive the DB lookup for departure time ground truth.
# check fields are verified against the agent's text response.

CASES = [
    # ── Single-leg PATH ────────────────────────────────────────────────────────
    {   "query":   "Next PATH train from Journal Square to 33rd Street at 8am",
        "gt":      ("path","26731","08:00:00","33RD"),
        "route":   ["PATH","33rd"],  "transfer": None, "refuse": None },
    {   "query":   "Next PATH train from Journal Square to World Trade Center at 8am",
        "gt":      ("path","26731","08:00:00","WORLD TRADE"),
        "route":   ["PATH","World Trade"],  "transfer": None, "refuse": None },
    {   "query":   "Next PATH train from Hoboken to 33rd Street at 8am",
        "gt":      ("path","26730","08:00:00","33RD"),
        "route":   ["PATH","33rd"],  "transfer": None, "refuse": None },
    {   "query":   "Next PATH train from Newark to World Trade Center at 8am",
        "gt":      ("path","26733","08:00:00","WORLD TRADE"),
        "route":   ["PATH","World Trade"],  "transfer": None, "refuse": None },

    # ── Single-leg NJT Rail ────────────────────────────────────────────────────
    {   "query":   "NJ Transit train from Princeton Junction to New York Penn Station at 8am",
        "gt":      ("njt","125","08:00:00","NEW YORK"),
        "route":   ["Northeast Corridor","NEC"],  "transfer": None, "refuse": None },
    {   "query":   "NJ Transit train from Hoboken to New York Penn Station at 8am",
        "gt":      ("njt","63","08:00:00","NEW YORK"),
        "route":   ["NJ Transit","NJT"],  "transfer": None, "refuse": None },

    # ── Multi-leg with transfer ────────────────────────────────────────────────
    {   "query":   "How do I get from Princeton to New York Penn Station at 8am?",
        "gt":      ("njt","124","08:00:00","PRINCETON JCT"),
        "route":   ["Princeton Shuttle","shuttle"],  "transfer": "Princeton Junction", "refuse": None },
    {   "query":   "How do I get from Hoboken Terminal to Times Square by 9am?",
        "gt":      ("path","26730","08:00:00","33RD"),
        "route":   ["PATH"],  "transfer": "33rd", "refuse": None },

    # ── Hallucination: agent must refuse these ─────────────────────────────────
    {   "query":   "Can I take the PATH train from Trenton to Manhattan?",
        "gt":      None,
        "route":   None, "transfer": None,
        "refuse":  ["not serve","does not","no PATH","PATH does not"] },
    {   "query":   "How do I take the NYC Subway from Princeton to Times Square?",
        "gt":      None,
        "route":   None, "transfer": None,
        "refuse":  ["not serve","does not","NJ Transit","cannot"] },
    {   "query":   "Can I take a PATH train from JFK to Newark?",
        "gt":      None,
        "route":   None, "transfer": None,
        "refuse":  ["not serve","does not","PATH does not"] },

    # ── RAG knowledge ──────────────────────────────────────────────────────────
    {   "query":   "What subway lines serve Times Square?",
        "gt":      None,
        "route":   ["1","2","3","N","Q","R","W"],  "transfer": None, "refuse": None },
    {   "query":   "Which PATH station is near Wall Street?",
        "gt":      None,
        "route":   ["World Trade","WTC"],  "transfer": None, "refuse": None },
    {   "query":   "What is the Northeast Corridor rail line?",
        "gt":      None,
        "route":   ["Northeast Corridor","Penn Station"],  "transfer": None, "refuse": None },
]


# ── Runner ─────────────────────────────────────────────────────────────────────

def run():
    if not DB_PATH.exists():
        sys.exit("Run 'python main.py' first to build the database.")

    from rich.console import Console
    from rich.table import Table
    from rich import box
    console = Console()

    # Start MCP server
    proc = subprocess.Popen(
        [sys.executable, str(BASE_DIR / "src" / "mcp_server.py")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(2)
    from agent import build_agent
    agent, mcp_client = build_agent()

    results = []
    try:
        for i, case in enumerate(CASES, 1):
            console.print(f"[dim]#{i:02d}[/dim] {case['query'][:70]}")
            with console.status("  thinking...", spinner="dots"):
                try:
                    answer = str(agent.run(case["query"]))
                except Exception as e:
                    answer = f"ERROR: {e}"

            # Score
            gt_time   = gt_departure(*case["gt"]) if case["gt"] else None
            route_ok  = contains(case["route"], answer)  if case["route"]   else None
            time_ok   = time_close(gt_time, answer)      if gt_time         else None
            xfer_ok   = contains([case["transfer"]], answer) if case["transfer"] else None
            refuse_ok = contains(case["refuse"], answer) if case["refuse"]  else None

            scores = {k: v for k, v in
                      [("route", route_ok), ("time", time_ok),
                       ("transfer", xfer_ok), ("refuse", refuse_ok)]
                      if v is not None}
            passed = sum(v for v in scores.values())
            total  = len(scores)
            pct    = passed / total if total else 0

            results.append({"id": i, "query": case["query"], "gt_time": gt_time,
                             "scores": scores, "pct": pct, "answer": answer})

            color = "green" if pct >= 0.7 else "yellow" if pct >= 0.4 else "red"
            checks = " ".join(f"{k}={'✓' if v else '✗'}" for k, v in scores.items())
            console.print(f"   [{color}]{pct*100:.0f}%[/{color}]  {checks}")
    finally:
        mcp_client.__exit__(None, None, None)
        proc.terminate()

    # Results table
    t = Table(box=box.ROUNDED, expand=True, header_style="bold white on dark_blue")
    t.add_column("#",        width=3)
    t.add_column("Query",    ratio=4)
    t.add_column("GT time",  width=9)
    t.add_column("Route",    width=7, justify="center")
    t.add_column("Time",     width=7, justify="center")
    t.add_column("Transfer", width=9, justify="center")
    t.add_column("Refuse",   width=8, justify="center")
    t.add_column("Score",    width=7, justify="right")

    def cell(v):
        if v is True:  return "[green]✓[/green]"
        if v is False: return "[red]✗[/red]"
        return "[dim]—[/dim]"

    for r in results:
        s = r["scores"]
        c = "green" if r["pct"] >= 0.7 else "yellow" if r["pct"] >= 0.4 else "red"
        t.add_row(str(r["id"]), r["query"][:55], r["gt_time"] or "—",
                  cell(s.get("route")), cell(s.get("time")),
                  cell(s.get("transfer")), cell(s.get("refuse")),
                  f"[{c}]{r['pct']*100:.0f}%[/{c}]")
    console.print(t)

    overall = sum(r["pct"] for r in results) / len(results)
    passed  = sum(1 for r in results if r["pct"] >= 0.7)
    color   = "green" if overall >= 0.7 else "yellow"
    console.print(f"\nOverall: [{color}]{overall*100:.1f}%[/{color}]  ({passed}/{len(results)} passed ≥70%)\n")

    with open(BASE_DIR / "eval_report.json", "w") as f:
        json.dump([{"id": r["id"], "query": r["query"], "gt_time": r["gt_time"],
                    "scores": r["scores"], "pct": round(r["pct"], 2)} for r in results], f, indent=2)
    console.print(f"[dim]Report saved → eval_report.json[/dim]")


if __name__ == "__main__":
    run()
