"""Runtime coordinator that bridges the UDP protocol with Home Assistant."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    ANNOUNCE_INTERVAL,
    BROADCAST_UNIT,
    DOMAIN,
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
        # Track which node IPs we've already fetched task metadata from so
        # we don't re-poll on every periodic Type-1 heartbeat.
        self._fetched_meta_for: set[str] = set()

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
        # Always refresh the HA device entry — entities may have been created
        # with placeholder metadata before the first Type-1 arrived. Wrap
        # defensively: a failure here must not break node discovery for the
        # rest of the coordinator pipeline. RPiEasy in particular has been
        # observed to send Type-1 packets that cause the registry update to
        # raise; we want the discovery and the JSON fallback to still run.
        try:
            self._update_device_registry(node)
        except Exception:  # noqa: BLE001
            _LOGGER.exception(
                "Failed to update HA device entry for unit %d (%s)",
                node.unit, node.name,
            )
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
        # Fetch task and value names from the node's HTTP /json endpoint.
        # This is the only reliable way to learn real names if the node
        # never sends Type-3 broadcasts. Both ESPEasy and RPiEasy expose
        # this endpoint.
        if (
            node.ip
            and node.ip != "0.0.0.0"
            and node.ip not in self._fetched_meta_for
        ):
            self._fetched_meta_for.add(node.ip)
            self.hass.async_create_task(self._fetch_node_metadata_safe(node))

    async def _fetch_node_metadata_safe(self, node: NodeInfo) -> None:
        try:
            await self._fetch_node_metadata(node)
        except Exception:  # noqa: BLE001
            _LOGGER.exception(
                "Unhandled error fetching /json for unit %d (%s)",
                node.unit, node.name,
            )
            self._fetched_meta_for.discard(node.ip)

    @callback
    def _update_device_registry(self, node: NodeInfo) -> None:
        """Create or update the HA device entry for this ESPEasy node."""
        registry = dr.async_get(self.hass)
        connections: set[tuple[str, str]] = set()
        if node.mac and node.mac != "00:00:00:00:00:00":
            connections.add((dr.CONNECTION_NETWORK_MAC, dr.format_mac(node.mac)))
        configuration_url = (
            f"http://{node.ip}:{node.web_port}"
            if node.ip and node.ip != "0.0.0.0"
            else None
        )
        registry.async_get_or_create(
            config_entry_id=self.entry_id,
            identifiers={(DOMAIN, f"unit-{node.unit}")},
            connections=connections,
            name=node.name,
            manufacturer="ESPEasy",
            model=node.node_type_name,
            sw_version=str(node.build),
            configuration_url=configuration_url,
        )

    async def _fetch_node_metadata(self, node: NodeInfo) -> None:
        """Pull real task and value names from a node's /json endpoint."""
        url = f"http://{node.ip}:{node.web_port}/json"
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status != 200:
                    _LOGGER.debug(
                        "GET %s returned HTTP %d", url, resp.status
                    )
                    return
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            _LOGGER.debug("Failed to fetch %s: %s", url, err)
            self._fetched_meta_for.discard(node.ip)
            return

        sensors = data.get("Sensors") or []
        learned = 0
        for sensor in sensors:
            # ESPEasy uses 1-based TaskNumber on the JSON API, but 0-based
            # task indices on the C013 wire. Convert here so they match
            # what we already keyed in self.tasks/self.values.
            task_number = sensor.get("TaskNumber")
            if task_number is None:
                continue
            task_index = int(task_number) - 1
            if task_index < 0:
                continue
            task_name = str(sensor.get("TaskName") or "").strip()
            task_values = sensor.get("TaskValues") or []
            value_names: list[str] = []
            for tv in task_values:
                # ESPEasy: "Name". RPiEasy: also "Name".
                value_names.append(str(tv.get("Name") or "").strip())
            value_names = (value_names + ["", "", "", ""])[:4]
            if not task_name and not any(value_names):
                continue
            self._on_task(
                TaskConfig(
                    src_unit=node.unit,
                    task_index=task_index,
                    device_number=int(sensor.get("Type") or 0)
                    if isinstance(sensor.get("Type"), int)
                    else 0,
                    task_name=task_name,
                    value_names=value_names,
                )
            )
            learned += 1
        if learned:
            _LOGGER.info(
                "Fetched %d task definitions for unit %d (%s) from %s",
                learned, node.unit, node.name, url,
            )

    @callback
    def _on_task(self, task: TaskConfig) -> None:
        key = (task.src_unit, task.task_index)
        prev = self.tasks.get(key)
        self.tasks[key] = task
        # Fire the discovery signal whenever this task gains real value
        # names that weren't there before. That covers both the initial
        # discovery and the case where a placeholder TaskConfig (created
        # from incoming sensor data with no metadata yet) gets replaced
        # by the real config from /json or a Type-3 broadcast.
        prev_named = bool(prev and any(v for v in prev.value_names))
        now_named = any(v for v in task.value_names)
        if now_named and not prev_named:
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
        # Track that this (unit, task) is alive even before we know the
        # real names. We deliberately do NOT create entities here from a
        # placeholder TaskConfig — the user does not want phantom "Task X
        # / Value 1-4" entities for slots that are not actually wired up
        # in the source node. Entities are only created once we have real
        # value names from a Type-3 broadcast or the node's /json HTTP
        # endpoint (see _fetch_node_metadata).
        if key not in self.tasks:
            placeholder = TaskConfig(
                src_unit=src_unit,
                task_index=payload.task_index,
                device_number=0,
                task_name="",
                value_names=["", "", "", ""],
            )
            self.tasks[key] = placeholder
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
