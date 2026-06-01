"""
Calendar Agent entry point.

Usage:
    python main.py            # process up to 50 inbox emails
    python main.py --max 100  # process up to 100 emails
"""

import sys
import argparse
from dotenv import load_dotenv

# Fix Windows terminal encoding so emoji in email subjects don't crash
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

from agent.runner import run, run_dedup

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calendar Agent — syncs Gmail events to Google Calendar")
    parser.add_argument("--max", type=int, default=50, help="Max emails to process per run (default: 50)")
    parser.add_argument("--dedup", action="store_true", help="Scan calendar and remove duplicate events, then exit")
    args = parser.parse_args()

    if args.dedup:
        run_dedup()
    else:
        run(max_emails=args.max)
