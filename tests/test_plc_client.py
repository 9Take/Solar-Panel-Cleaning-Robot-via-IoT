import asyncio
import socket
from pathlib import Path

import pytest

from app.delta import Area
from app.plc_client import MAX_WORDS_PER_READ, PlcClient, PlcOfflineError, PlcReadError, plan_reads
from app.sim.ladder import State
from app.sim.runner import SimRunner
from app.sim.server import MockPlcServer
from app.tags import load_tags

UNIT = 1
REPO_TAGS = load_tags(Path(__file__).resolve().parents[1] / "config" / "plc_tags.yaml")


def tags_from(tmp_path: Path, body: str):
    path = tmp_path / "tags.yaml"
    path.write_text("tags:\n" + body, encoding="utf-8")
    return load_tags(path)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


# --- plan_reads ----------------------------------------------------------------

def test_plan_merges_contiguous_and_splits_gaps():
    blocks = plan_reads(REPO_TAGS)
    summary = {(b.area, b.address, b.count) for b in blocks}
    assert (Area.DISCRETE_INPUT, 0x0400, 5) in summary      # X0..X4
    assert (Area.COIL, 0x0500, 2) in summary                # Y0, Y1
    assert (Area.COIL, 0x0864, 4) in summary                # M100..M103
    assert (Area.HOLDING_REGISTER, 0x100A, 4) in summary    # D10..D13
    assert (Area.HOLDING_REGISTER, 0x1014, 6) in summary    # D20..D25 (two uint32)
    assert (Area.HOLDING_REGISTER, 0x1065, 1) in summary    # D101 alone
    assert (Area.HOLDING_REGISTER, 0x106E, 2) in summary    # D110, D111
    assert len(blocks) == 7


def test_plan_never_spans_a_gap(tmp_path):
    tags = tags_from(tmp_path, "  a: {device: D0, dir: read, type: int16}\n"
                               "  b: {device: D2, dir: read, type: int16}\n")
    assert [(b.address, b.count) for b in plan_reads(tags)] == [(0x1000, 1), (0x1002, 1)]


def test_plan_respects_request_size_limit(tmp_path):
    body = "".join(f"  d{i}: {{device: D{i}, dir: read, type: int16}}\n" for i in range(MAX_WORDS_PER_READ + 5))
    blocks = plan_reads(tags_from(tmp_path, body))
    assert [b.count for b in blocks] == [MAX_WORDS_PER_READ, 5]


def test_plan_dedupes():
    tag = REPO_TAGS["robot_state"]
    assert len(plan_reads([tag, tag])) == 1


# --- against the mock PLC --------------------------------------------------------

def with_mock(tags, scenario, client_tags=None, simulate=False):
    async def main():
        server = MockPlcServer(tags, UNIT, "127.0.0.1", 0)
        await server.serve_forever(background=True)
        runner = SimRunner(server.core, tags, UNIT) if simulate else None
        plc = PlcClient(client_tags or tags, "127.0.0.1", server.bound_port, UNIT, timeout_s=1)
        try:
            await scenario(plc, server, runner)
        finally:
            plc.close()
            await server.shutdown()

    asyncio.run(main())


def test_read_values_by_name():
    async def scenario(plc, server, _runner):
        await server.core.async_setValues(UNIT, 16, REPO_TAGS["pzem_voltage"].addr.address, [5304])
        await server.core.async_setValues(UNIT, 16, REPO_TAGS["pzem_power"].addr.address, [0x86A0, 0x0001])
        await server.core.async_setValues(UNIT, 2, REPO_TAGS["estop_ok"].addr.address, [True])
        assert await plc.read("pzem_voltage") == 53.04          # scale 0.01
        assert await plc.read("pzem_power") == 10000.0         # uint32 low word first, scale 0.1
        assert await plc.read("estop_ok") is True
        assert await plc.read("drive_run") is False
        assert plc.online

    with_mock(REPO_TAGS, scenario)


