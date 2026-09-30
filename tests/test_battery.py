"""Step 6: battery feed + Pi heartbeat (fake PLC, and mock PLC + simulated ladder)."""

import asyncio
from pathlib import Path

import pytest

from app.battery import (BatteryFeeder, BatteryReading, battery_to_feed, fake_battery_source,
                         tuya_battery_source)
from app.config import Settings
from app.history import HistoryStore
from app.plc_client import PlcClient, PlcOfflineError
from app.robot import Alarm
from app.sim.ladder import LadderParams, LadderSim
from app.sim.plant import Plant, PlantParams
from app.sim.runner import SimRunner
from app.sim.server import MockPlcServer
from app.tags import load_tags
from app.tuya import TuyaError

UNIT = 1
TAGS = load_tags(Path(__file__).resolve().parents[1] / "config" / "plc_tags.yaml")


# --- policy: when may a Tuya reading be fed ---------------------------------------------

@pytest.mark.parametrize("pct, age, expected", [
    (87.0, 0, 87),
    (79.9, 0, 79),              # rounds down: never reaches the 80 % start threshold early
    (0.0, 0, 0),
    (100.0, 0, 100),
    (87.0, 300, 87),            # exactly at the age limit is still fresh
    (87.0, 300.1, None),        # stale
    (-1.0, 0, None),            # impossible values are not clamped
    (100.5, 0, None),
    (float("nan"), 0, None),
    (float("inf"), 0, None),
])
def test_battery_to_feed(pct, age, expected):
    assert battery_to_feed(BatteryReading(pct, at=1000.0), 1000.0 + age, 300.0) == expected


def test_battery_to_feed_without_reading():
    assert battery_to_feed(None, 1000.0, 300.0) is None


def test_default_policy_stops_heartbeat_when_reading_goes_stale():
    plc = FakePlc()
    clock = FakeClock()
    feeder = BatteryFeeder(plc, lambda: asyncio.sleep(0, 85.0), clock=clock, max_age_s=300)

    async def scenario():
        await feeder.refresh()
        await feeder.feed_once()
        clock.now = 301
        await feeder.feed_once()
    asyncio.run(scenario())
    assert plc.writes == [("battery_pct", 85), ("pi_heartbeat", 1)]


# --- feeder mechanics (stand-in policy) ------------------------------------------------

def feed_if_any(reading, now, max_age_s):
    """Stand-in policy for these tests: feed any reading, rounded."""
    return None if reading is None else round(reading.pct)


class FakePlc:
    def __init__(self):
        self.tags = TAGS
        self.writes = []
        self.offline = False

    async def _write(self, name, value):
        if self.offline:
            raise PlcOfflineError("cable unplugged")
        self.writes.append((name, value))


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make_feeder(plc, battery=85.0, store=None, clock=None, policy=feed_if_any):
    async def fetch():
        if isinstance(battery, Exception):
            raise battery
        return battery
    return BatteryFeeder(plc, fetch, store, policy=policy, clock=clock or FakeClock())


def test_no_reading_holds_heartbeat():
    plc = FakePlc()
    asyncio.run(make_feeder(plc).feed_once())
    assert plc.writes == []


def test_writes_battery_before_heartbeat_and_counts_up():
    plc = FakePlc()
    feeder = make_feeder(plc)

    async def scenario():
        await feeder.refresh()
        await feeder.feed_once()
        await feeder.feed_once()
    asyncio.run(scenario())
    assert plc.writes == [("battery_pct", 85), ("pi_heartbeat", 1), ("battery_pct", 85), ("pi_heartbeat", 2)]


def test_heartbeat_wraps_at_16_bits():
    plc = FakePlc()
    feeder = make_feeder(plc)
    feeder.heartbeat = 0xFFFF

    async def scenario():
        await feeder.refresh()
        await feeder.feed_once()
    asyncio.run(scenario())
    assert plc.writes[-1] == ("pi_heartbeat", 0)


