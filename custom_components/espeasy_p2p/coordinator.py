"""Runtime coordinator that bridges the UDP protocol with Home Assistant."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    ANNOUNCE_INTERVAL,
    BROADCAST_UNIT,
    HA_BUILD,
    HA_NODE_TYPE,
    HA_VERSION,
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
    detect_local_ip,
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
        self.local_ip = "0.0.0.0"
        self.nodes: dict[int, NodeInfo] = {}
        self.tasks: dict[tuple[int, int], TaskConfig] = {}
        self.values: dict[tuple[int, int], list[float]] = {}
        self._transport: asyncio.DatagramTransport | None = None
        self._announce_task: asyncio.Task | None = None

    async def async_start(self) -> None:
        loop = self.hass.loop
        self.local_ip = await loop.run_in_executor(None, detect_local_ip)
        self._transport, _ = await create_listener(
            loop,
            self.port,
            lambda: ESPEasyP2PProtocol(
                on_node=self._on_node,
                on_task=self._on_task,
                on_values=self._on_values,
            ),
        )
        _LOGGER.info(
            "ESPEasy P2P listener bound on UDP %s (peer unit=%d name=%s ip=%s version=%s)",
            self.port, self.unit, self.name, self.local_ip, HA_VERSION,
        )
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

    def _build_announce(self) -> bytes:
        return build_info_packet(
            unit=self.unit,
            name=self.name,
            ip=self.local_ip,
            web_port=8123,
            build=HA_BUILD,
            node_type=HA_NODE_TYPE,
        )

    @callback
    def async_scan(self) -> None:
        """Broadcast a node-info packet so all peers respond immediately."""
        if self._transport is None:
            return
        packet = self._build_announce()
        try:
            self._transport.sendto(packet, ("255.255.255.255", self.port))
            _LOGGER.debug("Sent C013 scan broadcast on port %s", self.port)
        except OSError as err:
            _LOGGER.warning("Failed to broadcast scan: %s", err)
        # Also unicast to known peers so they re-send their task config.
        for node in self.nodes.values():
            if not node.ip or node.ip == "0.0.0.0":
                continue
            try:
                self._transport.sendto(packet, (node.ip, self.port))
            except OSError as err:
                _LOGGER.debug("Unicast scan to %s failed: %s", node.ip, err)

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
            _LOGGER.info(
                "Discovered ESPEasy node unit=%d name=%s ip=%s build=%d",
                node.unit, node.name, node.ip, node.build,
            )
            async_dispatcher_send(self.hass, self._signal(SIGNAL_NODE_DISCOVERED), node)
            # Unicast a hello back so the node knows we exist and re-sends
            # its task configuration to us.
            if self._transport is not None and node.ip and node.ip != "0.0.0.0":
                try:
                    self._transport.sendto(self._build_announce(), (node.ip, self.port))
                except OSError as err:
                    _LOGGER.debug("Unicast hello to %s failed: %s", node.ip, err)

    @callback
    def _on_task(self, task: TaskConfig) -> None:
        key = (task.src_unit, task.task_index)
        is_new = key not in self.tasks
        self.tasks[key] = task
        if is_new:
            _LOGGER.info(
                "Discovered task %d on unit %d: %s (values=%s)",
                task.task_index,
                task.src_unit,
                task.task_name,
                task.value_names,
            )
            async_dispatcher_send(self.hass, self._signal(SIGNAL_TASK_DISCOVERED), task)

    def _resolve_src_unit(self, payload: TaskValues) -> int:
        """Some firmware sends sensor data with src_unit=255 (broadcast).
        Resolve the real sender via its IP address against our known nodes."""
        if payload.src_unit not in (0, BROADCAST_UNIT):
            return payload.src_unit
        if payload.src_ip:
            for unit, node in self.nodes.items():
                if node.ip == payload.src_ip:
                    _LOGGER.debug(
                        "Resolved broadcast sensor data from %s -> unit %d",
                        payload.src_ip, unit,
                    )
                    return unit
        # Last resort: keep whatever the packet had.
        return payload.src_unit

    @callback
    def _on_values(self, payload: TaskValues) -> None:
        src_unit = self._resolve_src_unit(payload)
        key = (src_unit, payload.task_index)
        self.values[key] = payload.values
        # Auto-create a placeholder TaskConfig the first time we see values
        # for a (unit, task) pair. ESPEasy nodes often skip Type-3 (sensor
        # config) on boot, so without this fallback no entities would ever
        # appear. If a real Type-3 arrives later it overwrites the synthetic
        # entry and the entity name updates dynamically (see sensor.py).
        if key not in self.tasks:
            synthetic = TaskConfig(
                src_unit=src_unit,
                task_index=payload.task_index,
                device_number=0,
                task_name=f"Task {payload.task_index}",
                value_names=[
                    f"Value {i + 1}" for i in range(len(payload.values))
                ],
            )
            self._on_task(synthetic)
        # Always rewrite payload.src_unit so downstream subscribers see the
        # resolved unit, not the raw broadcast 255.
        resolved = TaskValues(
            src_unit=src_unit,
            task_index=payload.task_index,
            values=payload.values,
            src_ip=payload.src_ip,
        )
        async_dispatcher_send(
            self.hass, self._signal(SIGNAL_VALUE_UPDATED), resolved
        )

    def diagnostics(self) -> dict[str, Any]:
        return {
            "port": self.port,
            "unit": self.unit,
            "node_count": len(self.nodes),
            "task_count": len(self.tasks),
        }
