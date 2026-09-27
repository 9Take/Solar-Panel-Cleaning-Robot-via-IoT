"""Step 7: timed cleaning runs (table helpers, scheduler rules, and a run on the mock PLC)."""

import asyncio
import contextlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import schedule
from app.commander import CommandResult, PlcCommander
from app.history import HistoryStore
from app.plc_client import PlcClient, PlcOfflineError
from app.robot import State
from app.schedule import Scheduler
from app.sim.plant import Plant, PlantParams
from app.sim.runner import SimRunner
from app.sim.server import MockPlcServer
from app.tags import load_tags

TZ = ZoneInfo("Asia/Bangkok")
TAGS = load_tags(Path(__file__).resolve().parents[1] / "config" / "plc_tags.yaml")


def ts(text: str) -> float:
    """'2026-09-28 08:00:30' Bangkok time -> Unix time. 2026-09-28 is a Monday."""
    return datetime.fromisoformat(text).replace(tzinfo=TZ).timestamp()


CREATED = ts("2026-09-27 12:00:00")


@pytest.fixture
def store(tmp_path):
    s = HistoryStore(tmp_path / "gateway.db")
    yield s
    s.close()


class FakePlc:
    def __init__(self, manual=False, offline=False):
        self.manual, self.offline = manual, offline

    async def read(self, name):
        assert name == "mode_switch"
        if self.offline:
            raise PlcOfflineError("cannot connect")
        return self.manual


class FakeCommander:
    def __init__(self, plc=None, results=None):
        self.plc = plc or FakePlc()
        self.results = results or {}
        self.calls = []

    async def execute(self, command, value=None):
        self.calls.append((command, value))
        status, message = self.results.get(command, ("done", "ok"))
        return CommandResult(command, status, message)


def run_check(store, commander, now_text):
    scheduler = Scheduler(store, commander, TZ, clock=lambda: ts(now_text))
    asyncio.run(scheduler.check_once())


def schedule_events(store):
    return [(e["new"], e["message"]) for e in store.events() if e["kind"] == "schedule"]


# --- table helpers ---------------------------------------------------------------------

def test_add_normalizes_and_lists(store):
    schedule.add(store, "8:00", "fri,mon", 2, now=CREATED)
    schedule.add(store, "07:30", "daily", now=CREATED)
    first, second = schedule.list_all(store)                 # ordered by time
    assert (first.at, first.days, first.cycles) == ("07:30", schedule.DAYS, None)
    assert (second.at, second.days, second.cycles) == ("08:00", ("mon", "fri"), 2)
    assert second.describe() == "#1 08:00 mon,fri, 2 cycle(s)"


@pytest.mark.parametrize("at, days, cycles, error", [
    ("24:00", "daily", None, "HH:MM"),
    ("8.00", "daily", None, "HH:MM"),
    ("08:60", "daily", None, "HH:MM"),
    ("08:00", "mon,funday", None, "funday"),
    ("08:00", "daily", 0, "1-100"),
    ("08:00", "daily", 101, "1-100"),
])
def test_add_rejects_bad_input(store, at, days, cycles, error):
    with pytest.raises(ValueError, match=error):
        schedule.add(store, at, days, cycles)


def test_remove_enable_disable(store):
    sid = schedule.add(store, "08:00")
    assert schedule.set_enabled(store, sid, False)
    assert not schedule.list_all(store)[0].enabled
    assert schedule.remove(store, sid)
    assert not schedule.remove(store, sid)
    assert not schedule.set_enabled(store, 999, True)


def test_next_due():
    entry = schedule.Schedule(1, "08:00", ("wed",), None, True, CREATED, None)
    now = datetime.fromisoformat("2026-09-28 09:00").replace(tzinfo=TZ)        # Monday
    assert schedule.next_due(entry, TZ, now) == datetime(2026, 9, 30, 8, 0, tzinfo=TZ)
    disabled = schedule.Schedule(1, "08:00", ("wed",), None, False, CREATED, None)
    assert schedule.next_due(disabled, TZ, now) is None


# --- scheduler rules -------------------------------------------------------------------

def test_fires_cycles_then_start_once(store):
    schedule.add(store, "08:00", "daily", 3, now=CREATED)
    commander = FakeCommander()
    run_check(store, commander, "2026-09-28 07:59:55")      # not yet
    assert commander.calls == []
    run_check(store, commander, "2026-09-28 08:00:03")
    run_check(store, commander, "2026-09-28 08:00:08")      # same day again: nothing
    assert commander.calls == [("cycles", 3), ("start", None)]
    assert schedule_events(store)[0][0] == "started"
    assert schedule.list_all(store)[0].last_run == "2026-09-28"


def test_without_cycles_keeps_plc_setpoint(store):
    schedule.add(store, "08:00", now=CREATED)
    commander = FakeCommander()
    run_check(store, commander, "2026-09-28 08:00:01")
    assert commander.calls == [("start", None)]


