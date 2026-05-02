"""Runtime coordinator that bridges the UDP protocol with Home Assistant."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    SIGNAL_NODE_DISCOVERED,
    SIGNAL_TASK_DISCOVERED,
    SIGNAL_VALUE_UPDATED,
)
from .protocol import (
    ESPEasyP2PProtocol,
    NodeInfo,
    TaskConfig,
    TaskValues,
    create_listener,
)

_LOGGER = logging.getLogger(__name__)


class ESPEasyP2PCoordinator:
    """Owns the UDP socket and the in-memory state of nodes/tasks/values."""

    def __init__(self, hass: HomeAssistant, entry_id: str, port: int) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.port = port
        self.nodes: dict[int, NodeInfo] = {}
        self.tasks: dict[tuple[int, int], TaskConfig] = {}
        self.values: dict[tuple[int, int], list[float]] = {}
        self._transport: asyncio.DatagramTransport | None = None

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

    async def async_stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def _signal(self, base: str) -> str:
        return f"{base}_{self.entry_id}"

    @callback
    def _on_node(self, node: NodeInfo) -> None:
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
            "node_count": len(self.nodes),
            "task_count": len(self.tasks),
        }
