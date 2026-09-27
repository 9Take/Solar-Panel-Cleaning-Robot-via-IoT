import asyncio
from pathlib import Path

import pytest
from pymodbus.constants import ExcCodes

from app.delta import Area
from app.sim.datastore import StrictSimCore, allowed_addresses
from app.tags import load_tags

UNIT = 1
TAGS_YAML = """
tags:
  start_cmd:  {device: M10,   dir: write, type: bool}
  high_m:     {device: M1600, dir: rw,    type: bool}
  run_lamp:   {device: Y0,    dir: read,  type: bool}
  estop:      {device: X0,    dir: read,  type: bool}
  battery_v:  {device: D100,  dir: read,  type: int16}
  energy:     {device: D200,  dir: read,  type: int32}
  high_d:     {device: D5000, dir: rw,    type: uint16}
"""


@pytest.fixture
def tags(tmp_path: Path):
    path = tmp_path / "tags.yaml"
    path.write_text(TAGS_YAML, encoding="utf-8")
    return load_tags(path)


@pytest.fixture
def core(tags):
    return StrictSimCore(tags, UNIT)


def run(coro):
    return asyncio.run(coro)


def test_allowed_addresses(tags):
    allowed = allowed_addresses(tags)
    assert allowed[Area.COIL] == {0x080A, 0xB040, 0x0500}          # M10, M1600, Y0
    assert allowed[Area.DISCRETE_INPUT] == {0x0400}                 # X0
    assert allowed[Area.HOLDING_REGISTER] == {0x1064, 0x10C8, 0x10C9, 0x9388}  # D100, D200+D201, D5000


@pytest.mark.parametrize(
    "fc, address, count",
    [
        (1, 0x080A, 1),   # M10
        (1, 0xB040, 1),   # M1600 (upper M range)
        (1, 0x0500, 1),   # Y0
        (2, 0x0400, 1),   # X0
        (3, 0x1064, 1),   # D100
        (3, 0x10C8, 2),   # D200 int32 = 2 registers
        (3, 0x9388, 1),   # D5000 (upper D range)
    ],
)
def test_tag_addresses_readable_and_zero(core, fc, address, count):
    values = run(core.async_getValues(UNIT, fc, address, count))
    assert not isinstance(values, ExcCodes)
    assert list(values) == [0] * count


@pytest.mark.parametrize(
    "fc, address, count",
    [
        (1, 0x080B, 1),   # M11: not in tag map (a coil gap pymodbus would allow)
        (1, 0x080A, 2),   # M10..M11: range runs past the tag
        (1, 0x9000, 1),   # far outside any tag
        (3, 0x1065, 1),   # D101: register gap
        (3, 0x10C9, 2),   # D201..D202: second half of int32 + unknown
        (2, 0x0401, 1),   # X1
        (1, 0x0400, 1),   # X0 via FC01: X is discrete input only
        (3, 0x080A, 1),   # M10 via FC03: M is not a register
    ],
)
def test_unknown_addresses_rejected(core, fc, address, count):
    assert run(core.async_getValues(UNIT, fc, address, count)) is ExcCodes.ILLEGAL_ADDRESS


def test_write_then_read_coil(core):
    assert run(core.async_setValues(UNIT, 5, 0x080A, [True])) is None
    assert run(core.async_getValues(UNIT, 1, 0x080A, 1)) == [True]


def test_write_then_read_register(core):
    assert run(core.async_setValues(UNIT, 16, 0x10C8, [0x1234, 0x0001])) is None
    assert run(core.async_getValues(UNIT, 3, 0x10C8, 2)) == [0x1234, 0x0001]


def test_write_unknown_address_rejected(core):
    assert run(core.async_setValues(UNIT, 5, 0x080B, [True])) is ExcCodes.ILLEGAL_ADDRESS
    assert run(core.async_setValues(UNIT, 5, 0x0400, [True])) is ExcCodes.ILLEGAL_ADDRESS  # X0 not writable


def test_wrong_unit_id(core):
    assert run(core.async_getValues(99, 1, 0x080A, 1)) is ExcCodes.GATEWAY_NO_RESPONSE


def test_input_registers_not_supported(core):
    assert run(core.async_getValues(UNIT, 4, 0x1064, 1)) is ExcCodes.ILLEGAL_FUNCTION


def test_repo_tag_map_builds():
    repo_tags = load_tags(Path(__file__).resolve().parents[1] / "config" / "plc_tags.yaml")
    StrictSimCore(repo_tags, UNIT)


def test_empty_areas_still_build(tmp_path):
    path = tmp_path / "tags.yaml"
    path.write_text("tags:\n  only_d: {device: D0, dir: read, type: int16}\n", encoding="utf-8")
    core = StrictSimCore(load_tags(path), UNIT)
    assert run(core.async_getValues(UNIT, 1, 0, 1)) is ExcCodes.ILLEGAL_ADDRESS  # placeholder hidden
