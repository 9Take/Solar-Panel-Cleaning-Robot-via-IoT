"""Read-only PLC client: read tags by name over Modbus TCP.

- Tags are read by name only; raw addresses come from the tag map.
- Tags with adjacent addresses are merged into one request, but never across a
  gap: the PLC rejects a range that touches an address it does not have.
- Connection loss raises PlcOfflineError; the next call reconnects, with
  exponential backoff between failed attempts.

No write methods here on purpose (commands come in step 5).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from app.codec import Value, decode
from app.config import Settings
from app.delta import Area
from app.tags import Tag, TagMap

log = logging.getLogger(__name__)
# pymodbus repeats our connection messages ("Failed to connect", "Repeating...").
logging.getLogger("pymodbus").setLevel(logging.ERROR)

# Per-request limits. Delta DVP documents up to 100 words (FC03); bits kept at 256.
MAX_WORDS_PER_READ = 100
MAX_BITS_PER_READ = 256


class PlcOfflineError(Exception):
    """No connection to the PLC (refused, timed out, or dropped)."""


class PlcReadError(Exception):
    """The PLC answered with a Modbus exception (e.g. 02 illegal address)."""


@dataclass(frozen=True)
class ReadBlock:
    area: Area
    address: int
    count: int
    tags: tuple[Tag, ...]


def plan_reads(tags: Iterable[Tag]) -> list[ReadBlock]:
    """Group tags into as few requests as possible without spanning gaps."""
    by_area: dict[Area, dict[str, Tag]] = defaultdict(dict)
    for tag in tags:
        by_area[tag.addr.area][tag.name] = tag  # dedupe by name

    blocks: list[ReadBlock] = []
    for area, named in by_area.items():
        limit = MAX_WORDS_PER_READ if area is Area.HOLDING_REGISTER else MAX_BITS_PER_READ
        current: list[Tag] = []
        start = end = 0
        for tag in sorted(named.values(), key=lambda t: t.addr.address):
            fits = tag.addr.address + tag.word_count - start <= limit
            if current and tag.addr.address == end and fits:
                current.append(tag)
            else:
                if current:
                    blocks.append(ReadBlock(area, start, end - start, tuple(current)))
                current, start = [tag], tag.addr.address
            end = tag.addr.address + tag.word_count
        if current:
            blocks.append(ReadBlock(area, start, end - start, tuple(current)))
    return sorted(blocks, key=lambda b: (b.area.value, b.address))


class PlcClient:
    def __init__(
        self,
        tags: TagMap,
        host: str,
        port: int = 502,
        unit_id: int = 1,
        timeout_s: float = 2.0,
        backoff_initial_s: float = 1.0,
        backoff_max_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.tags = tags
        self.host, self.port, self.unit_id = host, port, unit_id
        self._client = AsyncModbusTcpClient(
            host, port=port, timeout=timeout_s, retries=0, reconnect_delay=0)
        self._clock = clock
        self._backoff_initial = backoff_initial_s
        self._backoff_max = backoff_max_s
        self._backoff = backoff_initial_s
        self._next_attempt = 0.0

    @classmethod
    def from_settings(cls, settings: Settings, tags: TagMap) -> PlcClient:
        return cls(tags, settings.plc_host, settings.plc_port, settings.plc_unit_id, settings.plc_timeout_s)

    @property
    def online(self) -> bool:
        return self._client.connected

    async def __aenter__(self) -> PlcClient:
        return self

    async def __aexit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    async def connect(self) -> bool:
        """Connect if needed. Returns False while offline or waiting for the backoff delay."""
        if self._client.connected:
            return True
        now = self._clock()
        if now < self._next_attempt:
            return False
        if await self._client.connect():
            log.debug("Connected to PLC %s:%d", self.host, self.port)
            self._backoff = self._backoff_initial
            return True
        self._next_attempt = now + self._backoff
        log.warning("PLC %s:%d unreachable; retry in %.0fs", self.host, self.port, self._backoff)
        self._backoff = min(self._backoff * 2, self._backoff_max)
        return False

    async def read(self, name: str) -> Value:
        return (await self.read_many([name]))[name]

    async def read_all(self) -> dict[str, Value]:
        return await self.read_many(self.tags.tags)

    async def read_many(self, names: Iterable[str]) -> dict[str, Value]:
        tags = [self.tags[name] for name in dict.fromkeys(names)]  # KeyError for unknown names
        if not await self.connect():
            raise PlcOfflineError(f"cannot connect to {self.host}:{self.port}")
        values: dict[str, Value] = {}
        for block in plan_reads(tags):
            values.update(await self._read_block(block))
        return {tag.name: values[tag.name] for tag in tags}

    async def _read_block(self, block: ReadBlock) -> dict[str, Value]:
        request = {
            Area.COIL: self._client.read_coils,
            Area.DISCRETE_INPUT: self._client.read_discrete_inputs,
            Area.HOLDING_REGISTER: self._client.read_holding_registers,
        }[block.area]
        try:
            response = await request(block.address, count=block.count, device_id=self.unit_id)
        except (ModbusException, OSError, asyncio.TimeoutError) as exc:
            self._drop_connection(exc)
            raise PlcOfflineError(f"connection to {self.host}:{self.port} lost: {exc}") from exc
        if response.isError():
            raise PlcReadError(
                f"PLC rejected read of {', '.join(t.name for t in block.tags)} "
                f"(0x{block.address:04X} x{block.count}): Modbus exception {response.exception_code}")

        values: dict[str, Value] = {}
        for tag in block.tags:
            offset = tag.addr.address - block.address
            if block.area is Area.HOLDING_REGISTER:
                raw = response.registers[offset:offset + tag.word_count]
            else:
                raw = response.bits[offset]
            values[tag.name] = decode(tag, raw)
        return values

    def _drop_connection(self, exc: BaseException) -> None:
        log.warning("PLC connection lost: %s", exc)
        self._client.close()
        self._next_attempt = self._clock()  # first reconnect right away, then back off
