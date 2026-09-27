"""Mock PLC Modbus TCP server."""

from __future__ import annotations

from pymodbus.server import ModbusTcpServer

from app.sim.datastore import StrictSimCore, build_sim_device
from app.tags import TagMap


class MockPlcServer(ModbusTcpServer):
    """ModbusTcpServer backed by StrictSimCore.

    pymodbus always wraps its context in a plain SimCore, so the strict core is
    swapped in after construction. Must be created inside a running event loop.
    """

    def __init__(self, tags: TagMap, unit_id: int, host: str, port: int) -> None:
        super().__init__(build_sim_device(tags, unit_id), address=(host, port))
        self.core = StrictSimCore(tags, unit_id)
        self.context = self.core

    @property
    def bound_port(self) -> int:
        """Actual listening port (useful when started with port 0)."""
        return self.transport.sockets[0].getsockname()[1]
