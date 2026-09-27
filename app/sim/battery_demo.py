"""Manual test for step 6: battery feed + heartbeat against the mock PLC.

    python -m app.sim.battery_demo            (or: docker compose run --rm gateway python -m app.sim.battery_demo)

No Tuya account needed: a fake Tuya source follows a fixed timeline. Everything
else is real: BatteryFeeder with the real battery_to_feed() policy, Modbus TCP
writes to the mock PLC, and the simulated ladder's heartbeat watchdog.
Times are shortened (heartbeat 0.5 s, ladder timeout 3 s, max age 3 s) so the
run takes about 30 s. Prints one row per second and PASS/FAIL per phase.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from app.battery import BatteryFeeder
from app.plc_client import PlcClient
from app.robot import Alarm, describe
from app.sim.ladder import LadderParams, LadderSim
from app.sim.plant import Plant, PlantParams
from app.sim.runner import SimRunner
from app.sim.server import MockPlcServer
from app.tags import load_tags
from app.tuya import TuyaError

UNIT = 1
HEARTBEAT_S = 0.5
TUYA_POLL_S = 1.0
MAX_AGE_S = 3.0
LADDER_TIMEOUT_S = 3.0


@dataclass(frozen=True)
class Phase:
    seconds: float
    tuya: float | None        # value Tuya reports; None = Tuya request fails
    title: str
    expect_alarm: Alarm       # alarm_code expected at the end of the phase


TIMELINE = (
    Phase(6, 88.0, "Tuya OK 88 %", Alarm.NONE),
    Phase(9, None, "Tuya fails (reading goes stale)", Alarm.HEARTBEAT_LOST),
    Phase(5, 86.0, "Tuya back, 86 %", Alarm.NONE),
    Phase(8, -1.0, "Tuya reports -1 (invalid)", Alarm.HEARTBEAT_LOST),
    Phase(5, 15.0, "Tuya 15 % (critically low)", Alarm.BATTERY_CRITICAL),
)


async def run(tags_file: Path) -> bool:
    tags = load_tags(tags_file)
    server = MockPlcServer(tags, UNIT, "127.0.0.1", 0)
    await server.serve_forever(background=True)
    runner = SimRunner(server.core, tags, UNIT,
                       ladder=LadderSim(LadderParams(heartbeat_timeout_s=LADDER_TIMEOUT_S)),
                       plant=Plant(PlantParams(fake_pi=False)))    # the feeder is the only writer
    await runner.start_defaults(cycles_setpoint=1)
    plc = PlcClient(tags, "127.0.0.1", server.bound_port, UNIT, timeout_s=1)

    current: list[Phase] = [TIMELINE[0]]

    async def fake_tuya() -> float:
        if current[0].tuya is None:
            raise TuyaError("fake: Tuya Cloud unreachable")
        return current[0].tuya

    feeder = BatteryFeeder(plc, fake_tuya, heartbeat_interval_s=HEARTBEAT_S,
                           poll_interval_s=TUYA_POLL_S, max_age_s=MAX_AGE_S)
    tasks = [asyncio.create_task(runner.run_forever(0.1)), asyncio.create_task(feeder.run_forever())]

    print(f"{'t':>3}  {'phase':<32} {'battery_pct':>11} {'heartbeat':>9}  alarm_code")
    results: list[tuple[Phase, Alarm]] = []
    t = 0
    try:
        for phase in TIMELINE:
            current[0] = phase
            for _ in range(int(phase.seconds)):
                await asyncio.sleep(1)
                t += 1
                v = await plc.read_many(["battery_pct", "pi_heartbeat", "alarm_code"])
                print(f"{t:>3}  {phase.title:<32} {v['battery_pct']:>11} {v['pi_heartbeat']:>9}  "
                      f"{describe(Alarm, v['alarm_code'])}")
            results.append((phase, Alarm(v["alarm_code"])))
    finally:
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        plc.close()
        await server.shutdown()

    print()
    ok = True
    for phase, alarm in results:
        passed = alarm == phase.expect_alarm
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'}  {phase.title:<32} expected {describe(Alarm, phase.expect_alarm)}, "
              f"got {describe(Alarm, alarm)}")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tags", type=Path, default=Path("config/plc_tags.yaml"), help="tag map file")
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)      # keep feeder log lines in order with the table
    logging.basicConfig(level=logging.INFO, format="      [feeder] %(message)s", stream=sys.stdout)
    raise SystemExit(0 if asyncio.run(run(args.tags)) else 1)


if __name__ == "__main__":
    main()
