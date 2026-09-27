"""Entry point: `python -m app`.

Step 1 only: load settings + tag map, print a summary, exit.
"""

import logging

from app.config import Settings
from app.tags import load_tags


def main() -> None:
    settings = Settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("gateway")

    tags = load_tags(settings.plc_tags_file)
    log.info("PLC target %s:%d unit=%d", settings.plc_host, settings.plc_port, settings.plc_unit_id)
    log.info("Loaded %d tag(s) from %s", len(tags), settings.plc_tags_file)
    for tag in tags:
        log.info("  %-24s %-6s -> %-8s 0x%04X  %-5s %s",
                 tag.name, tag.device, tag.addr.area.value, tag.addr.address, tag.dir.value, tag.type.value)


if __name__ == "__main__":
    main()
