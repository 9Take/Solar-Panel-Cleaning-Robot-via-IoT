"""Feed the PLC the battery % from Tuya Cloud, plus the Pi heartbeat.

The ladder owns every battery decision; the Pi only supplies the number:

  every tuya_poll_interval_s:  read battery % from Tuya Cloud (own task, so a slow
                               or failing request never delays the heartbeat)
  every heartbeat_interval_s:  write battery_pct, then pi_heartbeat + 1

pi_heartbeat means "battery_pct is current". When there is no usable Tuya reading
the heartbeat stops (battery_pct is left as is), and after its timeout the ladder
treats the battery as unknown (alarm 5: no new start, finish the cycle, go home).

Only the two tags in FEED_TAGS are ever written here.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.config import Settings
from app.history import HistoryStore
from app.plc_client import PlcClient, PlcOfflineError, PlcReadError
from app.tuya import TuyaCloud, TuyaError

log = logging.getLogger("gateway")

FEED_TAGS = ("battery_pct", "pi_heartbeat")


@dataclass(frozen=True)
class BatteryReading:
    pct: float     # battery % as reported (after scale)
    at: float      # clock() time the reading was taken


def battery_to_feed(reading: BatteryReading | None, now: float, max_age_s: float) -> int | None:
    """Battery % to write to the PLC, or None to hold the heartbeat.

    Returning None tells the ladder "battery unknown" (after its heartbeat timeout).
    Stale or impossible readings are never clamped into a plausible-looking value.
    Rounds down so the ladder's start threshold (>= 80 %) is never met early.
    """
    if reading is None or now - reading.at > max_age_s:
        return None
    if not math.isfinite(reading.pct) or not 0 <= reading.pct <= 100:
        return None
    return int(reading.pct)


def tuya_battery_source(settings: Settings) -> Callable[[], Awaitable[float]]:
    """Async function returning the battery % of the configured Tuya device."""
    cloud = TuyaCloud(settings.tuya_api_endpoint, settings.tuya_access_id,
                      settings.tuya_access_secret.get_secret_value(), settings.tuya_timeout_s)
    dp, scale = settings.tuya_battery_dp, settings.tuya_battery_scale

    def read() -> float:
        status = cloud.device_status(settings.tuya_device_id)
        if dp not in status:
            raise TuyaError(f"device has no DP {dp!r}; available: {', '.join(sorted(status))}")
        value = status[dp]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TuyaError(f"DP {dp!r} is not a number: {value!r}")
        return value * scale

    return lambda: asyncio.to_thread(read)   # urllib blocks; keep the event loop free


def fake_battery_source(pct: float) -> Callable[[], Awaitable[float]]:
    """Async function always returning `pct` (BATTERY_FAKE_PCT, bench test without Tuya)."""
    async def read() -> float:
        return pct

    return read


class BatteryFeeder:
    def __init__(
        self,
        plc: PlcClient,
        fetch_battery: Callable[[], Awaitable[float]],
        store: HistoryStore | None = None,
        heartbeat_interval_s: float = 2.0,
        poll_interval_s: float = 60.0,
        max_age_s: float = 300.0,
        policy: Callable[[BatteryReading | None, float, float], int | None] = battery_to_feed,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        for name in FEED_TAGS:
            if not plc.tags[name].writable:   # KeyError if missing from the tag map
                raise ValueError(f"Tag map marks {name!r} read-only; the battery feed needs dir: write/rw")
        self.plc, self.fetch_battery, self.store = plc, fetch_battery, store
        self.heartbeat_interval_s, self.poll_interval_s = heartbeat_interval_s, poll_interval_s
        self.max_age_s, self.policy = max_age_s, policy
        self.clock, self.wall_clock = clock, wall_clock
        self.reading: BatteryReading | None = None
        self.heartbeat = 0
        self._status: str | None = None       # last reported feed status (log on change only)
        self._tuya_error: str | None = None

    async def run_forever(self) -> None:
        await asyncio.gather(self._poll_forever(), self._feed_forever())

    async def _poll_forever(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(self.poll_interval_s)

    async def _feed_forever(self) -> None:
        while True:
            await self.feed_once()
            await asyncio.sleep(self.heartbeat_interval_s)

    async def refresh(self) -> None:
        """Take a new battery reading from Tuya; keep the previous one on failure."""
        try:
            pct = await self.fetch_battery()
        except TuyaError as exc:
            if str(exc) != self._tuya_error:
                self._event("tuya_error", f"Tuya battery read failed: {exc}", logging.WARNING)
                self._tuya_error = str(exc)
            return
        if self._tuya_error is not None:
            self._event("tuya_ok", "Tuya battery read OK again")
            self._tuya_error = None
        self.reading = BatteryReading(pct, self.clock())
        log.debug("Tuya battery %.1f%%", pct)

    async def feed_once(self) -> None:
        """Write battery_pct + the next heartbeat, or hold the heartbeat."""
        pct = self.policy(self.reading, self.clock(), self.max_age_s)
        if pct is None:
            self._set_status("paused", f"Battery feed paused, heartbeat held: {self._describe_reading()}")
            return
        try:
            await self.plc._write("battery_pct", pct)        # value first, then vouch for it
            next_heartbeat = (self.heartbeat + 1) & 0xFFFF
            await self.plc._write("pi_heartbeat", next_heartbeat)
        except (PlcOfflineError, PlcReadError) as exc:
            self._set_status("plc_error", f"Battery feed cannot write to the PLC: {exc}")
            return
        self.heartbeat = next_heartbeat
        self._set_status("feeding", f"Battery feed running: {pct}% to the PLC")

    def _describe_reading(self) -> str:
        if self.reading is None:
            return "no Tuya reading yet"
        age = self.clock() - self.reading.at
        return f"last Tuya reading {self.reading.pct:g}% is {age:.0f}s old (limit {self.max_age_s:.0f}s)"

    def _set_status(self, status: str, message: str) -> None:
        if status != self._status:
            self._event("battery_feed", message, logging.INFO if status == "feeding" else logging.WARNING)
            self._status = status

    def _event(self, kind: str, message: str, level: int = logging.INFO) -> None:
        log.log(level, "%s", message)
        if self.store is not None:
            self.store.add_event(self.wall_clock(), kind, message)
