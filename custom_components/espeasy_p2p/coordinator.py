"""Runtime coordinator that bridges the UDP protocol with Home Assistant."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    ANNOUNCE_INTERVAL,
    SIGNAL_NODE_DISCOVERED,
    SIGNAL_TASK_DISCOVERED,
    SIGNAL_VALUE_UPDATED,
)
from .protocol import (
    ESPEasyP2PProtocol,
    NodeInfo,
    TaskConfig,
    TaskValues,
    build_info_packet,
    create_listener,
)

_LOGGER = logging.getLogger(__name__)


class ESPEasyP2PCoordinator:
    """Owns the UDP socket and the in-memory state of nodes/tasks/values."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        port: int,
        unit: int,
        name: str,
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.port = port
        self.unit = unit
        self.name = name
        self.nodes: dict[int, NodeInfo] = {}
        self.tasks: dict[tuple[int, int], TaskConfig] = {}
        self.values: dict[tuple[int, int], list[float]] = {}
        self._transport: asyncio.DatagramTransport | None = None
        self._announce_task: asyncio.Task | None = None

    async def async_start(self) -> None:
        loop = self.hass.loop
        self._transport, _ = await create_listener(
            loop,
            self.port,
            lambda: ESPEasyP2PProtocol(
                on_node=self._on_node,
                on_task=self._on_task,
                on_values=self._on_values,
            ),
        )
        _LOGGER.info("ESPEasy P2P listener bound on UDP port %s", self.port)
        # Kick off active discovery and start periodic announce loop.
        self.async_scan()
        self._announce_task = self.hass.loop.create_task(self._announce_loop())

    async def async_stop(self) -> None:
        if self._announce_task is not None:
            self._announce_task.cancel()
            self._announce_task = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    @callback
    def async_scan(self) -> None:
        """Broadcast a node-info packet so all peers respond immediately."""
        if self._transport is None:
            return
        # Source IP is filled in by the receiver from the UDP packet itself,
        # so we can leave it zero. Node type 5 = RPiEasy, which the reference
        # implementation accepts as a valid peer.
        packet = build_info_packet(
            unit=self.unit,
            name=self.name,
            ip="0.0.0.0",
            web_port=8123,
        )
        try:
            self._transport.sendto(packet, ("255.255.255.255", self.port))
            _LOGGER.debug("Sent ESPEasy P2P scan broadcast on port %s", self.port)
        except OSError as err:
            _LOGGER.warning("Failed to broadcast scan: %s", err)

    async def _announce_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(ANNOUNCE_INTERVAL)
                self.async_scan()
        except asyncio.CancelledError:
            pass

    def _signal(self, base: str) -> str:
        return f"{base}_{self.entry_id}"

    @callback
    def _on_node(self, node: NodeInfo) -> None:
        # Ignore our own announce echo
        if node.unit == self.unit and node.name == self.name:
            return
        existing = self.nodes.get(node.unit)
        self.nodes[node.unit] = node
        if existing is None:
            _LOGGER.debug("Discovered ESPEasy node %s (%s)", node.unit, node.name)
            async_dispatcher_send(self.hass, self._signal(SIGNAL_NODE_DISCOVERED), node)

    @callback
    def _on_task(self, task: TaskConfig) -> None:
        key = (task.src_unit, task.task_index)
        is_new = key not in self.tasks
        self.tasks[key] = task
        if is_new:
            _LOGGER.debug(
                "Discovered task %s on unit %s: %s",
                task.task_index,
                task.src_unit,
                task.task_name,
            )
            async_dispatcher_send(self.hass, self._signal(SIGNAL_TASK_DISCOVERED), task)

    @callback
    def _on_values(self, payload: TaskValues) -> None:
        key = (payload.src_unit, payload.task_index)
        self.values[key] = payload.values
        async_dispatcher_send(
            self.hass, self._signal(SIGNAL_VALUE_UPDATED), payload
        )

    def diagnostics(self) -> dict[str, Any]:
        return {
            "port": self.port,
            "unit": self.unit,
            "node_count": len(self.nodes),
            "task_count": len(self.tasks),
        }
