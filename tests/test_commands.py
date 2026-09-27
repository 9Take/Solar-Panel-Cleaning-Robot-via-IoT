"""Step 5: commands under the safety rules, against the mock PLC + simulated robot."""

import asyncio
import contextlib
from pathlib import Path

import pytest

from app import command_queue
from app.command_queue import CommandQueueWorker
from app.commander import PlcCommander
from app.history import HistoryStore
from app.plc_client import PlcClient
from app.robot import State
from app.sim.plant import Plant, PlantParams
from app.sim.runner import SimRunner
from app.sim.server import MockPlcServer
from app.tags import load_tags

UNIT = 1
TAGS = load_tags(Path(__file__).resolve().parents[1] / "config" / "plc_tags.yaml")
TICK = 0.05


def with_robot(scenario, ladder_running=True, battery=90.0, travel_s=2.0):
    """Mock PLC + simulated robot ticking in the background; ladder_running=False = PLC in STOP mode."""

    async def main():
        server = MockPlcServer(TAGS, UNIT, "127.0.0.1", 0)
        await server.serve_forever(background=True)
        plant = Plant(PlantParams(travel_s=travel_s, start_battery_pct=battery, heartbeat_period_s=0.1,
                                  drain_pct_per_s=0, solar_pct_per_s=0))
        runner = SimRunner(server.core, TAGS, UNIT, plant=plant)
        await runner.start_defaults(cycles_setpoint=1)
        for _ in range(5):                               # boot: heartbeat seen, state decided
            await runner.tick(TICK)

        async def tick_forever():
            while True:
                await asyncio.sleep(TICK)
                await runner.tick(TICK)

        ticker = asyncio.create_task(tick_forever()) if ladder_running else None
        plc = PlcClient(TAGS, "127.0.0.1", server.bound_port, UNIT, timeout_s=1)
        commander = PlcCommander(plc, ack_timeout_s=0.5, confirm_s=1.0, poll_s=0.05)
        try:
            await scenario(commander, plc, runner)
        finally:
            if ticker:
                ticker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ticker
            plc.close()
            await server.shutdown()

    asyncio.run(main())


# --- pulses and acknowledgement ------------------------------------------------------

def test_start_from_home():
    async def scenario(cmd, plc, _runner):
        result = await cmd.execute("start")
        assert result.status == "done", result
        assert await plc.read("robot_state") == State.CLEANING
        assert await plc.read("cmd_start") is False

    with_robot(scenario)


def test_not_acknowledged_clears_the_request_bit():
    """PLC in STOP mode: nobody clears the bit, so the Pi must, or it fires on RUN."""
    async def scenario(cmd, plc, _runner):
        result = await cmd.execute("start")
        assert result.status == "not_acknowledged"
        assert "request cleared" in result.message
        assert await plc.read("cmd_start") is False

    with_robot(scenario, ladder_running=False)


def test_start_acknowledged_but_battery_too_low():
    async def scenario(cmd, plc, _runner):
        result = await cmd.execute("start")
        assert result.status == "not_started"
        assert "battery 70% is below 80%" in result.message
        assert await plc.read("robot_state") == State.HOME

    with_robot(scenario, battery=70)


# --- pre-checks (rule 6) --------------------------------------------------------------

def test_start_rejected_while_estop_pressed():
    async def scenario(cmd, plc, runner):
        runner.plant.press_estop()
        await asyncio.sleep(0.2)
        result = await cmd.execute("start")
        assert result.status == "rejected" and "E-stop" in result.message
        assert await plc.read("cmd_start") is False, "nothing was written"

    with_robot(scenario)


def test_start_rejected_while_already_cleaning():
    async def scenario(cmd, _plc, _runner):
        assert (await cmd.execute("start")).ok
        cmd._last_sent.clear()                      # skip debounce for this test
        result = await cmd.execute("start")
        assert result.status == "rejected" and "already Cleaning" in result.message

    with_robot(scenario)


def test_reset_needs_alarm_and_released_estop():
    async def scenario(cmd, _plc, runner):
        assert (await cmd.execute("reset")).message == "no alarm to reset"
        runner.plant.press_estop()
        await asyncio.sleep(0.2)
        cmd._last_sent.clear()
        assert (await cmd.execute("reset")).message == "release the E-stop first"
        runner.plant.release_estop()
        await asyncio.sleep(0.1)
        cmd._last_sent.clear()
        result = await cmd.execute("reset")
        assert result.status == "done", result

    with_robot(scenario)


def test_return_while_cleaning_and_already_home():
    async def scenario(cmd, _plc, _runner):
        assert (await cmd.execute("return")).message == "already at home"
        assert (await cmd.execute("start")).ok
        await asyncio.sleep(0.5)
        result = await cmd.execute("return")
        assert result.status == "done", result

    with_robot(scenario, travel_s=5)


# --- stop is never blocked (rule 5) -------------------------------------------------------

def test_stop_while_cleaning():
    async def scenario(cmd, plc, _runner):
        assert (await cmd.execute("start")).ok
        await asyncio.sleep(0.3)
        result = await cmd.execute("stop")
        assert result.status == "done" and result.message == "stopped"
        assert await plc.read("drive_run") is False
        assert await plc.read("robot_state") == State.IDLE

    with_robot(scenario, travel_s=5)


def test_stop_runs_while_another_command_holds_the_lock():
    async def scenario(cmd, plc, _runner):
        start = asyncio.create_task(cmd.execute("start"))
        await asyncio.sleep(0.15)                 # start is waiting for confirmation, lock held
        assert cmd._lock.locked()
        stop = await cmd.execute("stop")
        assert stop.status == "done", stop
        await start
        await asyncio.sleep(0.2)
        assert await plc.read("drive_run") is False

    with_robot(scenario, travel_s=5)


