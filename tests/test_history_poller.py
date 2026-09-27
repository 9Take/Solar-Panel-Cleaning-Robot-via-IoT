import asyncio
import sqlite3
from pathlib import Path

import pytest

from app.history import HistoryStore
from app.plc_client import PlcClient, PlcOfflineError, PlcReadError
from app.poller import Poller
from app.robot import Alarm, State, describe
from app.sim.runner import SimRunner
from app.sim.server import MockPlcServer
from app.tags import load_tags

UNIT = 1
REPO_TAGS = load_tags(Path(__file__).resolve().parents[1] / "config" / "plc_tags.yaml")


class FakeClock:
    def __init__(self, now=1_700_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


class FakePlc:
    """Stands in for PlcClient: returns queued results in order."""

    def __init__(self, *results):
        self.results = list(results)

    async def read_all(self):
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return dict(result)


def base_values(**overrides):
    values = {"robot_state": State.HOME, "alarm_code": 0, "estop_ok": True, "mode_switch": False,
              "battery_pct": 90}
    values.update(overrides)
    return values


def run_polls(poller, clock, count, step=1.0):
    async def main():
        for _ in range(count):
            await poller.poll_once()
            clock.now += step
    asyncio.run(main())


# --- HistoryStore -------------------------------------------------------------

def test_store_round_trip(tmp_path):
    store = HistoryStore(tmp_path / "sub" / "gateway.db")     # creates parent dir
    store.set_latest(10.0, True, {"a": 1})
    store.set_latest(11.0, False, None)                       # offline keeps last values
    assert store.latest() == {"ts": 11.0, "online": False, "values": {"a": 1}}

    store.add_snapshot(10.0, {"battery_pct": 90})
    store.add_snapshot(20.0, {"battery_pct": 89})
    assert store.snapshots(since_ts=15) == [(20.0, {"battery_pct": 89})]

    store.add_event(12.0, "change", "robot_state: Home -> Cleaning", tag="robot_state", old=3, new=1)
    assert store.events() == [{"ts": 12.0, "kind": "change", "tag": "robot_state",
                               "old": "3", "new": "1", "message": "robot_state: Home -> Cleaning"}]
    store.close()


def test_store_prune_and_wal(tmp_path):
    store = HistoryStore(tmp_path / "gateway.db")
    for ts in (1.0, 2.0, 3.0):
        store.add_snapshot(ts, {})
    assert store.prune_snapshots(older_than_ts=2.5) == 2
    assert [ts for ts, _ in store.snapshots()] == [3.0]
    assert store.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

    # A second connection (like the dashboard) can read while the gateway holds its own.
    reader = sqlite3.connect(tmp_path / "gateway.db")
    assert reader.execute("SELECT json_extract(data, '$') FROM snapshots").fetchone()[0] == "{}"
    reader.close()
    store.close()


def test_describe():
    assert describe(State, 3) == "Home"
    assert describe(Alarm, 5) == "Heartbeat Lost"
    assert describe(State, 9) == "unknown(9)"


# --- Poller (with a fake PLC) ------------------------------------------------------

@pytest.fixture
def store():
    s = HistoryStore(":memory:")
    yield s
    s.close()


def test_first_poll_records_initial_state(store):
    clock = FakeClock()
    poller = Poller(FakePlc(base_values()), store, clock=clock)
    run_polls(poller, clock, 1)
    messages = [e["message"] for e in store.events()]
    assert "robot_state: Home" in messages
    assert "alarm_code: None" in messages
    assert not [e for e in store.events() if e["kind"] == "online"], "no 'online' event on a clean start"
    assert store.latest()["values"]["battery_pct"] == 90


def test_changes_become_events_only_when_they_change(store):
    clock = FakeClock()
    poller = Poller(FakePlc(base_values(), base_values(battery_pct=89),
                            base_values(robot_state=State.CLEANING),
                            base_values(robot_state=State.ALARM, alarm_code=Alarm.ESTOP, estop_ok=False)),
                    store, clock=clock)
    run_polls(poller, clock, 4)
    changes = [e["message"] for e in store.events() if e["kind"] == "change"][4:]  # skip initial state
    assert changes == [
        "robot_state: Home -> Cleaning",
        "robot_state: Cleaning -> Alarm",
        "alarm_code: None -> Estop",
        "estop_ok: released -> PRESSED",
    ]


def test_snapshot_interval(store):
    clock = FakeClock()
    poller = Poller(FakePlc(*[base_values()] * 25), store, snapshot_interval_s=10, clock=clock)
    run_polls(poller, clock, 25, step=1.0)       # t = 0..24 s
    assert [ts - 1_700_000_000.0 for ts, _ in store.snapshots()] == [0, 10, 20]


def test_offline_and_back_online(store):
    clock = FakeClock()
    poller = Poller(FakePlc(base_values(), PlcOfflineError("refused"), PlcOfflineError("refused"),
                            base_values()), store, clock=clock)
    run_polls(poller, clock, 4)
    kinds = [e["kind"] for e in store.events() if e["kind"] in ("online", "offline")]
    assert kinds == ["offline", "online"], "one event per transition, not per poll"
    assert store.latest()["online"] is True


def test_offline_at_startup_is_recorded(store):
    clock = FakeClock()
    poller = Poller(FakePlc(PlcOfflineError("refused")), store, clock=clock)
    run_polls(poller, clock, 1)
    assert [e["kind"] for e in store.events()] == ["offline"]
    assert store.latest() == {"ts": clock.now - 1, "online": False, "values": None}


def test_read_error_recorded_once(store):
    clock = FakeClock()
    err = PlcReadError("PLC rejected read of ghost: Modbus exception 2")
    poller = Poller(FakePlc(err, err, err), store, clock=clock)
    run_polls(poller, clock, 3)
    assert [e["kind"] for e in store.events()] == ["read_error"]


def test_old_snapshots_pruned(store):
    clock = FakeClock()
    store.add_snapshot(clock.now - 31 * 86400, {"old": True})
    store.add_snapshot(clock.now - 29 * 86400, {"recent": True})
    poller = Poller(FakePlc(base_values()), store, retention_days=30, clock=clock)
    run_polls(poller, clock, 1)
    assert [data for _, data in store.snapshots()] == [{"recent": True}, base_values()]


# --- End to end: mock PLC + simulation + real client --------------------------------

def test_poller_against_simulated_robot(tmp_path):
    async def main():
        server = MockPlcServer(REPO_TAGS, UNIT, "127.0.0.1", 0)
        await server.serve_forever(background=True)
        runner = SimRunner(server.core, REPO_TAGS, UNIT)
        await runner.start_defaults()
        plc = PlcClient(REPO_TAGS, "127.0.0.1", server.bound_port, UNIT, timeout_s=1)
        store = HistoryStore(tmp_path / "gateway.db")
        poller = Poller(plc, store)
        try:
            for _ in range(15):                     # boot: wait for fake Pi heartbeat
                await runner.tick(0.2)
            await poller.poll_once()

            runner.plant.press_button()             # operator presses Start on the robot
            for _ in range(3):
                await runner.tick(0.2)
            await poller.poll_once()

            runner.plant.press_estop()
            await runner.tick(0.2)
            await poller.poll_once()
        finally:
            plc.close()
            await server.shutdown()

        messages = [e["message"] for e in store.events()]
        assert "robot_state: Home -> Cleaning" in messages
        assert "robot_state: Cleaning -> Alarm" in messages
        assert "estop_ok: released -> PRESSED" in messages
        latest = store.latest()
        assert latest["online"] and latest["values"]["robot_state"] == State.ALARM
        store.close()

    asyncio.run(main())
