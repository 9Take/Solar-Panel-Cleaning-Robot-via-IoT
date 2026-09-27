"""Entry point: `python -m app.sim` — run the mock PLC until SIGINT/SIGTERM."""

import asyncio
import logging
import signal

from app.config import Settings
from app.sim.plant import Plant, PlantParams
from app.sim.runner import SimRunner
from app.sim.server import MockPlcServer
from app.tags import load_tags


async def main() -> None:
    settings = Settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("plc-sim")

    tags = load_tags(settings.plc_tags_file)
    server = MockPlcServer(tags, settings.plc_unit_id, settings.sim_host, settings.sim_port)
    await server.serve_forever(background=True)
    log.info("Mock PLC listening on %s:%d unit=%d with %d tag(s)",
             settings.sim_host, server.bound_port, settings.plc_unit_id, len(tags))

    sim_task = None
    if settings.sim_behavior:
        plant = Plant(PlantParams(travel_s=settings.sim_travel_s, fake_pi=settings.sim_fake_pi_battery))
        runner = SimRunner(server.core, tags, settings.plc_unit_id, plant=plant)
        await runner.start_defaults()
        sim_task = asyncio.create_task(runner.run_forever(settings.sim_tick_s))
        log.info("Robot simulation running (tick %.2fs, travel %.0fs, fake Pi battery %s)",
                 settings.sim_tick_s, settings.sim_travel_s, settings.sim_fake_pi_battery)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):  # SIGTERM = `docker compose down`
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    log.info("Shutting down mock PLC")
    if sim_task:
        sim_task.cancel()
    await server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
