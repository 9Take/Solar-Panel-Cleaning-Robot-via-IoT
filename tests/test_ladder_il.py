"""plc/robot_reference.il run by app.sim.il against the simulated plant.

Same scenarios as tests/test_sim_behavior.py (the Python reference ladder),
so the IL and app/sim/ladder.py are held to one behavior spec.
"""

from pathlib import Path

import pytest

from app.sim.il import IlLadder, load_il
from app.sim.ladder import Alarm, State
from app.sim.plant import Plant, PlantParams
from app.sim.runner import REQUIRED_TAGS
from app.tags import load_tags

ROOT = Path(__file__).resolve().parents[1]
IL_PATH = ROOT / "plc" / "robot_reference.il"
TAGS = load_tags(ROOT / "config" / "plc_tags.yaml")

DT = 0.1
BOOT_S = 2.5
TRAVEL_S = 20.0   # the IL's built-in first guess, so position estimates hold before learning


class Loop:
    def __init__(self, travel_s=TRAVEL_S, battery=90.0, fake_pi=True, start_position=0.0):
        self.mem = {name: 0 for name in REQUIRED_TAGS}
        self.mem.update({name: False for name in (
            "cmd_start", "cmd_stop", "cmd_return", "cmd_reset_alarm", "limit_1", "limit_2",
            "start_stop_btn", "mode_switch", "estop_ok", "drive_run", "drive_dir")})
        self.mem["cycles_setpoint"] = 2
        self.plant = Plant(PlantParams(travel_s=travel_s, start_battery_pct=battery,
                                       fake_pi=fake_pi, start_position=start_position,
                                       drain_pct_per_s=0, solar_pct_per_s=0))
        self.ladder = IlLadder(IL_PATH, TAGS)
        self.run(BOOT_S)

    def step(self):
        self.mem.update(self.plant.step(self.mem, DT))
        self.mem.update(self.ladder.scan(dict(self.mem), DT))

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
        self.mem[name] = True

    @property
    def state(self):
        return State(self.mem["robot_state"])


def test_il_parses():
    assert load_il(IL_PATH)[-1] == ("END", [])


def test_il_uses_tag_map_devices():
    """Every Pi-facing device in the tag map is used by the IL (catches drift when one changes)."""
    words = {w.upper() for line in IL_PATH.read_text(encoding="utf-8").splitlines()
             for w in line.split(";", 1)[0].split()}
    words |= {f"M{n}" for n in range(100, 104)}          # cleared by ZRST M100 M103
    names = [n for n in REQUIRED_TAGS if not n.startswith("pzem_")]
    assert [n for n in names if TAGS[n].device not in words] == []


def test_unknown_instruction_rejected(tmp_path):
    bad = tmp_path / "bad.il"
    bad.write_text("LD M0\nFOO D0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bad.il:2: unsupported instruction FOO"):
        load_il(bad)


def test_boot_at_end_is_home():
    loop = Loop()
    assert loop.state == State.HOME


def test_boot_mid_panel_is_idle():
    assert Loop(start_position=0.4).state == State.IDLE


def test_auto_run_completes_cycles_then_home():
    loop = Loop()
    loop.command("cmd_start")
    loop.step()
    assert loop.state == State.CLEANING
    assert loop.mem["cmd_start"] is False, "ladder must clear the request bit"
    assert loop.mem["drive_run"] and loop.mem["drive_dir"]

    loop.run_until(lambda m: m["limit_2"])
    loop.step()
    assert not loop.mem["drive_run"], "pauses at the far end"
    loop.run(2.5)
    assert loop.mem["drive_run"] and not loop.mem["drive_dir"], "reverses after the pause"

    loop.run_until(lambda m: m["robot_state"] == State.HOME, max_s=120)
    assert loop.mem["cycle_count"] == 2
    assert loop.mem["limit_1"] and not loop.mem["drive_run"]


def test_start_from_end_2_goes_toward_end_1():
    loop = Loop(start_position=1.0)
    loop.command("cmd_start")
    loop.step()
    assert loop.state == State.CLEANING and loop.mem["drive_dir"] is False


def test_manual_mode_runs_until_stop():
    loop = Loop()
    loop.plant.mode_manual = True
    loop.command("cmd_start")
    loop.run(100)                     # past 2 cycles of ~42 s
    assert loop.state == State.CLEANING and loop.mem["cycle_count"] >= 2
    loop.command("cmd_stop")
    loop.step()
    assert loop.state == State.IDLE and not loop.mem["drive_run"]


def test_position_estimate_follows_travel():
    loop = Loop()
    loop.command("cmd_start")
    loop.run(10)
    assert loop.mem["position_est_pct"] == pytest.approx(50, abs=3)