def test_read_all_uses_one_request_per_block():
    async def scenario(plc, _server, _runner):
        calls = []
        for method in ("read_coils", "read_discrete_inputs", "read_holding_registers"):
            original = getattr(plc._client, method)

            async def spy(*args, _original=original, **kwargs):
                calls.append(args)
                return await _original(*args, **kwargs)
            setattr(plc._client, method, spy)

        values = await plc.read_all()
        assert set(values) == set(REPO_TAGS.tags)
        assert len(calls) == 7

    with_mock(REPO_TAGS, scenario)


def test_reads_live_simulation():
    async def scenario(plc, _server, runner):
        for _ in range(15):
            await runner.tick(0.2)
        values = await plc.read_many(["robot_state", "limit_1", "battery_pct"])
        assert values == {"robot_state": State.HOME, "limit_1": True, "battery_pct": 90}

    with_mock(REPO_TAGS, scenario, simulate=True)


def test_unknown_tag_name():
    async def scenario(plc, _server, _runner):
        with pytest.raises(KeyError, match="Unknown tag"):
            await plc.read("no_such_tag")

    with_mock(REPO_TAGS, scenario)


def test_modbus_exception_is_read_error_not_offline(tmp_path):
    server_tags = tags_from(tmp_path, "  a: {device: D0, dir: read, type: int16}\n")
    (tmp_path / "client.yaml").write_text(
        "tags:\n  a: {device: D0, dir: read, type: int16}\n  ghost: {device: D5, dir: read, type: int16}\n",
        encoding="utf-8")
    client_tags = load_tags(tmp_path / "client.yaml")

    async def scenario(plc, _server, _runner):
        with pytest.raises(PlcReadError, match="ghost.*exception 2"):
            await plc.read("ghost")
        assert plc.online
        assert await plc.read("a") == 0

    with_mock(server_tags, scenario, client_tags=client_tags)


# --- offline and reconnect ---------------------------------------------------------

def test_offline_then_backoff():
    clock = FakeClock()

    async def main():
        plc = PlcClient(REPO_TAGS, "127.0.0.1", free_port(), UNIT, timeout_s=0.5,
                        backoff_initial_s=1, backoff_max_s=4, clock=clock)
        attempts = []
        original = plc._client.connect

        async def counting_connect():
            attempts.append(clock.now)
            return await original()
        plc._client.connect = counting_connect

        with pytest.raises(PlcOfflineError):
            await plc.read("robot_state")
        assert len(attempts) == 1

        with pytest.raises(PlcOfflineError):
            await plc.read("robot_state")
        assert len(attempts) == 1, "no new attempt inside the 1 s backoff"

        clock.now += 1
        with pytest.raises(PlcOfflineError):
            await plc.read("robot_state")
        assert len(attempts) == 2

        clock.now += 1.5
        with pytest.raises(PlcOfflineError):
            await plc.read("robot_state")
        assert len(attempts) == 2, "backoff doubled to 2 s"
        plc.close()

    asyncio.run(main())


def test_reconnects_after_plc_restart():
    async def main():
        port = free_port()
        server = MockPlcServer(REPO_TAGS, UNIT, "127.0.0.1", port)
        await server.serve_forever(background=True)
        plc = PlcClient(REPO_TAGS, "127.0.0.1", port, UNIT, timeout_s=0.5, backoff_initial_s=0.1)
        try:
            assert await plc.read("robot_state") == 0

            await server.shutdown()                 # PLC power loss
            await asyncio.sleep(0.1)
            with pytest.raises(PlcOfflineError):
                await plc.read("robot_state")
            assert not plc.online

            server = MockPlcServer(REPO_TAGS, UNIT, "127.0.0.1", port)
            await server.serve_forever(background=True)
            await asyncio.sleep(0.2)
            for _ in range(20):                     # backoff may skip a call or two
                try:
                    assert await plc.read("robot_state") == 0
                    break
                except PlcOfflineError:
                    await asyncio.sleep(0.1)
            else:
                pytest.fail("client did not reconnect")
            assert plc.online
        finally:
            plc.close()
            await server.shutdown()

    asyncio.run(main())
