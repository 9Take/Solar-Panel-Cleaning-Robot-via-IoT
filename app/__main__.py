"""Entry point: `python -m app` — the gateway service.

Polls the PLC, logs changes, records history in SQLite, executes commands
queued in the `commands` table (dashboard / CLI), and feeds the PLC the battery %
from Tuya Cloud plus the Pi heartbeat (only when the TUYA_* settings are filled in),
and starts timed cleaning runs from the `schedules` table (python -m app.schedule).
Runs until SIGINT/SIGTERM (`docker compose down`).
"""

import asyncio
import logging
import signal
from zoneinfo import ZoneInfo

from app.battery import BatteryFeeder, tuya_battery_source
from app.command_queue import CommandQueueWorker
from app.commander import PlcCommander
from app.config import Settings
from app.history import HistoryStore
from app.plc_client import PlcClient
from app.poller import Poller
from app.schedule import Scheduler
from app.tags import load_tags


async def main() -> None:
    settings = Settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("gateway")

    tags = load_tags(settings.plc_tags_file)
    log.info("Gateway starting: PLC %s:%d unit=%d, %d tag(s), poll %.1fs, snapshot %.0fs, history %s",
             settings.plc_host, settings.plc_port, settings.plc_unit_id, len(tags),
             settings.plc_poll_interval_s, settings.snapshot_interval_s, settings.history_db)

    store = HistoryStore(settings.history_db)
    plc = PlcClient.from_settings(settings, tags)
    poller = Poller(plc, store, settings.plc_poll_interval_s,
                    settings.snapshot_interval_s, settings.history_retention_days)
    commander = PlcCommander(plc)
    worker = CommandQueueWorker(store, commander)
    scheduler = Scheduler(store, commander, ZoneInfo(settings.schedule_tz))
    tasks = [asyncio.create_task(poller.run_forever()), asyncio.create_task(worker.run_forever()),
             asyncio.create_task(scheduler.run_forever())]

    missing = settings.missing_tuya_keys()
    if missing:
        log.warning("Battery feed disabled (%s empty): the PLC gets no battery_pct / pi_heartbeat "
                    "from this gateway", ", ".join(missing))
    else:
        feeder = BatteryFeeder(plc, tuya_battery_source(settings), store,
                               settings.pi_heartbeat_interval_s, settings.tuya_poll_interval_s,
                               settings.battery_max_age_s)
        tasks.append(asyncio.create_task(feeder.run_forever()))
        log.info("Battery feed on: Tuya DP %r every %.0fs, heartbeat every %.1fs",
                 settings.tuya_battery_dp, settings.tuya_poll_interval_s, settings.pi_heartbeat_interval_s)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    log.info("Gateway stopping")
    for task in tasks:
        task.cancel()
    plc.close()
    store.close()


if __name__ == "__main__":
    asyncio.run(main())
