"""Simulated ladder logic, following docs/robot-operation.md.

Pure logic: scan() takes a snapshot of PLC memory (tag name -> value) and
returns the tag values to write back, like one PLC scan cycle. No Modbus here.

This is a reference model for the real ladder, not a replacement for it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.robot import Alarm, State

TOWARD_END_2 = True   # drive_dir = 1 -> moving toward X1 (assumed polarity)
TOWARD_END_1 = False

COMMANDS = ("cmd_start", "cmd_stop", "cmd_return", "cmd_reset_alarm")


@dataclass
class LadderParams:
    battery_low_pct: float = 25        # below -> return home
    battery_critical_pct: float = 20   # below -> alarm code 2 (warning)
    battery_start_min_pct: float = 80  # needed to leave home
    end_pause_s: float = 2.0           # pause at the far end before reversing
    heartbeat_timeout_s: float = 10.0  # pi_heartbeat unchanged this long -> battery unknown
    default_travel_s: float = 20.0     # end-to-end time until the first full traverse is measured


class LadderSim:
    """State machine for one robot. Alarm state (4) is only for faults that stop
    the robot (E-stop, both limits); battery/heartbeat problems are reported in
    alarm_code while the robot heads home on its own."""

    def __init__(self, params: LadderParams | None = None) -> None:
        self.p = params or LadderParams()
        self.state: State | None = None      # decided on the first scan
        self.alarm = Alarm.NONE
        self.direction = TOWARD_END_2
        self.cycle_count = 0
        self.start_end_1 = True              # cycle started at end 1 (X0)?
        self.pause_left = 0.0
        self.finish_then_home = False        # heartbeat lost during cleaning
        self.position = 0.0                  # 0 = end 1 (X0), 1 = end 2 (X1)
        self.travel_s = self.p.default_travel_s
        self.leg_elapsed: float | None = None  # timing a full end-to-end leg
        self.last_heartbeat: int | None = None
        self.heartbeat_age = self.p.heartbeat_timeout_s  # unknown until first change
        self.button_prev = False

    # --- helpers -------------------------------------------------------------

    def _toward_nearest_end(self) -> bool:
        return TOWARD_END_1 if self.position < 0.5 else TOWARD_END_2

    def _arrived(self, io: dict) -> bool:
        """Reached the limit switch in the current travel direction."""
        return io["limit_2"] if self.direction == TOWARD_END_2 else io["limit_1"]

    def _go_home(self, io: dict) -> None:
        self.state = State.RETURNING
        self.pause_left = 0.0
        self.leg_elapsed = None         # partial leg: don't learn travel time from it
        self.direction = self._toward_nearest_end()

    def _state_at_rest(self, io: dict) -> State:
        return State.HOME if io["limit_1"] or io["limit_2"] else State.IDLE

    # --- one scan ------------------------------------------------------------

    def scan(self, io: dict, dt: float) -> dict:
        out: dict = {}

        # Command bits are requests: act on them this scan, then clear them.
        cmd = {name: bool(io[name]) for name in COMMANDS}
        for name, requested in cmd.items():
            if requested:
                out[name] = False

        button_edge = io["start_stop_btn"] and not self.button_prev
        self.button_prev = bool(io["start_stop_btn"])
        auto_mode = not io["mode_switch"]
        at_1, at_2 = bool(io["limit_1"]), bool(io["limit_2"])

        # Pi heartbeat watchdog: battery_pct is only trusted while it keeps changing.
        # The first value seen is only a baseline (it may be stale from before power-up).
        if self.last_heartbeat is None:
            self.last_heartbeat = io["pi_heartbeat"]
        elif io["pi_heartbeat"] != self.last_heartbeat:
            self.last_heartbeat = io["pi_heartbeat"]
            self.heartbeat_age = 0.0
        else:
            self.heartbeat_age += dt
        heartbeat_ok = self.heartbeat_age < self.p.heartbeat_timeout_s
        battery = io["battery_pct"] if heartbeat_ok else None

        if at_1:
            self.position = 0.0
        elif at_2:
            self.position = 1.0

        if self.state is None:
            self.state = self._state_at_rest(io)

        # Stopping faults, checked every scan.
        if not io["estop_ok"]:
            self.state, self.alarm = State.ALARM, Alarm.ESTOP
        elif at_1 and at_2:
            self.state, self.alarm = State.ALARM, Alarm.BOTH_LIMITS

        if self.state == State.ALARM:
            if cmd["cmd_reset_alarm"] and io["estop_ok"] and not (at_1 and at_2):
                self.alarm = Alarm.NONE
                self.state = self._state_at_rest(io)
        else:
            self._run(io, dt, cmd, button_edge, auto_mode, heartbeat_ok, battery)

        moving = self.state in (State.CLEANING, State.RETURNING) and self.pause_left <= 0
        if moving:
            step = dt / self.travel_s
            self.position = min(1.0, max(0.0, self.position + (step if self.direction else -step)))
            if self.leg_elapsed is not None:
                self.leg_elapsed += dt

        out.update(
            drive_run=moving,
            drive_dir=self.direction,
            robot_state=int(self.state),
            alarm_code=int(self.alarm),
            cycle_count=self.cycle_count,
            position_est_pct=round(self.position * 100),
        )
        return out

    def _run(self, io, dt, cmd, button_edge, auto_mode, heartbeat_ok, battery) -> None:
        # Non-stopping warnings.
        if not heartbeat_ok:
            self.alarm = Alarm.HEARTBEAT_LOST
        elif battery < self.p.battery_critical_pct:
            self.alarm = Alarm.BATTERY_CRITICAL
        else:
            self.alarm = Alarm.NONE
        battery_low = battery is not None and battery < self.p.battery_low_pct

        moving_state = self.state in (State.CLEANING, State.RETURNING)
        want_start = cmd["cmd_start"] or (button_edge and not moving_state)
        want_stop = cmd["cmd_stop"] or (button_edge and moving_state)

        if self.state == State.HOME:
            may_start = heartbeat_ok and battery >= self.p.battery_start_min_pct
            if want_start and may_start:
                self.start_end_1 = bool(io["limit_1"])
                self.direction = TOWARD_END_2 if self.start_end_1 else TOWARD_END_1
                self.cycle_count = 0
                self.pause_left = 0.0
                self.finish_then_home = False
                self.leg_elapsed = 0.0
                self.state = State.CLEANING

        elif self.state == State.IDLE:
            if want_start or cmd["cmd_return"]:
                self._go_home(io)       # position unknown mid-panel: go to nearest end first

        elif self.state == State.CLEANING:
            if not heartbeat_ok:
                self.finish_then_home = True
            if want_stop:
                self.state = State.IDLE
                self.leg_elapsed = None
            elif cmd["cmd_return"] or battery_low:
                self._go_home(io)
            elif self.pause_left > 0:
                self.pause_left -= dt
            elif self._arrived(io):
                self._on_end_reached(io, auto_mode)

        elif self.state == State.RETURNING:
            if want_stop:
                self.state = State.IDLE
            elif self._arrived(io):
                self.state = State.HOME

    def _on_end_reached(self, io: dict, auto_mode: bool) -> None:
        if self.leg_elapsed:                      # learn end-to-end travel time
            self.travel_s = self.leg_elapsed
        self.leg_elapsed = 0.0

        back_at_start = bool(io["limit_1"]) == self.start_end_1
        if back_at_start:
            self.cycle_count += 1
            done = auto_mode and self.cycle_count >= max(1, io["cycles_setpoint"])
            if done or self.finish_then_home:
                self.state = State.HOME
                return
        self.pause_left = self.p.end_pause_s
        self.direction = not self.direction
