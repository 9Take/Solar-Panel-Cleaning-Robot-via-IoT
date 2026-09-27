"""Send a command through the gateway's queue (the same path as the dashboard).

    python -m app.cmd start
    python -m app.cmd stop
    python -m app.cmd return
    python -m app.cmd reset
    python -m app.cmd cycles 3

Requires the gateway (`python -m app`) to be running; it applies all safety rules.
"""

from __future__ import annotations

import argparse
import time

from app import command_queue
from app.commander import COMMANDS
from app.config import Settings
from app.history import HistoryStore

WAIT_S = 15.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a command to the robot via the gateway")
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("value", nargs="?", help="value for setpoints, e.g. cycles 3")
    args = parser.parse_args()

    store = HistoryStore(Settings().history_db)
    command_id = command_queue.enqueue(store, args.command, args.value, source="cli")
    print(f"Queued #{command_id}: {args.command}{' ' + args.value if args.value else ''} ... ", end="", flush=True)

    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        row = command_queue.get(store, command_id)
        if row["status"] in command_queue.FINAL:
            print(f"{row['status']}: {row['result']}")
            raise SystemExit(0 if row["status"] == "done" else 1)
        time.sleep(0.2)
    print("no answer - is the gateway (python -m app) running? The request will expire unsent.")
    raise SystemExit(2)


if __name__ == "__main__":
    main()
