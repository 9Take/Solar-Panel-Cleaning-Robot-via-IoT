"""Command queue in SQLite: how the dashboard (and the CLI) send commands.

    client  : INSERT INTO commands (ts, command, value, source) VALUES (...)
    gateway : picks up pending rows every CHECK_INTERVAL_S, runs PlcCommander,
              writes status/result back, and logs an audit event

Rows older than EXPIRY_S when picked up are marked `expired` and never sent,
so a stale "start" cannot fire when the gateway comes back after being down.
Stop commands are taken first and run without waiting for other commands.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from app.commander import PlcCommander
from app.history import HistoryStore

log = logging.getLogger("gateway")

CHECK_INTERVAL_S = 0.2
EXPIRY_S = 5.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS commands (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL NOT NULL,              -- when requested (Unix time)
    command  TEXT NOT NULL,              -- start | stop | return | reset | cycles
    value    NUMERIC,                    -- for setpoints (cycles)
    source   TEXT NOT NULL DEFAULT '?',  -- dashboard | cli | ...
    status   TEXT NOT NULL DEFAULT 'pending',
    result   TEXT,
    done_ts  REAL
);
CREATE INDEX IF NOT EXISTS commands_status ON commands (status);
"""
FINAL = ("done", "rejected", "ignored", "not_acknowledged", "not_started", "error", "expired")


def ensure_schema(store: HistoryStore) -> None:
    store.db.executescript(SCHEMA)
    store.db.commit()


def enqueue(store: HistoryStore, command: str, value=None, source: str = "?", ts: float | None = None) -> int:
    ensure_schema(store)
    cur = store.db.execute("INSERT INTO commands (ts, command, value, source) VALUES (?, ?, ?, ?)",
                           (time.time() if ts is None else ts, command, value, source))
    store.db.commit()
    return cur.lastrowid


def get(store: HistoryStore, command_id: int) -> dict | None:
    row = store.db.execute("SELECT id, ts, command, value, source, status, result, done_ts "
                           "FROM commands WHERE id = ?", (command_id,)).fetchone()
    keys = ("id", "ts", "command", "value", "source", "status", "result", "done_ts")
    return dict(zip(keys, row)) if row else None


class CommandQueueWorker:
    def __init__(self, store: HistoryStore, commander: PlcCommander,
                 expiry_s: float = EXPIRY_S, clock: Callable[[], float] = time.time) -> None:
        ensure_schema(store)
        self.store, self.commander, self.expiry_s, self.clock = store, commander, expiry_s, clock
        self.tasks: set[asyncio.Task] = set()

    def _finish(self, row_id: int, command: str, source: str, status: str, result: str) -> None:
        now = self.clock()
        self.store.db.execute("UPDATE commands SET status = ?, result = ?, done_ts = ? WHERE id = ?",
                              (status, result, now, row_id))
        self.store.db.commit()
        message = f"{command} from {source}: {status} - {result}"
        (log.info if status == "done" else log.warning)("Command %s", message)
        self.store.add_event(now, "command", message, tag=command, new=status)

    async def check_once(self) -> None:
        rows = self.store.db.execute(
            "SELECT id, ts, command, value, source FROM commands WHERE status = 'pending' "
            "ORDER BY (command = 'stop') DESC, id").fetchall()
        for row_id, ts, command, value, source in rows:
            if self.clock() - ts > self.expiry_s:
                self._finish(row_id, command, source, "expired",
                             f"not sent: waited {self.clock() - ts:.0f}s (limit {self.expiry_s:.0f}s)")
                continue
            self.store.db.execute("UPDATE commands SET status = 'running' WHERE id = ?", (row_id,))
            self.store.db.commit()
            task = asyncio.create_task(self._run(row_id, command, value, source))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def _run(self, row_id: int, command: str, value, source: str) -> None:
        try:
            result = await self.commander.execute(command, value)
            self._finish(row_id, command, source, result.status, result.message)
        except Exception as exc:  # never leave a row stuck in 'running'
            log.exception("Command %s failed", command)
            self._finish(row_id, command, source, "error", f"internal error: {exc!r}")

    async def run_forever(self, interval_s: float = CHECK_INTERVAL_S) -> None:
        # Rows left 'running' by a crash are not retried (rule 8).
        self.store.db.execute("UPDATE commands SET status = 'error', result = 'gateway restarted while running' "
                              "WHERE status = 'running'")
        self.store.db.commit()
        while True:
            await self.check_once()
            await asyncio.sleep(interval_s)
