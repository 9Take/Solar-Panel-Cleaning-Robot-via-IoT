"""Simulated ladder + plant, closed loop, per docs/robot-operation.md."""

import asyncio
from pathlib import Path

import pytest
from pymodbus.client import AsyncModbusTcpClient

from app.codec import decode, encode
from app.sim.ladder import Alarm, LadderParams, LadderSim, State
from app.sim.plant import Plant, PlantParams
from app.sim.runner import REQUIRED_TAGS, SimRunner
from app.sim.server import MockPlcServer
from app.tags import load_tags

DT = 0.1
BOOT_S = 2.5   # fake Pi heartbeat ticks every 2 s; ladder trusts battery only after a change
REPO_TAGS = Path(__file__).resolve().parents[1] / "config" / "plc_tags.yaml"


class Loop:
    """PLC memory as a dict + plant + ladder, stepped together like SimRunner."""

    def __init__(self, travel_s=10.0, battery=90.0, fake_pi=True, start_position=0.0, **ladder_kw):
        self.mem = {name: 0 for name in REQUIRED_TAGS}
        self.mem.update({name: False for name in (
            "cmd_start", "cmd_stop", "cmd_return", "cmd_reset_alarm", "limit_1", "limit_2",
            "start_stop_btn", "mode_switch", "estop_ok", "drive_run", "drive_dir")})
        self.mem["cycles_setpoint"] = 2
        self.plant = Plant(PlantParams(travel_s=travel_s, start_battery_pct=battery,
                                       fake_pi=fake_pi, start_position=start_position,
                                       drain_pct_per_s=0, solar_pct_per_s=0))
        self.ladder = LadderSim(LadderParams(default_travel_s=travel_s, **ladder_kw))
        self.time = 0.0
        self.run(BOOT_S)

    def step(self, dt=DT):
        self.mem.update(self.plant.step(self.mem, dt))
        self.mem.update(self.ladder.scan(dict(self.mem), dt))
        self.time += dt

    def run(self, seconds):
        for _ in range(round(seconds / DT)):
            self.step()

    def run_until(self, cond, max_s=120.0):
        for _ in range(round(max_s / DT)):
            self.step()
            if cond(self.mem):
                return
        pytest.fail(f"condition not reached within {max_s}s; state={self.mem['robot_state']}")

    def command(self, name):
        self.mem[name] = True   # Pi writes the request bit

    @property
    def state(self):
        return State(self.mem["robot_state"])


def test_boot_at_end_is_home():
    loop = Loop()
    loop.step()
    assert loop.state == State.HOME and loop.mem["limit_1"]


def test_boot_mid_panel_is_idle():
    loop = Loop(start_position=0.4)
    loop.step()
    assert loop.state == State.IDLE


def test_auto_run_completes_cycles_then_home():
    loop = Loop()
    loop.step()
    loop.command("cmd_start")
    loop.step()
    assert loop.state == State.CLEANING
    assert loop.mem["cmd_start"] is False, "ladder must clear the request bit"
    assert loop.mem["drive_run"] and loop.mem["drive_dir"], "leaves end 1 toward end 2"

    loop.run_until(lambda m: m["limit_2"])
    loop.step()
    assert not loop.mem["drive_run"], "pauses at the far end"
    loop.run(2.5)
    assert loop.mem["drive_run"] and not loop.mem["drive_dir"], "reverses after the pause"

    loop.run_until(lambda m: m["robot_state"] == State.HOME, max_s=60)
    assert loop.mem["cycle_count"] == 2
    assert loop.mem["limit_1"] and not loop.mem["drive_run"]


def test_start_from_end_2_goes_toward_end_1():
    loop = Loop(start_position=1.0)
    loop.step()
    loop.command("cmd_start")
    loop.step()
    assert loop.state == State.CLEANING and loop.mem["drive_dir"] is False


def test_manual_mode_runs_until_stop():
    loop = Loop()
    loop.plant.mode_manual = True
    loop.step()
    loop.command("cmd_start")
    loop.run(60)                      # well past 2 cycles of ~22 s
    assert loop.state == State.CLEANING and loop.mem["cycle_count"] >= 2

    loop.command("cmd_stop")
    loop.step()
    assert loop.state == State.IDLE and not loop.mem["drive_run"]


def test_start_from_idle_returns_to_nearest_end():
    loop = Loop(start_position=0.8)
    loop.step()
    loop.ladder.position = 0.8        # ladder's estimate (would come from travel timing)
    loop.command("cmd_start")
    loop.run_until(lambda m: m["robot_state"] == State.HOME, max_s=10)
    assert loop.mem["limit_2"], "0.8 is closer to end 2"


def test_low_battery_returns_home():
    loop = Loop()
    loop.step()
    loop.command("cmd_start")
    loop.run(4)                       # ~40 % across the panel
    loop.plant.battery_pct = 24       # below 25 % low threshold
    loop.run(0.5)
    assert loop.state == State.RETURNING
    assert loop.mem["drive_dir"] is False, "nearest end is end 1"
    loop.run_until(lambda m: m["robot_state"] == State.HOME, max_s=10)


def test_critical_battery_sets_alarm_code_but_keeps_moving_home():
    loop = Loop()
    loop.step()
    loop.command("cmd_start")
    loop.run(4)
    loop.plant.battery_pct = 15
    loop.run(0.5)
    assert loop.state == State.RETURNING
    assert loop.mem["alarm_code"] == Alarm.BATTERY_CRITICAL


