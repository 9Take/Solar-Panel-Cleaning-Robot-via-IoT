"""Glue between the mock PLC datastore, the simulated ladder and the plant.

Each tick:  plant reads PLC outputs -> writes sensor inputs
            ladder reads all PLC memory -> writes outputs/status (one scan)

Runs in the server's event loop. StrictSimCore calls complete without
yielding, so a tick is atomic with respect to Modbus client requests.
"""

from __future__ import annotations

import asyncio

from pymodbus.constants import ExcCodes

from app.codec import Value, decode, encode
from app.delta import Area
from app.sim.datastore import StrictSimCore
from app.sim.ladder import LadderSim
from app.sim.plant import Plant
from app.tags import TagMap

REQUIRED_TAGS = (
    "cmd_start", "cmd_stop", "cmd_return", "cmd_reset_alarm",
    "cycles_setpoint", "battery_pct", "pi_heartbeat",
    "robot_state", "alarm_code", "cycle_count", "position_est_pct",
    "limit_1", "limit_2", "start_stop_btn", "mode_switch", "estop_ok",
    "drive_run", "drive_dir",
    "pzem_voltage", "pzem_current", "pzem_power", "pzem_energy",
)

_READ_FC = {Area.COIL: 1, Area.DISCRETE_INPUT: 2, Area.HOLDING_REGISTER: 3}
# FC02 "write" only exists inside the server: it is how the plant sets X inputs.
_WRITE_FC = {Area.COIL: 15, Area.DISCRETE_INPUT: 2, Area.HOLDING_REGISTER: 16}


class CoreTagIO:
    """Read/write tags by name directly on the mock datastore (server side)."""

    def __init__(self, core: StrictSimCore, tags: TagMap, unit_id: int) -> None:
        self.core, self.tags, self.unit_id = core, tags, unit_id

    async def read(self, name: str) -> Value:
        tag = self.tags[name]
        raw = await self.core.async_getValues(
            self.unit_id, _READ_FC[tag.addr.area], tag.addr.address, tag.word_count)
        if isinstance(raw, ExcCodes):
            raise RuntimeError(f"mock read {name} failed: {raw!r}")
        return decode(tag, raw[0] if tag.addr.is_bit else raw)

    async def write(self, name: str, value: Value) -> None:
        tag = self.tags[name]
        raw = encode(tag, value)
        result = await self.core.async_setValues(
            self.unit_id, _WRITE_FC[tag.addr.area], tag.addr.address,
            [raw] if tag.addr.is_bit else raw)
        if isinstance(result, ExcCodes):
            raise RuntimeError(f"mock write {name} failed: {result!r}")

    async def read_all(self) -> dict[str, Value]:
        return {tag.name: await self.read(tag.name) for tag in self.tags}

    async def write_many(self, values: dict[str, Value]) -> None:
        for name, value in values.items():
            await self.write(name, value)


class SimRunner:
    def __init__(self, core: StrictSimCore, tags: TagMap, unit_id: int,
                 ladder: LadderSim | None = None, plant: Plant | None = None) -> None:
        missing = [name for name in REQUIRED_TAGS if name not in tags.tags]
        if missing:
            raise ValueError(f"Tag map is missing tags needed by the simulation: {', '.join(missing)}")
        self.io = CoreTagIO(core, tags, unit_id)
        self.ladder = ladder or LadderSim()
        self.plant = plant or Plant()

    async def start_defaults(self, cycles_setpoint: int = 2) -> None:
        """Values a freshly downloaded ladder would hold."""
        await self.io.write("cycles_setpoint", cycles_setpoint)

    async def tick(self, dt: float) -> None:
        outputs = {name: await self.io.read(name) for name in ("drive_run", "drive_dir")}
        await self.io.write_many(self.plant.step(outputs, dt))
        await self.io.write_many(self.ladder.scan(await self.io.read_all(), dt))

    async def run_forever(self, tick_s: float) -> None:
        loop = asyncio.get_running_loop()
        last = loop.time()
        while True:
            await asyncio.sleep(tick_s)
            now = loop.time()
            await self.tick(now - last)
            last = now
