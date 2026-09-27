"""Send commands to the PLC under the step 5 safety rules.

 1. Only whitelisted tags, and only if the tag map marks them writable.
 2. Writing lives here, not in the read-only PlcClient API.
 3. Commands are pulses: write 1, the ladder clears it to acknowledge.
 4. No acknowledgement within ACK_TIMEOUT_S -> the Pi clears the bit itself, so a
    request cannot fire later (e.g. when a PLC in STOP mode is switched to RUN).
 5. Stop is never blocked: no lock, no debounce, no precondition checks.
 6. Pre-checks only explain obvious refusals; the ladder still decides.
 7. One command at a time (lock); the same command written again within DEBOUNCE_S
    is ignored (commands refused by a pre-check do not count).
 8. No automatic retries.
 9. Every result is returned for the audit log (see app.command_queue).
10. cycles_setpoint accepts 1-100 only.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.plc_client import PlcClient, PlcOfflineError, PlcReadError
from app.robot import BATTERY_START_MIN_PCT, Alarm, State, describe

PULSES = {"start": "cmd_start", "stop": "cmd_stop", "return": "cmd_return", "reset": "cmd_reset_alarm"}
SETPOINTS = {"cycles": ("cycles_setpoint", 1, 100)}
COMMANDS = tuple(PULSES) + tuple(SETPOINTS)

ACK_TIMEOUT_S = 2.0
CONFIRM_S = 3.0
DEBOUNCE_S = 1.0


@dataclass(frozen=True)
class CommandResult:
    command: str
    status: str      # done | rejected | ignored | not_acknowledged | not_started | error
    message: str

    @property
    def ok(self) -> bool:
        return self.status == "done"


class PlcCommander:
    def __init__(
        self,
        plc: PlcClient,
        ack_timeout_s: float = ACK_TIMEOUT_S,
        confirm_s: float = CONFIRM_S,
        debounce_s: float = DEBOUNCE_S,
        poll_s: float = 0.1,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        for tag_name in list(PULSES.values()) + [t for t, _, _ in SETPOINTS.values()]:
            if not plc.tags[tag_name].writable:  # KeyError if missing from the tag map
                raise ValueError(f"Tag map marks {tag_name!r} read-only; commands need dir: write/rw")
        self.plc = plc
        self.ack_timeout_s, self.confirm_s, self.debounce_s = ack_timeout_s, confirm_s, debounce_s
        self.poll_s, self.clock, self.sleep = poll_s, clock, sleep
        self._lock = asyncio.Lock()
        self._last_sent: dict[str, float] = {}

    async def execute(self, command: str, value=None) -> CommandResult:
        if command not in COMMANDS:
            return CommandResult(command, "rejected", f"unknown command; allowed: {', '.join(COMMANDS)}")
        try:
            if command == "stop":
                return await self._stop()                      # rule 5: never waits for the lock
            if self._lock.locked():
                return CommandResult(command, "rejected", "another command is in progress")
            async with self._lock:
                if self.clock() - self._last_sent.get(command, float("-inf")) < self.debounce_s:
                    return CommandResult(command, "ignored", f"same command within {self.debounce_s:.0f}s")
                if command in SETPOINTS:
                    return await self._setpoint(command, value)
                return await {"start": self._start, "return": self._return, "reset": self._reset}[command]()
        except PlcOfflineError as exc:
            return CommandResult(command, "error", f"PLC offline: {exc}")
        except PlcReadError as exc:
            return CommandResult(command, "error", str(exc))

    # --- commands ------------------------------------------------------------------

    async def _stop(self) -> CommandResult:
        acked = await self._pulse("stop")
        if not acked:
            return self._not_acked("stop")
        if await self._wait_for(lambda s: not s["drive_run"], ["drive_run"]):
            return CommandResult("stop", "done", "stopped")
        return CommandResult("stop", "not_started", "acknowledged but the drive motor is still running")

    async def _start(self) -> CommandResult:
        status = await self._status()
        if not status["estop_ok"]:
            return CommandResult("start", "rejected", "E-stop is pressed")
        if status["robot_state"] == State.ALARM:
            return CommandResult("start", "rejected", f"robot in alarm ({describe(Alarm, status['alarm_code'])}); reset first")
        if status["robot_state"] in (State.CLEANING, State.RETURNING):
            return CommandResult("start", "rejected", f"robot is already {describe(State, status['robot_state'])}")

        if not await self._pulse("start"):
            return self._not_acked("start")
        moving = (State.CLEANING, State.RETURNING)
        if await self._wait_for(lambda s: s["robot_state"] in moving, ["robot_state"]):
            state = (await self._status())["robot_state"]
            if state == State.RETURNING:
                return CommandResult("start", "done", "started: returning to the nearest end first")
            return CommandResult("start", "done", "started cleaning")
        return CommandResult("start", "not_started", "acknowledged but not started: " + self._why_not(await self._status()))

    async def _return(self) -> CommandResult:
        status = await self._status()
        if status["robot_state"] == State.HOME:
            return CommandResult("return", "rejected", "already at home")
        if status["robot_state"] == State.ALARM:
            return CommandResult("return", "rejected", "robot in alarm; reset first")
        if not await self._pulse("return"):
            return self._not_acked("return")
        if await self._wait_for(lambda s: s["robot_state"] in (State.RETURNING, State.HOME), ["robot_state"]):
            return CommandResult("return", "done", "returning to the nearest end")
        return CommandResult("return", "not_started", "acknowledged but not returning")

    async def _reset(self) -> CommandResult:
        status = await self._status()
        if status["robot_state"] != State.ALARM:
            return CommandResult("reset", "rejected", "no alarm to reset")
        if not status["estop_ok"]:
            return CommandResult("reset", "rejected", "release the E-stop first")
        if not await self._pulse("reset"):
            return self._not_acked("reset")
        if await self._wait_for(lambda s: s["robot_state"] != State.ALARM, ["robot_state"]):
            return CommandResult("reset", "done", "alarm cleared")
        return CommandResult("reset", "not_started", "acknowledged but still in alarm: "
                             + describe(Alarm, (await self._status())["alarm_code"]))

    async def _setpoint(self, command: str, value) -> CommandResult:
        tag, low, high = SETPOINTS[command]
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = float("nan")
        if isinstance(value, bool) or not number.is_integer() or not low <= number <= high:
            return CommandResult(command, "rejected", f"value must be a whole number {low}-{high}, got {value!r}")
        number = int(number)
        self._last_sent[command] = self.clock()
        await self.plc._write(tag, number)
        if await self.plc.read(tag) == number:
            return CommandResult(command, "done", f"{tag} = {number}")
        return CommandResult(command, "error", f"{tag} read-back does not match {number}")

    # --- helpers -------------------------------------------------------------------

    async def _status(self) -> dict:
        return await self.plc.read_many(["robot_state", "alarm_code", "estop_ok", "battery_pct", "drive_run"])

    async def _pulse(self, command: str) -> bool:
        """Write the request bit and wait for the ladder to clear it. Clears it ourselves on timeout."""
        tag = PULSES[command]
        self._last_sent[command] = self.clock()
        await self.plc._write(tag, True)
        if await self._wait_for(lambda s: not s[tag], [tag], self.ack_timeout_s):
            return True
        await self.plc._write(tag, False)                      # rule 4
        return False

    def _not_acked(self, command: str) -> CommandResult:
        return CommandResult(command, "not_acknowledged",
                             f"PLC did not acknowledge within {self.ack_timeout_s:.0f}s "
                             "(PLC in STOP mode or ladder missing the command); request cleared")

    async def _wait_for(self, condition, names, timeout_s: float | None = None) -> bool:
        deadline = self.clock() + (self.confirm_s if timeout_s is None else timeout_s)
        while True:
            if condition(await self.plc.read_many(names)):
                return True
            if self.clock() >= deadline:
                return False
            await self.sleep(self.poll_s)

    @staticmethod
    def _why_not(status: dict) -> str:
        if status["alarm_code"] == Alarm.HEARTBEAT_LOST:
            return "Pi heartbeat lost, battery level unknown to the PLC"
        if status["battery_pct"] < BATTERY_START_MIN_PCT:
            return f"battery {status['battery_pct']}% is below {BATTERY_START_MIN_PCT}%"
        if status["alarm_code"] != Alarm.NONE:
            return describe(Alarm, status["alarm_code"])
        return "the ladder did not start (check mode and robot conditions)"
