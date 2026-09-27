"""Timed cleaning runs (Auto mode), stored in the SQLite `schedules` table.

    schedule row:  at "08:00" (local time, SCHEDULE_TZ), days "mon,wed,fri",
                   cycles 2 (or NULL = keep the PLC's cycles_setpoint), enabled

Every CHECK_INTERVAL_S the scheduler looks for rows due today that have not run:

  due within GRACE_S   -> fire: Auto mode only, then `cycles` (if set) and `start`
                          through PlcCommander, so every step 5 safety rule applies
  due longer ago       -> missed (gateway or PLC was down): skipped, event recorded

A row runs at most once per day: last_run is set to today before firing, and a
refusal (Manual mode, not at Home, battery < 80 %, alarm, PLC offline) is logged
as an event and not retried. The table is re-read on every check, so edits from
the CLI (`python -m app.schedule`) or a future UI apply without a restart.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.commander import SETPOINTS, PlcCommander
from app.history import HistoryStore
from app.plc_client import PlcOfflineError, PlcReadError

log = logging.getLogger("gateway")

CHECK_INTERVAL_S = 5.0
GRACE_S = 60.0
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")    # index = datetime.weekday()
CYCLES_MIN, CYCLES_MAX = SETPOINTS["cycles"][1:]

SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,              -- HH:MM, local time (SCHEDULE_TZ)
    days       TEXT NOT NULL,              -- e.g. mon,tue,wed,thu,fri,sat,sun
    cycles     INTEGER,                    -- NULL = keep the PLC's cycles_setpoint
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_ts REAL NOT NULL,              -- Unix time
    last_run   TEXT                        -- local date (YYYY-MM-DD) of the last run/skip
);
"""


@dataclass(frozen=True)
class Schedule:
    id: int
    at: str
    days: tuple[str, ...]
    cycles: int | None
    enabled: bool
    created_ts: float
    last_run: str | None

    def describe(self) -> str:
        days = "every day" if len(self.days) == 7 else ",".join(self.days)
        cycles = f"{self.cycles} cycle(s)" if self.cycles else "PLC cycles_setpoint"
        return f"#{self.id} {self.at} {days}, {cycles}"


# --- table access (shared by the scheduler, the CLI and a future UI) -------------------

def ensure_schema(store: HistoryStore) -> None:
    store.db.executescript(SCHEMA)
    store.db.commit()


def parse_at(text: str) -> str:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text.strip())
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        raise ValueError(f"time must be HH:MM (00:00-23:59), got {text!r}")
    return f"{int(match[1]):02d}:{match[2]}"


def parse_days(text: str) -> tuple[str, ...]:
    if text.strip().lower() in ("", "daily", "all", "*"):
        return DAYS
    days = {d.strip().lower() for d in text.split(",") if d.strip()}
    unknown = days - set(DAYS)
    if unknown:
        raise ValueError(f"unknown day(s) {', '.join(sorted(unknown))}; use {','.join(DAYS)} or daily")
    return tuple(d for d in DAYS if d in days)


def check_cycles(cycles: int | None) -> int | None:
    if cycles is not None and not CYCLES_MIN <= cycles <= CYCLES_MAX:
        raise ValueError(f"cycles must be {CYCLES_MIN}-{CYCLES_MAX}, got {cycles}")
    return cycles


def add(store: HistoryStore, at: str, days: str = "daily", cycles: int | None = None,
        now: float | None = None) -> int:
    ensure_schema(store)
    cur = store.db.execute(
        "INSERT INTO schedules (at, days, cycles, created_ts) VALUES (?, ?, ?, ?)",
        (parse_at(at), ",".join(parse_days(days)), check_cycles(cycles), time.time() if now is None else now))
    store.db.commit()
    return cur.lastrowid


def remove(store: HistoryStore, schedule_id: int) -> bool:
    ensure_schema(store)
    deleted = store.db.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,)).rowcount
    store.db.commit()
    return deleted > 0


def set_enabled(store: HistoryStore, schedule_id: int, enabled: bool) -> bool:
    ensure_schema(store)
    changed = store.db.execute("UPDATE schedules SET enabled = ? WHERE id = ?",
                               (int(enabled), schedule_id)).rowcount
    store.db.commit()
    return changed > 0


def list_all(store: HistoryStore) -> list[Schedule]:
    ensure_schema(store)
    rows = store.db.execute(
        "SELECT id, at, days, cycles, enabled, created_ts, last_run FROM schedules ORDER BY at, id")
    return [Schedule(r[0], r[1], tuple(r[2].split(",")), r[3], bool(r[4]), r[5], r[6]) for r in rows]


# --- scheduler -------------------------------------------------------------------------

