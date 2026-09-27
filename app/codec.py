"""Tag value <-> raw Modbus value, shared by the mock PLC and the PLC client.

Raw value: bool for bit tags, list[int] of 16-bit registers for word tags.
Engineering value = raw integer * tag.scale.

Delta DVP stores 32-bit values low word first: D(n) = low 16 bits, D(n+1) = high.
"""

from __future__ import annotations

import struct

from app.tags import Tag, TagType

Raw = bool | list[int]
Value = bool | int | float

_INT_RANGE = {
    TagType.INT16: (-0x8000, 0x7FFF),
    TagType.UINT16: (0, 0xFFFF),
    TagType.INT32: (-0x8000_0000, 0x7FFF_FFFF),
    TagType.UINT32: (0, 0xFFFF_FFFF),
}


def decode(tag: Tag, raw: Raw) -> Value:
    if tag.type is TagType.BOOL:
        return bool(raw)
    regs = list(raw)
    if len(regs) != tag.word_count:
        raise ValueError(f"{tag.name}: expected {tag.word_count} register(s), got {len(regs)}")

    if tag.type is TagType.FLOAT32:
        number: int | float = struct.unpack("<f", struct.pack("<HH", regs[0], regs[1]))[0]
    else:
        number = regs[0] if tag.word_count == 1 else regs[0] | (regs[1] << 16)
        low, _ = _INT_RANGE[tag.type]
        if low < 0 and number >= -low:  # two's complement
            number -= 2 * -low

    if tag.scale == 1:
        return number
    return round(number * tag.scale, 9)  # trim float noise like 2.4500000000000002


def encode(tag: Tag, value: Value) -> Raw:
    if tag.type is TagType.BOOL:
        if value not in (0, 1):
            raise ValueError(f"{tag.name}: bool tag needs True/False, got {value!r}")
        return bool(value)

    raw_number = value / tag.scale if tag.scale != 1 else value
    if tag.type is TagType.FLOAT32:
        low_word, high_word = struct.unpack("<HH", struct.pack("<f", raw_number))
        return [low_word, high_word]

    number = round(raw_number)
    low, high = _INT_RANGE[tag.type]
    if not low <= number <= high:
        raise ValueError(f"{tag.name}: {value!r} out of range for {tag.type.value} (scale {tag.scale})")
    if tag.word_count == 1:
        return [number & 0xFFFF]
    number &= 0xFFFF_FFFF
    return [number & 0xFFFF, number >> 16]