def test_start_from_idle_returns_to_nearest_end():
    loop = Loop()
    loop.command("cmd_start")
    loop.run(16)                      # ~80 % across
    loop.command("cmd_stop")
    loop.step()
    assert loop.state == State.IDLE
    loop.command("cmd_start")
    loop.step()
    assert loop.state == State.RETURNING and loop.mem["drive_dir"] is True
    loop.run_until(lambda m: m["robot_state"] == State.HOME, max_s=10)
    assert loop.mem["limit_2"]


def test_low_battery_returns_home():
    loop = Loop()
    loop.command("cmd_start")
    loop.run(8)                       # ~40 %
    loop.plant.battery_pct = 24
    loop.run(0.5)
    assert loop.state == State.RETURNING and loop.mem["drive_dir"] is False
    loop.run_until(lambda m: m["robot_state"] == State.HOME, max_s=15)
    assert loop.mem["limit_1"]


def test_critical_battery_sets_alarm_code_but_keeps_moving_home():
    loop = Loop()
    loop.command("cmd_start")
    loop.run(8)
    loop.plant.battery_pct = 15
    loop.run(0.5)
    assert loop.state == State.RETURNING and loop.mem["drive_run"]
    assert loop.mem["alarm_code"] == Alarm.BATTERY_CRITICAL


def test_start_refused_below_80_percent():
    loop = Loop(battery=79)
    loop.command("cmd_start")
    loop.run(1)
    assert loop.state == State.HOME and not loop.mem["drive_run"]
    assert loop.mem["cmd_start"] is False


def test_estop_stops_and_needs_release_plus_reset():
    loop = Loop()
    loop.command("cmd_start")
    loop.run(3)
    loop.plant.press_estop()
    loop.step()
    assert loop.state == State.ALARM and loop.mem["alarm_code"] == Alarm.ESTOP
    assert not loop.mem["drive_run"]

    loop.command("cmd_reset_alarm")
    loop.step()
    assert loop.state == State.ALARM, "reset ignored while E-stop still pressed"

    loop.plant.release_estop()
    loop.step()
    assert loop.state == State.ALARM, "releasing alone does not reset"
    loop.command("cmd_reset_alarm")
    loop.step()
    assert loop.state == State.IDLE and loop.mem["alarm_code"] == Alarm.NONE
    assert not loop.mem["drive_run"]


def test_both_limits_is_alarm():
    ladder = IlLadder(IL_PATH, TAGS)
    io = {name: 0 for name in REQUIRED_TAGS}
    io.update(limit_1=True, limit_2=True, estop_ok=True, pi_heartbeat=1, battery_pct=90)
    out = ladder.scan(io, DT)
    assert out["robot_state"] == State.ALARM and out["alarm_code"] == Alarm.BOTH_LIMITS
    assert out["drive_run"] is False


def test_no_heartbeat_blocks_start():
    loop = Loop(fake_pi=False)
    loop.mem["battery_pct"] = 100     # stale value, never refreshed
    loop.command("cmd_start")
    loop.run(1)
    assert loop.state == State.HOME
    assert loop.mem["alarm_code"] == Alarm.HEARTBEAT_LOST


def test_heartbeat_lost_while_cleaning_finishes_cycle_then_home():
    loop = Loop(travel_s=5)
    loop.mem["cycles_setpoint"] = 5
    loop.command("cmd_start")
    loop.step()
    loop.plant.p.fake_pi = False      # Pi dies
    loop.run(11)
    assert loop.mem["alarm_code"] == Alarm.HEARTBEAT_LOST
    loop.run_until(lambda m: m["robot_state"] == State.HOME, max_s=60)
    assert loop.mem["cycle_count"] < 5 and loop.mem["limit_1"]


def test_front_button_toggles_start_stop():
    loop = Loop()
    loop.plant.press_button()
    loop.run(0.3)
    assert loop.state == State.CLEANING
    loop.run(2)
    loop.plant.press_button()
    loop.run(0.3)
    assert loop.state == State.IDLE


def test_stop_wins_over_start_in_same_scan():
    loop = Loop()
    loop.command("cmd_start")
    loop.command("cmd_stop")
    loop.step()
    assert loop.state == State.HOME and not loop.mem["drive_run"]


def test_cmd_return_while_cleaning():
    loop = Loop()
    loop.command("cmd_start")
    loop.run(16)
    loop.command("cmd_return")
    loop.step()
    assert loop.state == State.RETURNING and loop.mem["drive_dir"] is True
    loop.run_until(lambda m: m["robot_state"] == State.HOME, max_s=10)
    assert loop.mem["limit_2"]


def test_learns_travel_time():
    loop = Loop(travel_s=10.0)
    loop.command("cmd_start")
    loop.run_until(lambda m: m["limit_2"])
    loop.step()
    assert loop.ladder.word("D202") == pytest.approx(100, abs=3)   # 0.1 s ticks