def test_start_refused_below_80_percent():
    loop = Loop(battery=79)
    loop.step()
    loop.command("cmd_start")
    loop.run(1)
    assert loop.state == State.HOME and not loop.mem["drive_run"]


def test_estop_stops_and_needs_release_plus_reset():
    loop = Loop()
    loop.step()
    loop.command("cmd_start")
    loop.run(3)
    loop.plant.press_estop()
    loop.step()
    assert loop.state == State.ALARM and loop.mem["alarm_code"] == Alarm.ESTOP
    position = loop.plant.position
    loop.run(2)
    assert loop.plant.position == position, "motor must not move"

    loop.command("cmd_reset_alarm")
    loop.step()
    assert loop.state == State.ALARM, "reset ignored while E-stop still pressed"

    loop.plant.release_estop()
    loop.command("cmd_reset_alarm")
    loop.step()
    assert loop.state == State.IDLE and loop.mem["alarm_code"] == Alarm.NONE


def test_both_limits_is_alarm():
    ladder = LadderSim()
    io = {name: 0 for name in REQUIRED_TAGS}
    io.update(limit_1=True, limit_2=True, estop_ok=True, pi_heartbeat=1, battery_pct=90)
    out = ladder.scan(io, DT)
    assert out["robot_state"] == State.ALARM and out["alarm_code"] == Alarm.BOTH_LIMITS
    assert out["drive_run"] is False


def test_no_heartbeat_blocks_start():
    loop = Loop(fake_pi=False)
    loop.mem["battery_pct"] = 100     # stale value, never refreshed
    loop.step()
    loop.command("cmd_start")
    loop.run(1)
    assert loop.state == State.HOME
    assert loop.mem["alarm_code"] == Alarm.HEARTBEAT_LOST


def test_heartbeat_lost_while_cleaning_finishes_cycle_then_home():
    loop = Loop(heartbeat_timeout_s=3)
    loop.mem["cycles_setpoint"] = 5
    loop.step()
    loop.command("cmd_start")
    loop.step()
    loop.plant.p.fake_pi = False      # Pi dies
    loop.run_until(lambda m: m["robot_state"] == State.HOME, max_s=60)
    assert loop.mem["cycle_count"] == 1 and loop.mem["limit_1"]


def test_front_button_toggles_start_stop():
    loop = Loop()
    loop.step()
    loop.plant.press_button()
    loop.run(0.3)
    assert loop.state == State.CLEANING
    loop.run(2)
    loop.plant.press_button()
    loop.run(0.3)
    assert loop.state == State.IDLE


def test_cmd_return_while_cleaning():
    loop = Loop()
    loop.step()
    loop.command("cmd_start")
    loop.run(8)                       # 80 % across
    loop.command("cmd_return")
    loop.step()
    assert loop.state == State.RETURNING and loop.mem["drive_dir"] is True
    loop.run_until(lambda m: m["robot_state"] == State.HOME, max_s=10)
    assert loop.mem["limit_2"]


def test_learns_travel_time():
    loop = Loop(travel_s=10.0)
    loop.ladder.travel_s = loop.ladder.p.default_travel_s = 30.0   # ladder starts with a wrong guess
    loop.step()
    loop.command("cmd_start")
    loop.run_until(lambda m: m["limit_2"])
    loop.step()
    assert loop.ladder.travel_s == pytest.approx(10.0, abs=0.3)


def test_repo_tag_map_has_simulation_tags():
    tags = load_tags(REPO_TAGS)
    assert not [n for n in REQUIRED_TAGS if n not in tags.tags]


def test_runner_rejects_incomplete_tag_map(tmp_path):
    path = tmp_path / "tags.yaml"
    path.write_text("tags:\n  cmd_start: {device: M100, dir: write, type: bool}\n", encoding="utf-8")
    tags = load_tags(path)
    server_core = None
    with pytest.raises(ValueError, match="missing tags"):
        SimRunner(server_core, tags, 1)


def test_end_to_end_over_modbus():
    """Pi-like client writes cmd_start over TCP; simulation reacts; client sees it."""
    tags = load_tags(REPO_TAGS)

    async def main():
        server = MockPlcServer(tags, 1, "127.0.0.1", 0)
        await server.serve_forever(background=True)
        runner = SimRunner(server.core, tags, 1, plant=Plant(PlantParams(travel_s=5)))
        await runner.start_defaults()
        client = AsyncModbusTcpClient("127.0.0.1", port=server.bound_port, timeout=2, retries=0)
        try:
            assert await client.connect()

            async def read(name):
                tag = tags[name]
                if tag.addr.is_bit:
                    fc = client.read_discrete_inputs if tag.device.startswith("X") else client.read_coils
                    return decode(tag, (await fc(tag.addr.address, count=1, device_id=1)).bits[0])
                r = await client.read_holding_registers(tag.addr.address, count=tag.word_count, device_id=1)
                return decode(tag, r.registers)

            for _ in range(round(BOOT_S / 0.2)):   # wait for the first heartbeat change
                await runner.tick(0.2)
            assert await read("robot_state") == State.HOME

            start = tags["cmd_start"]
            assert not (await client.write_coil(start.addr.address, encode(start, True), device_id=1)).isError()
            await runner.tick(0.2)
            assert await read("robot_state") == State.CLEANING
            assert await read("cmd_start") is False
            assert await read("drive_run") is True

            for _ in range(10):
                await runner.tick(0.2)
            assert await read("position_est_pct") > 0
            assert await read("pzem_current") == 2.5
        finally:
            client.close()
            await server.shutdown()

    asyncio.run(main())