def test_stop_is_not_debounced():
    async def scenario(cmd, _plc, _runner):
        first = await cmd.execute("stop")
        second = await cmd.execute("stop")
        assert first.status != "ignored" and second.status != "ignored"

    with_robot(scenario)


# --- lock / debounce (rule 7), whitelist (rule 1), setpoints (rule 10) --------------------

def test_second_command_rejected_while_busy_and_debounced():
    async def scenario(cmd, _plc, _runner):
        first = asyncio.create_task(cmd.execute("start"))
        await asyncio.sleep(0.05)
        busy = await cmd.execute("return")
        assert busy.status == "rejected" and "in progress" in busy.message
        assert (await first).ok
        again = await cmd.execute("start")
        assert again.status == "ignored"

    with_robot(scenario)


def test_unknown_command_rejected():
    async def scenario(cmd, _plc, _runner):
        assert (await cmd.execute("jump")).status == "rejected"

    with_robot(scenario)


@pytest.mark.parametrize("value", [0, 101, 2.5, "abc", None, True])
def test_cycles_out_of_range(value):
    async def scenario(cmd, plc, _runner):
        result = await cmd.execute("cycles", value)
        assert result.status == "rejected", result
        assert await plc.read("cycles_setpoint") == 1, "unchanged"

    with_robot(scenario)


@pytest.mark.parametrize("value", [5, "5", 5.0])
def test_cycles_set(value):
    async def scenario(cmd, plc, _runner):
        assert (await cmd.execute("cycles", value)).ok
        assert await plc.read("cycles_setpoint") == 5

    with_robot(scenario)


def test_write_guard_in_client():
    async def scenario(_cmd, plc, _runner):
        with pytest.raises(PermissionError):
            await plc._write("robot_state", 1)          # dir: read
        with pytest.raises(PermissionError):
            await plc._write("estop_ok", True)          # PLC input

    with_robot(scenario)


def test_commander_refuses_read_only_command_tags(tmp_path):
    body = (Path(__file__).resolve().parents[1] / "config" / "plc_tags.yaml").read_text(encoding="utf-8")
    body = body.replace('cmd_start:        {device: M100, dir: write', 'cmd_start:        {device: M100, dir: read')
    (tmp_path / "t.yaml").write_text(body, encoding="utf-8")

    async def main():
        plc = PlcClient(load_tags(tmp_path / "t.yaml"), "127.0.0.1", 1)
        with pytest.raises(ValueError, match="read-only"):
            PlcCommander(plc)
        plc.close()

    asyncio.run(main())


def test_stop_wins_over_start_in_the_same_scan():
    """Both request bits set before the ladder scans: the robot must not move."""
    async def scenario(_cmd, plc, _runner):
        await plc._write("cmd_start", True)
        await plc._write("cmd_stop", True)
        await asyncio.sleep(0.3)
        assert await plc.read("robot_state") == State.HOME
        assert await plc.read("drive_run") is False

    with_robot(scenario)


def test_offline_is_an_error_result():
    async def main():
        plc = PlcClient(TAGS, "127.0.0.1", 1, UNIT, timeout_s=0.3)
        result = await PlcCommander(plc).execute("stop")
        assert result.status == "error" and "offline" in result.message
        plc.close()

    asyncio.run(main())


# --- queue (dashboard path) -----------------------------------------------------------------

def test_queue_executes_and_audits():
    async def scenario(cmd, _plc, _runner):
        store = HistoryStore(":memory:")
        worker = CommandQueueWorker(store, cmd)
        cid = command_queue.enqueue(store, "start", source="dashboard")
        await worker.check_once()
        assert command_queue.get(store, cid)["status"] == "running"
        await asyncio.gather(*worker.tasks)
        row = command_queue.get(store, cid)
        assert row["status"] == "done" and row["done_ts"]
        events = store.events()
        assert events[-1]["kind"] == "command"
        assert events[-1]["message"].startswith("start from dashboard: done")
        store.close()

    with_robot(scenario)


def test_queue_expires_stale_commands():
    async def scenario(cmd, plc, _runner):
        store = HistoryStore(":memory:")
        worker = CommandQueueWorker(store, cmd, expiry_s=5)
        import time
        cid = command_queue.enqueue(store, "start", source="dashboard", ts=time.time() - 30)
        await worker.check_once()
        assert command_queue.get(store, cid)["status"] == "expired"
        assert await plc.read("robot_state") == State.HOME, "never sent"
        store.close()

    with_robot(scenario)


def test_queue_takes_stop_first():
    store = HistoryStore(":memory:")
    command_queue.ensure_schema(store)
    started = []

    class Recorder:
        async def execute(self, command, value=None):
            started.append(command)
            from app.commander import CommandResult
            return CommandResult(command, "done", "ok")

    async def main():
        command_queue.enqueue(store, "start", source="a")
        command_queue.enqueue(store, "cycles", 3, source="a")
        command_queue.enqueue(store, "stop", source="b")
        worker = CommandQueueWorker(store, Recorder())
        await worker.check_once()
        await asyncio.gather(*worker.tasks)

    asyncio.run(main())
    assert started[0] == "stop"
    store.close()


def test_rows_left_running_after_crash_are_not_retried():
    store = HistoryStore(":memory:")
    cid = command_queue.enqueue(store, "start", source="dashboard")
    store.db.execute("UPDATE commands SET status = 'running' WHERE id = ?", (cid,))

    async def main():
        worker = CommandQueueWorker(store, object())
        task = asyncio.create_task(worker.run_forever())
        await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(main())
    assert command_queue.get(store, cid)["status"] == "error"
    store.close()