def test_runs_again_next_day(store):
    schedule.add(store, "08:00", now=CREATED)
    commander = FakeCommander()
    run_check(store, commander, "2026-09-28 08:00:01")
    run_check(store, commander, "2026-09-29 08:00:01")
    assert commander.calls == [("start", None), ("start", None)]


def test_other_weekday_and_disabled_do_not_fire(store):
    schedule.add(store, "08:00", "tue", now=CREATED)
    sid = schedule.add(store, "08:00", "daily", now=CREATED)
    schedule.set_enabled(store, sid, False)
    commander = FakeCommander()
    run_check(store, commander, "2026-09-28 08:00:01")       # Monday
    assert commander.calls == []


def test_missed_run_is_skipped_and_recorded(store):
    schedule.add(store, "08:00", now=CREATED)
    commander = FakeCommander()
    run_check(store, commander, "2026-09-28 08:20:00")      # gateway was down at 08:00
    run_check(store, commander, "2026-09-28 08:20:05")
    assert commander.calls == []
    events = schedule_events(store)
    assert len(events) == 1 and events[0][0] == "missed" and "20 min late" in events[0][1]


def test_added_after_todays_time_waits_for_tomorrow(store):
    schedule.add(store, "08:00", now=ts("2026-09-28 10:00:00"))
    commander = FakeCommander()
    run_check(store, commander, "2026-09-28 10:00:05")
    assert commander.calls == [] and schedule_events(store) == []
    run_check(store, commander, "2026-09-29 08:00:01")
    assert commander.calls == [("start", None)]


def test_manual_mode_skips_without_commands(store):
    schedule.add(store, "08:00", "daily", 2, now=CREATED)
    commander = FakeCommander(FakePlc(manual=True))
    run_check(store, commander, "2026-09-28 08:00:01")
    assert commander.calls == []
    assert schedule_events(store) == [("skipped", schedule_events(store)[0][1])]
    assert "Manual" in schedule_events(store)[0][1]


def test_plc_offline_skips_and_is_not_retried(store):
    schedule.add(store, "08:00", now=CREATED)
    commander = FakeCommander(FakePlc(offline=True))
    run_check(store, commander, "2026-09-28 08:00:01")
    commander.plc.offline = False
    run_check(store, commander, "2026-09-28 08:00:06")
    assert commander.calls == []
    assert "cannot read the mode switch" in schedule_events(store)[0][1]


def test_refused_start_is_logged_with_reason_and_not_retried(store):
    schedule.add(store, "08:00", now=CREATED)
    commander = FakeCommander(results={"start": ("not_started", "battery 70% is below 80%")})
    run_check(store, commander, "2026-09-28 08:00:01")
    run_check(store, commander, "2026-09-28 08:00:06")
    assert commander.calls == [("start", None)]
    outcome, message = schedule_events(store)[0]
    assert outcome == "skipped" and "battery 70% is below 80%" in message


def test_failed_cycles_setpoint_does_not_start(store):
    schedule.add(store, "08:00", "daily", 2, now=CREATED)
    commander = FakeCommander(results={"cycles": ("error", "read-back does not match 2")})
    run_check(store, commander, "2026-09-28 08:00:01")
    assert commander.calls == [("cycles", 2)]
    assert "setting cycles failed" in schedule_events(store)[0][1]


# --- on the mock PLC + simulated robot --------------------------------------------------

def test_schedule_starts_the_simulated_robot(store):
    async def main():
        server = MockPlcServer(TAGS, 1, "127.0.0.1", 0)
        await server.serve_forever(background=True)
        runner = SimRunner(server.core, TAGS, 1, plant=Plant(PlantParams(
            travel_s=2.0, heartbeat_period_s=0.1, drain_pct_per_s=0, solar_pct_per_s=0)))
        await runner.start_defaults(cycles_setpoint=1)
        for _ in range(5):
            await runner.tick(0.05)
        ticker = asyncio.create_task(runner.run_forever(0.05))
        plc = PlcClient(TAGS, "127.0.0.1", server.bound_port, 1, timeout_s=1)
        commander = PlcCommander(plc, ack_timeout_s=0.5, confirm_s=1.0, poll_s=0.05)
        try:
            schedule.add(store, "08:00", "daily", 4, now=CREATED)
            await Scheduler(store, commander, TZ, clock=lambda: ts("2026-09-28 08:00:02")).check_once()
            return await plc.read_many(["robot_state", "cycles_setpoint"])
        finally:
            ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ticker
            plc.close()
            await server.shutdown()

    values = asyncio.run(main())
    assert values == {"robot_state": State.CLEANING, "cycles_setpoint": 4}
    assert schedule_events(store)[0][0] == "started"
