"""Dashboard data access: SQLite only (latest, snapshots, events, commands, schedules).

No Streamlit here, so everything the page shows can be tested without a browser.
The dashboard never talks Modbus; the gateway (`python -m app`) owns the PLC link.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app import command_queue
from app.history import HistoryStore
from app.robot import Alarm, State, describe

# `latest` older than this means the gateway stopped updating it (not running / crashed).
STALE_S = 10.0
COMMAND_WAIT_S = 10.0

STATE_ICONS = {State.IDLE: "⏸️", State.CLEANING: "🧹", State.RETURNING: "↩️", State.HOME: "🏠", State.ALARM: "🚨"}


@dataclass(frozen=True)
class LiveStatus:
    gateway_alive: bool         # gateway updated `latest` within STALE_S
    plc_online: bool            # gateway's last attempt reached the PLC
    age_s: float | None         # seconds since the last update (None = never)
    values: dict | None         # last known tag values (None = never read)

    @property
    def headline(self) -> tuple[str, str]:
        """(icon, text) for the connection banner."""
        if self.age_s is None:
            return "⚪", "No data yet: is the gateway running?"
        if not self.gateway_alive:
            return "⚪", f"Gateway not updating (last update {self.age_s:.0f}s ago)"
        if not self.plc_online:
            return "🔴", "PLC offline (showing last known values)"
        return "🟢", "PLC online"


def live_status(store: HistoryStore, now: float | None = None) -> LiveStatus:
    row = store.latest()
    if row is None:
        return LiveStatus(False, False, None, None)
    age = (time.time() if now is None else now) - row["ts"]
    return LiveStatus(age < STALE_S, row["online"], age, row["values"])


def state_label(value) -> str:
    try:
        state = State(value)
    except (TypeError, ValueError):
        return f"❔ {value}"
    return f"{STATE_ICONS[state]} {describe(State, state)}"


def alarm_label(value) -> str:
    if value in (None, Alarm.NONE):
        return "✅ None"
    return f"⚠️ {describe(Alarm, value)}"


def battery_label(value) -> str:
    if value is None:
        return "–"
    icon = "🔋" if value >= 25 else "🪫"
    return f"{icon} {value:.0f} %"


def history(store: HistoryStore, since_ts: float, tags: list[str], tz: ZoneInfo) -> list[dict]:
    """Snapshot rows since since_ts: [{"time": datetime, tag: value, ...}]."""
    return [{"time": datetime.fromtimestamp(ts, tz), **{t: data.get(t) for t in tags}}
            for ts, data in store.snapshots(since_ts)]


def recent_events(store: HistoryStore, tz: ZoneInfo, limit: int = 100) -> list[dict]:
    rows = store.db.execute(
        "SELECT ts, kind, message FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"time": datetime.fromtimestamp(ts, tz).strftime("%d %b %H:%M:%S"), "kind": kind, "message": msg}
            for ts, kind, msg in rows]


def send_command(
    store: HistoryStore,
    command: str,
    value=None,
    wait_s: float = COMMAND_WAIT_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """Queue a command for the gateway and wait for its result.

    Returns the command row; status stays 'pending' if the gateway did not answer
    in time (the row then expires unsent, see app.command_queue).
    """
    command_id = command_queue.enqueue(store, command, value, source="dashboard")
    deadline = clock() + wait_s
    while True:
        row = command_queue.get(store, command_id)
        if row["status"] in command_queue.FINAL or clock() >= deadline:
            return row
        sleep(0.2)
