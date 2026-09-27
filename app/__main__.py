"""Entry point: `python -m app` — the gateway service.

Polls the PLC (read-only), logs changes, and records history in SQLite.
Runs until SIGINT/SIGTERM (`docker compose down`).
"""

import asyncio
import logging
import signal

from app.config import Settings
from app.history import HistoryStore
from app.plc_client import PlcClient
from app.poller import Poller
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
    task = asyncio.create_task(poller.run_forever())

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    log.info("Gateway stopping")
    task.cancel()
    plc.close()
    store.close()


if __name__ == "__main__":
    asyncio.run(main())