def test_tuya_failure_keeps_last_reading_and_logs_once(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    plc = FakePlc()
    feeder = make_feeder(plc, store=store)

    async def scenario():
        await feeder.refresh()
        async def failing():
            raise TuyaError("device offline")
        feeder.fetch_battery = failing
        await feeder.refresh()
        await feeder.refresh()
        await feeder.feed_once()
    asyncio.run(scenario())
    assert feeder.reading.pct == 85.0                    # policy decides if it is still usable
    kinds = [e["kind"] for e in store.events()]
    assert kinds.count("tuya_error") == 1
    store.close()


def test_policy_none_pauses_and_records_one_event(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    plc = FakePlc()
    feeder = make_feeder(plc, store=store, policy=lambda r, now, age: None)

    async def scenario():
        await feeder.refresh()
        for _ in range(3):
            await feeder.feed_once()
    asyncio.run(scenario())
    assert plc.writes == []
    events = [e for e in store.events() if e["kind"] == "battery_feed"]
    assert len(events) == 1 and "paused" in events[0]["message"]
    store.close()


def test_plc_offline_does_not_advance_heartbeat():
    plc = FakePlc()
    feeder = make_feeder(plc)

    async def scenario():
        await feeder.refresh()
        plc.offline = True
        await feeder.feed_once()
        plc.offline = False
        await feeder.feed_once()
    asyncio.run(scenario())
    assert plc.writes == [("battery_pct", 85), ("pi_heartbeat", 1)]


def test_feeder_refuses_read_only_tags():
    class ReadOnlyTags(FakePlc):
        def __init__(self):
            super().__init__()
            self.tags = load_tags(Path(__file__).resolve().parents[1] / "config" / "plc_tags.yaml")
            object.__setattr__(self.tags["pi_heartbeat"], "dir", self.tags["robot_state"].dir)
    with pytest.raises(ValueError, match="pi_heartbeat"):
        make_feeder(ReadOnlyTags())


def test_tuya_battery_source_applies_dp_and_scale(monkeypatch):
    settings = Settings(_env_file=None, plc_host="x", tuya_access_id="id", tuya_access_secret="s",
                        tuya_api_endpoint="https://tuya.test", tuya_device_id="dev1",
                        tuya_battery_dp="battery_percentage", tuya_battery_scale=0.1)
    monkeypatch.setattr("app.battery.TuyaCloud.device_status",
                        lambda self, dev: {"battery_percentage": 873, "work_mode": "charge"})
    assert asyncio.run(tuya_battery_source(settings)()) == pytest.approx(87.3)

    monkeypatch.setattr("app.battery.TuyaCloud.device_status", lambda self, dev: {"work_mode": "charge"})
    with pytest.raises(TuyaError, match="available: work_mode"):
        asyncio.run(tuya_battery_source(settings)())


def test_missing_tuya_keys():
    assert Settings(_env_file=None, plc_host="x").missing_tuya_keys() == [
        "TUYA_ACCESS_ID", "TUYA_ACCESS_SECRET", "TUYA_API_ENDPOINT", "TUYA_DEVICE_ID", "TUYA_BATTERY_DP"]


# --- against the mock PLC + simulated ladder ------------------------------------------

def test_ladder_trusts_battery_while_fed_and_alarms_when_held():
    async def main():
        server = MockPlcServer(TAGS, UNIT, "127.0.0.1", 0)
        await server.serve_forever(background=True)
        ladder = LadderSim(LadderParams(heartbeat_timeout_s=1.0))
        runner = SimRunner(server.core, TAGS, UNIT, ladder=ladder, plant=Plant(PlantParams(fake_pi=False)))
        await runner.start_defaults(cycles_setpoint=1)
        plc = PlcClient(TAGS, "127.0.0.1", server.bound_port, UNIT, timeout_s=1)
        clock = FakeClock()
        feeding = [True]
        feeder = make_feeder(plc, battery=88.0, clock=clock,
                             policy=lambda r, now, age: round(r.pct) if feeding[0] and r else None)
        try:
            await feeder.refresh()
            for _ in range(10):                       # 2 s of ladder time, fed every 0.2 s
                await feeder.feed_once()
                await runner.tick(0.2)
            fed = await plc.read_many(["alarm_code", "battery_pct"])
            feeding[0] = False
            for _ in range(10):                       # 2 s with the heartbeat held
                await feeder.feed_once()
                await runner.tick(0.2)
            held = await plc.read("alarm_code")
        finally:
            plc.close()
            await server.shutdown()
        return fed, held

    fed, held = asyncio.run(main())
    assert fed == {"alarm_code": Alarm.NONE, "battery_pct": 88}
    assert held == Alarm.HEARTBEAT_LOST


def test_fake_battery_source_returns_fixed_pct():
    assert asyncio.run(fake_battery_source(90.0)()) == 90.0
