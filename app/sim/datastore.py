"""Mock PLC datastore built from the tag map.

Only addresses that appear in the tag map exist; any other address returns
Modbus exception 02 (Illegal Data Address), like a PLC without that device.

pymodbus pads gaps between coils with readable False bits, so strictness is
enforced in StrictSimCore instead, where the exact request range is known.

Function codes follow Delta DVP: FC01/05/15 -> coils (Y, M), FC02 -> X,
FC03/06/16 -> D registers. FC02 on Y/M is not simulated.
"""

from __future__ import annotations

from pymodbus.constants import ExcCodes
from pymodbus.simulator import DataType, SimData, SimDevice
from pymodbus.simulator.simcore import SimCore

from app.delta import Area
from app.tags import TagMap

_FC_AREA = {
    1: Area.COIL, 5: Area.COIL, 15: Area.COIL,
    2: Area.DISCRETE_INPUT,
    3: Area.HOLDING_REGISTER, 6: Area.HOLDING_REGISTER, 16: Area.HOLDING_REGISTER,
}


def allowed_addresses(tags: TagMap) -> dict[Area, frozenset[int]]:
    """Every Modbus address used by the tag map, per area (32-bit tags use 2 registers)."""
    used: dict[Area, set[int]] = {area: set() for area in Area}
    for tag in tags:
        for offset in range(tag.word_count):
            used[tag.addr.area].add(tag.addr.address + offset)
    return {area: frozenset(addrs) for area, addrs in used.items()}


def build_sim_device(tags: TagMap, unit_id: int) -> SimDevice:
    """SimDevice with one zero-initialised entry per tag address."""
    blocks: dict[Area, list[SimData]] = {area: [] for area in Area}
    for area, addrs in allowed_addresses(tags).items():
        for address in sorted(addrs):
            if area is Area.HOLDING_REGISTER:
                blocks[area].append(SimData(address, values=0, datatype=DataType.REGISTERS))
            else:
                blocks[area].append(SimData(address, values=False, datatype=DataType.BITS))

    # pymodbus needs at least one entry per block; StrictSimCore still rejects these.
    bit_placeholder = [SimData(0, values=False, datatype=DataType.BITS)]
    reg_placeholder = [SimData(0, datatype=DataType.INVALID)]
    return SimDevice(
        id=unit_id,
        simdata=(
            blocks[Area.COIL] or bit_placeholder,
            blocks[Area.DISCRETE_INPUT] or bit_placeholder,
            blocks[Area.HOLDING_REGISTER] or reg_placeholder,
            reg_placeholder,  # input registers (FC04): Delta DVP has none
        ),
    )


class StrictSimCore(SimCore):
    """SimCore that only serves addresses from the tag map and the configured unit ID."""

    def __init__(self, tags: TagMap, unit_id: int) -> None:
        super().__init__(build_sim_device(tags, unit_id))
        self.unit_id = unit_id
        self.allowed = allowed_addresses(tags)

    def check_request(self, device_id: int, func_code: int, address: int, count: int) -> ExcCodes | None:
        if device_id != self.unit_id:
            return ExcCodes.GATEWAY_NO_RESPONSE
        area = _FC_AREA.get(func_code)
        if area is None:
            return ExcCodes.ILLEGAL_FUNCTION
        allowed = self.allowed[area]
        if any(address + i not in allowed for i in range(count)):
            return ExcCodes.ILLEGAL_ADDRESS
        return None

    async def async_getValues(self, device_id, func_code, address, count=1):
        if error := self.check_request(device_id, func_code, address, count):
            return error
        return await super().async_getValues(device_id, func_code, address, count)

    async def async_setValues(self, device_id, func_code, address, values):
        if error := self.check_request(device_id, func_code, address, len(values)):
            return error
        return await super().async_setValues(device_id, func_code, address, values)
