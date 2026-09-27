"""Delta DVP device name -> Modbus address conversion.

Address map verified against Delta "DVP Series PLC Communication Protocol"
(device address table). D is capped at D9999, the highest address listed there.

    X0-X377  (octal)  0x0400  bit, read-only input
    Y0-Y377  (octal)  0x0500  bit, output
    M0-M1535          0x0800  bit
    M1536-M4095       0xB000  bit
    D0-D4095          0x1000  16-bit register
    D4096-D9999       0x9000  16-bit register
"""

import re
from dataclasses import dataclass
from enum import Enum


class Area(str, Enum):
    COIL = "coil"                  # bit, FC01 read / FC05, FC15 write
    DISCRETE_INPUT = "discrete"    # bit, FC02 read only
    HOLDING_REGISTER = "holding"   # 16-bit, FC03 read / FC06, FC16 write


@dataclass(frozen=True)
class ModbusAddress:
    device: str
    area: Area
    address: int

    @property
    def is_bit(self) -> bool:
        return self.area in (Area.COIL, Area.DISCRETE_INPUT)


_DEVICE_RE = re.compile(r"^([XYMD])(\d+)$")
_MAX_XY_OCTAL = 0o377


def to_modbus(device: str) -> ModbusAddress:
    """Convert a Delta device name (e.g. "M100", "D200", "X10") to a Modbus address."""
    name = device.strip().upper()
    match = _DEVICE_RE.match(name)
    if not match:
        raise ValueError(f"Unsupported Delta device: {device!r} (supported: X, Y, M, D)")
    kind, digits = match.group(1), match.group(2)

    if kind in ("X", "Y"):
        if any(c in "89" for c in digits):
            raise ValueError(f"{name}: X/Y numbers are octal (digits 0-7 only)")
        n = int(digits, 8)
        if n > _MAX_XY_OCTAL:
            raise ValueError(f"{name}: out of range (max {kind}377)")
        if kind == "X":
            return ModbusAddress(name, Area.DISCRETE_INPUT, 0x0400 + n)
        return ModbusAddress(name, Area.COIL, 0x0500 + n)

    n = int(digits)
    if kind == "M":
        if n <= 1535:
            return ModbusAddress(name, Area.COIL, 0x0800 + n)
        if n <= 4095:
            return ModbusAddress(name, Area.COIL, 0xB000 + (n - 1536))
        raise ValueError(f"{name}: out of range (max M4095)")

    # kind == "D"
    if n <= 4095:
        return ModbusAddress(name, Area.HOLDING_REGISTER, 0x1000 + n)
    if n <= 9999:
        return ModbusAddress(name, Area.HOLDING_REGISTER, 0x9000 + (n - 4096))
    raise ValueError(f"{name}: out of range (max D9999)")
