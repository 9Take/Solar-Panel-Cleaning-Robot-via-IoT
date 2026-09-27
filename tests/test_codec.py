import pytest

from app.codec import decode, encode
from app.tags import Tag


def tag(type_: str, device: str = "D0", scale: float = 1.0) -> Tag:
    return Tag(name="t", device=device, dir="rw", type=type_, scale=scale)


@pytest.mark.parametrize(
    "type_, value, raw",
    [
        ("int16", 0, [0]),
        ("int16", 1234, [1234]),
        ("int16", -1, [0xFFFF]),
        ("int16", -32768, [0x8000]),
        ("uint16", 65535, [0xFFFF]),
        ("int32", 0x12345678, [0x5678, 0x1234]),      # Delta: low word first
        ("int32", -2, [0xFFFE, 0xFFFF]),
        ("uint32", 100_000, [0x86A0, 0x0001]),
        ("float32", 1.5, [0x0000, 0x3FC0]),           # 0x3FC00000, low word first
    ],
)
def test_round_trip(type_, value, raw):
    t = tag(type_)
    assert encode(t, value) == raw
    assert decode(t, raw) == value


def test_bool():
    t = tag("bool", device="M0")
    assert encode(t, True) is True
    assert decode(t, 1) is True
    with pytest.raises(ValueError):
        encode(t, 2)


def test_scale():
    t = tag("int16", scale=0.01)
    assert encode(t, 2.45) == [245]
    assert decode(t, [245]) == 2.45
    assert decode(t, [0xFFFF]) == -0.01


def test_scaled_uint32():
    t = tag("uint32", scale=0.1)
    assert encode(t, 1234.5) == [12345, 0]
    assert decode(t, [12345, 0]) == 1234.5


@pytest.mark.parametrize(
    "type_, value",
    [("int16", 32768), ("int16", -32769), ("uint16", -1), ("uint16", 65536), ("uint32", -1)],
)
def test_out_of_range(type_, value):
    with pytest.raises(ValueError, match="out of range"):
        encode(tag(type_), value)


def test_wrong_register_count():
    with pytest.raises(ValueError, match="expected 2"):
        decode(tag("int32"), [1])
