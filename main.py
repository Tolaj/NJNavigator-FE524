"""main.py — NJNavigator entry point"""
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH  = BASE_DIR / "data" / "transit.db"


def kill_port(port: int) -> None:
    try:
        pids = subprocess.run(["lsof", "-ti", f"tcp:{port}"], capture_output=True, text=True).stdout.split()
        for pid in pids:
            subprocess.run(["kill", "-9", pid], check=False)
        if pids:
            time.sleep(0.5)
    except Exception:
        pass


def main():
    # Run setup if DB is missing
    if not DB_PATH.exists():
        print("transit.db not found — running setup.py ...")
        subprocess.run([sys.executable, str(BASE_DIR / "src" / "utils" / "setup.py")], check=True)

    # Kill any leftover process on port 8000 then start MCP server
    kill_port(8000)
    proc = subprocess.Popen([sys.executable, str(BASE_DIR / "src" / "mcp_server.py")])
    time.sleep(2)
    print("[OK] MCP server running")

    # Build agent
    from src.agent import build_agent
    agent, mcp_client = build_agent()
    print("[OK] Agent ready\n")

    # Chat loop
    try:
        while True:
            question = input("You: ").strip()
            if not question or question.lower() in {"exit", "quit"}:
                break
            answer = agent.run(question)
            print(f"\nNJNavigator: {answer}\n")
    finally:
        mcp_client.__exit__(None, None, None)
        proc.terminate()


if __name__ == "__main__":
    main()