class Scheduler:
    def __init__(
        self,
        store: HistoryStore,
        commander: PlcCommander,
        tz: ZoneInfo,
        grace_s: float = GRACE_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        ensure_schema(store)
        self.store, self.commander, self.tz = store, commander, tz
        self.grace_s, self.clock = grace_s, clock

    async def run_forever(self, interval_s: float = CHECK_INTERVAL_S) -> None:
        while True:
            await self.check_once()
            await asyncio.sleep(interval_s)

    async def check_once(self) -> None:
        now_ts = self.clock()
        now = datetime.fromtimestamp(now_ts, self.tz)
        today = now.date()
        for entry in list_all(self.store):
            due = self._due(entry, today)
            if not entry.enabled or due is None or now < due or entry.last_run == today.isoformat():
                continue
            self._mark_run(entry.id, today)                  # before firing: once per day, no retries
            if entry.created_ts > due.timestamp():
                continue                                     # added after today's time: starts tomorrow
            late_s = (now - due).total_seconds()
            if late_s >= self.grace_s:
                self._event(f"{entry.describe()} missed: due {entry.at}, gateway checked "
                            f"{late_s / 60:.0f} min late (gateway or PLC was down); skipped", "missed")
                continue
            await self._fire(entry)

    def _due(self, entry: Schedule, day: date) -> datetime | None:
        if DAYS[day.weekday()] not in entry.days:
            return None
        hour, minute = map(int, entry.at.split(":"))
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=self.tz)

    async def _fire(self, entry: Schedule) -> None:
        try:
            manual = await self.commander.plc.read("mode_switch")
        except (PlcOfflineError, PlcReadError) as exc:
            self._event(f"{entry.describe()} skipped: cannot read the mode switch ({exc})", "skipped")
            return
        if manual:
            self._event(f"{entry.describe()} skipped: mode switch is on Manual (schedule runs in Auto only)",
                        "skipped")
            return
        if entry.cycles is not None:
            result = await self.commander.execute("cycles", entry.cycles)
            if not result.ok:
                self._event(f"{entry.describe()} skipped: setting cycles failed ({result.message})", "skipped")
                return
        result = await self.commander.execute("start")
        if result.ok:
            self._event(f"{entry.describe()} started: {result.message}", "started")
        else:
            self._event(f"{entry.describe()} skipped: start {result.status} ({result.message})", "skipped")

    def _mark_run(self, schedule_id: int, day: date) -> None:
        self.store.db.execute("UPDATE schedules SET last_run = ? WHERE id = ?", (day.isoformat(), schedule_id))
        self.store.db.commit()

    def _event(self, message: str, outcome: str) -> None:
        (log.info if outcome == "started" else log.warning)("Schedule %s", message)
        self.store.add_event(self.clock(), "schedule", message, new=outcome)


def next_due(entry: Schedule, tz: ZoneInfo, now: datetime) -> datetime | None:
    """Next time this entry will fire (for display), or None if disabled."""
    if not entry.enabled:
        return None
    hour, minute = map(int, entry.at.split(":"))
    for offset in range(8):
        day = (now + timedelta(days=offset)).date()
        due = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
        if DAYS[day.weekday()] in entry.days and due > now and entry.last_run != day.isoformat():
            return due
    return None


def main() -> None:
    """`python -m app.schedule list | add HH:MM [--days ...] [--cycles N] | remove ID | enable ID | disable ID`"""
    import argparse

    from app.config import Settings

    parser = argparse.ArgumentParser(description="Manage timed cleaning runs (Auto mode)")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="show all schedules")
    add_p = sub.add_parser("add", help="add a run, e.g. add 08:00 --days mon,wed,fri --cycles 2")
    add_p.add_argument("at", help="HH:MM local time")
    add_p.add_argument("--days", default="daily", help=f"comma list of {','.join(DAYS)}, or daily (default)")
    add_p.add_argument("--cycles", type=int, help=f"{CYCLES_MIN}-{CYCLES_MAX}; omit to keep the PLC setpoint")
    for action in ("remove", "enable", "disable"):
        sub.add_parser(action).add_argument("id", type=int)
    args = parser.parse_args()

    settings = Settings()
    tz = ZoneInfo(settings.schedule_tz)
    store = HistoryStore(settings.history_db)
    try:
        if args.action == "add":
            try:
                new_id = add(store, args.at, args.days, args.cycles)
            except ValueError as exc:
                raise SystemExit(f"Error: {exc}") from None
            print(f"Added #{new_id}")
        elif args.action in ("remove", "enable", "disable"):
            done = (remove(store, args.id) if args.action == "remove"
                    else set_enabled(store, args.id, args.action == "enable"))
            if not done:
                raise SystemExit(f"Error: no schedule #{args.id}")
            print(f"{args.action.capitalize()}d #{args.id}")
        now = datetime.now(tz)
        entries = list_all(store)
        print(f"Schedules ({settings.schedule_tz}, now {now:%a %H:%M}):" if entries else "No schedules.")
        for entry in entries:
            nxt = next_due(entry, tz, now)
            state = f"next {nxt:%a %d %b %H:%M}" if nxt else ("disabled" if not entry.enabled else "-")
            print(f"  {entry.describe():<45} {state}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
