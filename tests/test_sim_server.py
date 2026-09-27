"""Mock PLC over real TCP: pymodbus client <-> MockPlcServer on a random port."""

import asyncio
from pathlib import Path

import pytest
from pymodbus.client import AsyncModbusTcpClient

from app.sim.server import MockPlcServer
from app.tags import load_tags

UNIT = 1
TAGS_YAML = """
tags:
  start_cmd: {device: M10,  dir: write, type: bool}
  estop:     {device: X0,   dir: read,  type: bool}
  battery_v: {device: D100, dir: read,  type: int16}
  energy:    {device: D200, dir: read,  type: int32}
"""


@pytest.fixture
def tags(tmp_path: Path):
    path = tmp_path / "tags.yaml"
    path.write_text(TAGS_YAML, encoding="utf-8")
    return load_tags(path)


def with_server(tags, scenario):
    """Start the mock on 127.0.0.1:<random>, run scenario(client, server), always shut down."""

    async def main():
        server = MockPlcServer(tags, UNIT, "127.0.0.1", 0)
        await server.serve_forever(background=True)
        client = AsyncModbusTcpClient("127.0.0.1", port=server.bound_port, timeout=2, retries=0)
        try:
            assert await client.connect()
            await scenario(client, server)
        finally:
            client.close()
            await server.shutdown()

    asyncio.run(main())


def test_read_initial_values(tags):
    async def scenario(client, _server):
        r = await client.read_coils(0x080A, count=1, device_id=UNIT)            # M10
        assert not r.isError() and r.bits[0] is False
        r = await client.read_discrete_inputs(0x0400, count=1, device_id=UNIT)  # X0
        assert not r.isError() and r.bits[0] is False
        r = await client.read_holding_registers(0x10C8, count=2, device_id=UNIT)  # D200 int32
        assert not r.isError() and r.registers == [0, 0]

    with_server(tags, scenario)


def test_write_coil_and_register(tags):
    async def scenario(client, _server):
        assert not (await client.write_coil(0x080A, True, device_id=UNIT)).isError()
        assert (await client.read_coils(0x080A, count=1, device_id=UNIT)).bits[0] is True
        assert not (await client.write_register(0x1064, 245, device_id=UNIT)).isError()
        assert (await client.read_holding_registers(0x1064, count=1, device_id=UNIT)).registers == [245]

    with_server(tags, scenario)


def test_server_side_update_visible_to_client(tags):
    """2c behavior will change values from the server side; the client must see them."""

    async def scenario(client, server):
        assert await server.core.async_setValues(UNIT, 16, 0x1064, [123]) is None
        assert (await client.read_holding_registers(0x1064, count=1, device_id=UNIT)).registers == [123]

    with_server(tags, scenario)


@pytest.mark.parametrize(
    "call, exc_code",
    [
        (lambda c: c.read_coils(0x080B, count=1, device_id=UNIT), 2),               # M11 not in map
        (lambda c: c.read_holding_registers(0x1065, count=1, device_id=UNIT), 2),   # D101 not in map
        (lambda c: c.write_coil(0x0400, True, device_id=UNIT), 2),                  # X0 not writable
        (lambda c: c.read_input_registers(0x1064, count=1, device_id=UNIT), 1),     # FC04 unsupported
        (lambda c: c.read_coils(0x080A, count=1, device_id=UNIT + 1), 0x0B),        # wrong unit ID
    ],
)
def test_errors_over_tcp(tags, call, exc_code):
    async def scenario(client, _server):
        r = await call(client)
        assert r.isError()
        assert r.exception_code == exc_code

    with_server(tags, scenario)
