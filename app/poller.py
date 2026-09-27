"""Poll the PLC, detect changes, and record history.

Every poll_interval_s:  read all tags -> update `latest`
                        log + store an event for each watched change
Every snapshot_interval_s: store a snapshot of all values
Every hour: drop snapshots older than the retention period
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from app.history import HistoryStore
from app.plc_client import PlcClient, PlcOfflineError, PlcReadError
from app.robot import Alarm, State, describe

log = logging.getLogger("gateway")

# Tags whose changes become events, with a formatter for the log message.
WATCHED: dict[str, Callable[[object], str]] = {
    "robot_state": lambda v: describe(State, v),
    "alarm_code": lambda v: describe(Alarm, v),
    "estop_ok": lambda v: "released" if v else "PRESSED",
    "mode_switch": lambda v: "Manual" if v else "Auto",
}
PRUNE_EVERY_S = 3600


class Poller:
    def __init__(
        self,
        plc: PlcClient,
        store: HistoryStore,
        poll_interval_s: float = 1.0,
        snapshot_interval_s: float = 10.0,
        retention_days: float = 30.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.plc, self.store = plc, store
        self.poll_interval_s = poll_interval_s
        self.snapshot_interval_s = snapshot_interval_s
        self.retention_s = retention_days * 86400
        self.clock = clock
        self.previous: dict | None = None     # last values seen while online
        self.online: bool | None = None       # None until the first poll
        self._last_snapshot = float("-inf")
        self._last_read_error: str | None = None
        self._last_prune = float("-inf")

    async def poll_once(self) -> None:
        now = self.clock()
        try:
            values = await self.plc.read_all()
        except PlcOfflineError as exc:
            self._set_online(now, False, str(exc))
            self.store.set_latest(now, False, None)
            return
        except PlcReadError as exc:
            # PLC reachable but the tag map does not match it: a config problem.
            if str(exc) != self._last_read_error:   # record once, not every poll
                log.error("%s", exc)
                self.store.add_event(now, "read_error", str(exc))
                self._last_read_error = str(exc)
            self._set_online(now, True)
            self.store.set_latest(now, True, None)
            return
        self._last_read_error = None

        self._set_online(now, True)
        self._record_changes(now, values)
        self.store.set_latest(now, True, values)

        if now - self._last_snapshot >= self.snapshot_interval_s:
            self.store.add_snapshot(now, values)
            self._last_snapshot = now
        if now - self._last_prune >= PRUNE_EVERY_S:
            deleted = self.store.prune_snapshots(now - self.retention_s)
            if deleted:
                log.info("Pruned %d snapshot(s) older than %.0f days", deleted, self.retention_s / 86400)
            self._last_prune = now

    def _set_online(self, now: float, online: bool, reason: str = "") -> None:
        if online == self.online:
            return
        if online:
            message = "PLC online"
            log.info(message)
        else:
            message = f"PLC offline: {reason}"
            log.warning(message)
        # Skip the "online" event on a clean first start; always record going offline.
        if self.online is not None or not online:
            self.store.add_event(now, "online" if online else "offline", message)
        self.online = online

    def _record_changes(self, now: float, values: dict) -> None:
        first = self.previous is None
        for tag, fmt in WATCHED.items():
            if tag not in values:
                continue
            new = values[tag]
            old = None if first else self.previous.get(tag)
            if not first and old == new:
                continue
            message = f"{tag}: {fmt(new)}" if first else f"{tag}: {fmt(old)} -> {fmt(new)}"
            level = logging.WARNING if _is_problem(tag, new) else logging.INFO
            log.log(level, message)
            self.store.add_event(now, "change", message, tag=tag, old=old, new=new)
        self.previous = values

    async def run_forever(self) -> None:
        while True:
            started = self.clock()
            await self.poll_once()
            elapsed = self.clock() - started
            await asyncio.sleep(max(0.0, self.poll_interval_s - elapsed))


def _is_problem(tag: str, value) -> bool:
    return (tag == "alarm_code" and value != Alarm.NONE) or (tag == "estop_ok" and not value) \
        or (tag == "robot_state" and value == State.ALARM)
