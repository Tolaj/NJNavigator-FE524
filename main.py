"""main.py — NJNavigator entry point"""
import re
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich import box

load_dotenv()

BASE_DIR      = Path(__file__).resolve().parent
DB_PATH       = BASE_DIR / "data" / "transit.db"
CHROMA_DIR    = BASE_DIR / "data" / "chroma_db"
console       = Console()


def _ensure_rag_index() -> None:
    """Build the ChromaDB knowledge index if it hasn't been built yet."""
    import chromadb
    try:
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        col = client.get_or_create_collection("transit_knowledge")
        if col.count() > 0:
            return  # already built
    except Exception:
        pass
    console.print("[yellow]Building knowledge index (first run only) ...[/yellow]")
    sys.path.insert(0, str(BASE_DIR / "src"))
    from utils.rag_builder import build_index
    build_index()
    console.print("[green]Knowledge index ready.[/green]\n")


def kill_port(port: int) -> None:
    try:
        pids = subprocess.run(["lsof", "-ti", f"tcp:{port}"], capture_output=True, text=True).stdout.split()
        for pid in pids:
            subprocess.run(["kill", "-9", pid], check=False)
        if pids:
            time.sleep(0.5)
    except Exception:
        pass


def _extract_section(text: str, header: str) -> list[str]:
    """Return lines under a section header until the next all-caps header."""
    lines   = text.splitlines()
    capture = False
    result  = []
    for line in lines:
        stripped = line.strip()
        if stripped.upper() == header.upper():
            capture = True
            continue
        if capture:
            if re.match(r'^[A-Z]{3,}$', stripped):   # next section header
                break
            if stripped:
                result.append(stripped)
    return result


def _parse_legs(text: str) -> list[dict]:
    """Parse all LEG N | Route blocks from the LEGS section."""
    legs_text = ""
    lines     = text.splitlines()
    in_legs   = False
    for line in lines:
        if line.strip().upper() == "LEGS":
            in_legs = True
            continue
        if in_legs:
            if re.match(r'^[A-Z]{3,}$', line.strip()):
                break
            legs_text += line + "\n"

    legs    = []
    current = {}
    for line in legs_text.splitlines():
        m_header = re.match(r'(?i)leg\s*\d+\s*\|\s*(.+)', line.strip())
        if m_header:
            if current:
                legs.append(current)
            current = {"route": m_header.group(1).strip(), "board": "", "alight": "", "travel": ""}
            continue
        m_board  = re.match(r'(?i)board\s*:\s*(.+)', line.strip())
        m_alight = re.match(r'(?i)alight\s*:\s*(.+)', line.strip())
        m_travel = re.match(r'(?i)travel\s*:\s*(.+)', line.strip())
        if m_board  and current: current["board"]  = m_board.group(1).strip()
        if m_alight and current: current["alight"] = m_alight.group(1).strip()
        if m_travel and current: current["travel"] = m_travel.group(1).strip()
    if current:
        legs.append(current)
    return legs


def render_response(text: str) -> None:
    """Render a structured NJNavigator response using rich."""

    # ── Summary panel ─────────────────────────────────────────────────────────
    summary_lines = _extract_section(text, "SUMMARY")
    if summary_lines:
        summary_text = "\n".join(f"  {l}" for l in summary_lines)
        console.print(Panel(
            summary_text,
            title="[bold cyan]Trip Summary[/bold cyan]",
            border_style="cyan",
            padding=(0, 1),
        ))
    else:
        # No structured format found — fall back to plain markdown rendering
        console.print(Panel(text, title="[bold cyan]NJNavigator[/bold cyan]", border_style="cyan"))
        return

    # ── Legs table ────────────────────────────────────────────────────────────
    legs = _parse_legs(text)
    if legs:
        table = Table(
            box=box.ROUNDED,
            show_header=True,
            header_style="bold white on dark_blue",
            border_style="blue",
            expand=True,
        )
        table.add_column("Leg",    style="bold yellow", no_wrap=True, width=4)
        table.add_column("Route",  style="bold white",  ratio=3)
        table.add_column("Board",  style="green",       ratio=4)
        table.add_column("Alight", style="bright_cyan", ratio=4)
        table.add_column("Travel", style="magenta",     no_wrap=True, width=10)

        for i, leg in enumerate(legs, 1):
            table.add_row(
                str(i),
                leg["route"],
                leg["board"],
                leg["alight"],
                leg["travel"],
            )
        console.print(table)

    # ── Alerts ────────────────────────────────────────────────────────────────
    alerts = _extract_section(text, "ALERTS")
    if alerts and not (len(alerts) == 1 and alerts[0].lower() == "none"):
        alert_text = "\n".join(f"  [yellow]⚠[/yellow]  {a.lstrip('•- ')}" for a in alerts)
        console.print(Panel(
            alert_text,
            title="[bold yellow]Service Alerts[/bold yellow]",
            border_style="yellow",
            padding=(0, 1),
        ))

    # ── Notes ─────────────────────────────────────────────────────────────────
    notes = _extract_section(text, "NOTES")
    if notes:
        notes_text = "  " + "  ".join(notes)
        console.print(Panel(
            notes_text,
            title="[bold green]Notes[/bold green]",
            border_style="green",
            padding=(0, 1),
        ))


def main():
    if not DB_PATH.exists():
        console.print("[yellow]transit.db not found — running setup.py ...[/yellow]")
        subprocess.run([sys.executable, str(BASE_DIR / "src" / "utils" / "setup.py")], check=True)

    _ensure_rag_index()
    kill_port(8000)
    proc = subprocess.Popen(
        [sys.executable, str(BASE_DIR / "src" / "mcp_server.py")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)

    from src.agent import build_agent
    agent, mcp_client = build_agent()

    console.print(Rule("[bold cyan]NJNavigator — NJ/NYC Transit Assistant[/bold cyan]", style="cyan"))
    console.print("[dim]Type your trip question. Type [bold]exit[/bold] to quit.[/dim]\n")

    history: list[tuple[str, str]] = []
    MAX_HISTORY = 3

    def build_prompt(question: str) -> str:
        if not history:
            return question
        ctx = "\n".join(f"User: {q}\nAssistant: {a}" for q, a in history[-MAX_HISTORY:])
        return (
            f"Conversation so far:\n{ctx}\n\n"
            f"User follow-up: {question}\n"
            f"Answer the follow-up using context from the conversation above."
        )

    try:
        while True:
            console.print("[bold cyan]You:[/bold cyan] ", end="")
            try:
                question = input().strip()
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]Goodbye![/dim]")
                break

            if not question:
                continue
            if question.lower() in {"exit", "quit", "q"}:
                console.print("[dim]Goodbye![/dim]")
                break

            console.print()
            with console.status("[cyan]Planning your trip...[/cyan]", spinner="dots"):
                answer = str(agent.run(build_prompt(question)))

            history.append((question, answer))

            console.print()
            render_response(answer)
            console.print()
    finally:
        mcp_client.__exit__(None, None, None)
        proc.terminate()


if __name__ == "__main__":
    main()
