"""Dev tool: read PLC tags by name (read-only).

    python -m app.read                      # all tags once
    python -m app.read robot_state limit_1  # selected tags
    python -m app.read --watch              # refresh every PLC_POLL_INTERVAL_S
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.config import Settings
from app.plc_client import PlcClient, PlcOfflineError, PlcReadError
from app.tags import load_tags


def format_values(values: dict, tags) -> str:
    width = max(len(name) for name in values)
    lines = []
    for name, value in values.items():
        tag = tags[name]
        shown = int(value) if isinstance(value, bool) else value
        lines.append(f"  {name:<{width}}  {tag.device:<5}  {shown!s:>10} {tag.unit}".rstrip())
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Read PLC tags by name (read-only)")
    parser.add_argument("names", nargs="*", help="tag names (default: all)")
    parser.add_argument("--watch", action="store_true", help="refresh every PLC_POLL_INTERVAL_S")
    args = parser.parse_args()

    settings = Settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    tags = load_tags(settings.plc_tags_file)
    names = args.names or list(tags.tags)
    unknown = [name for name in names if name not in tags.tags]
    if unknown:
        raise SystemExit(f"Unknown tag(s): {', '.join(unknown)}. Known: {', '.join(tags.tags)}")

    async with PlcClient.from_settings(settings, tags) as plc:
        while True:
            try:
                values = await plc.read_many(names)
                print(f"PLC {settings.plc_host}:{settings.plc_port}")
                print(format_values(values, tags), flush=True)
            except (PlcOfflineError, PlcReadError) as exc:
                print(f"!! PLC offline or read failed: {exc}", flush=True)
            if not args.watch:
                break
            await asyncio.sleep(settings.plc_poll_interval_s)
            print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
