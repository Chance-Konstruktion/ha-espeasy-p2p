"""Runtime coordinator that bridges the UDP protocol with Home Assistant."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    ANNOUNCE_INTERVAL,
    BROADCAST_UNIT,
    CONF_COMMAND_MAP,
    CONF_GPIO_PIN_MAP,
    DOMAIN,
    HA_BUILD,
    HA_NODE_TYPE,
    HA_VERSION,
    NODE_AGING_INTERVAL,
    NODE_OFFLINE_TIMEOUT,
    SIGNAL_NODE_AVAILABILITY,
    SIGNAL_NODE_DISCOVERED,
    SIGNAL_NODE_REMOVED,
    SIGNAL_TASK_DISCOVERED,
    SIGNAL_VALUE_UPDATED,
)
from .protocol import (
    ESPEasyP2PProtocol,
    NodeInfo,
    TaskConfig,
    TaskValues,
    build_command_packet,
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
        pin_overrides: dict[str, int] | None = None,
        command_overrides: dict[str, str] | None = None,
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
        # Last monotonic timestamp at which we saw any traffic (Type-1 or
        # Type-5) from each unit. Drives the `available` property of all
        # entities so a powered-off node correctly fades to "unavailable".
        self.last_seen: dict[int, float] = {}
        # Units currently considered offline (last_seen older than the
        # timeout). Tracked separately so we only fire availability signals
        # on transitions, not on every periodic check.
        self._offline: set[int] = set()
        # User-supplied "<unit>/<taskname>" -> GPIO pin overrides. Used when
        # the firmware (notably RPiEasy) does not expose the pin via /json.
        self.pin_overrides: dict[str, int] = dict(pin_overrides or {})
        # User-supplied "<unit>/<taskname>" -> raw command template. When
        # set, this replaces the default gpio,/<taskname>, dispatch logic.
        # `{state}` in the template is substituted with 0 or 1.
        self.command_overrides: dict[str, str] = dict(command_overrides or {})
        self._transport: asyncio.DatagramTransport | None = None
        self._announce_task: asyncio.Task | None = None
        self._aging_task: asyncio.Task | None = None
        # Track which node IPs we've already fetched task metadata from so
        # we don't re-poll on every periodic Type-1 heartbeat.
        self._fetched_meta_for: set[str] = set()

    def get_gpio_pin(self, unit: int, task_name: str) -> int | None:
        """Return the configured/learned GPIO pin for a task, or None."""
        if task_name:
            override = self.pin_overrides.get(f"{unit}/{task_name}")
            if override is not None:
                return override
        task = self.tasks.get((unit, self._task_index_for_name(unit, task_name)))
        if task and task.gpio_pin is not None:
            return task.gpio_pin
        return None

    def _task_index_for_name(self, unit: int, task_name: str) -> int:
        if not task_name:
            return -1
        for (u, idx), task in self.tasks.items():
            if u == unit and task.task_name == task_name:
                return idx
        return -1

    def get_command_template(self, unit: int, task_name: str) -> str | None:
        """Return the user-defined raw command template for a task, or None."""
        if not task_name:
            return None
        tpl = self.command_overrides.get(f"{unit}/{task_name}")
        return tpl or None

    def set_command_override(
        self, unit: int, task_name: str, template: str
    ) -> None:
        key = f"{unit}/{task_name}"
        if template:
            self.command_overrides[key] = template
        else:
            self.command_overrides.pop(key, None)

    def set_pin_override(self, unit: int, task_name: str, pin: int) -> None:
        """Persist a GPIO pin override for a (unit, task_name)."""
        self.pin_overrides[f"{unit}/{task_name}"] = pin
        # Also patch the in-memory TaskConfig so existing entities pick it up.
        idx = self._task_index_for_name(unit, task_name)
        if idx >= 0:
            task = self.tasks[(unit, idx)]
            self.tasks[(unit, idx)] = TaskConfig(
                src_unit=task.src_unit,
                task_index=task.task_index,
                device_number=task.device_number,
                task_name=task.task_name,
                value_names=task.value_names,
                plugin_type=task.plugin_type,
                enabled=task.enabled,
                gpio_pin=pin,
            )

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
        self._aging_task = self.hass.loop.create_task(self._aging_loop())

    async def async_stop(self) -> None:
        for task_attr in ("_announce_task", "_aging_task"):
            task = getattr(self, task_attr)
            if task is not None:
                task.cancel()
                setattr(self, task_attr, None)
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

    async def _aging_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(NODE_AGING_INTERVAL)
                self._evaluate_availability()
        except asyncio.CancelledError:
            pass

    def _evaluate_availability(self) -> None:
        """Mark nodes offline when their last_seen exceeds the timeout."""
        now = time.monotonic()
        changed: list[int] = []
        for unit, ts in list(self.last_seen.items()):
            stale = (now - ts) > NODE_OFFLINE_TIMEOUT
            was_offline = unit in self._offline
            if stale and not was_offline:
                self._offline.add(unit)
                changed.append(unit)
                _LOGGER.info(
                    "Node unit=%d offline (no packets for %.0fs)", unit, now - ts
                )
            elif not stale and was_offline:
                self._offline.discard(unit)
                changed.append(unit)
                _LOGGER.info("Node unit=%d back online", unit)
        for unit in changed:
            async_dispatcher_send(
                self.hass, self._signal(SIGNAL_NODE_AVAILABILITY), unit
            )

    def is_unit_online(self, unit: int) -> bool:
        if unit not in self.last_seen:
            return False
        return unit not in self._offline

    def _touch(self, unit: int) -> None:
        """Record activity from a unit and fire an availability signal if it
        just transitioned back online."""
        self.last_seen[unit] = time.monotonic()
        if unit in self._offline:
            self._offline.discard(unit)
            async_dispatcher_send(
                self.hass, self._signal(SIGNAL_NODE_AVAILABILITY), unit
            )

    def _signal(self, base: str) -> str:
        return f"{base}_{self.entry_id}"

    @callback
    def _on_node(self, node: NodeInfo) -> None:
        # Ignore our own announce echo
        if node.unit == self.unit and node.name == self.name:
            return
        existing = self.nodes.get(node.unit)
        self.nodes[node.unit] = node
        self._touch(node.unit)
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
            enabled_raw = sensor.get("TaskEnabled")
            if enabled_raw is not None:
                if isinstance(enabled_raw, bool):
                    enabled = enabled_raw
                else:
                    enabled = str(enabled_raw).strip().lower() in ("true", "1")
                if not enabled:
                    continue
            else:
                enabled = True

            plugin_type = str(sensor.get("Type") or "").strip()
            gpio_pin: int | None = None
            for pin_key in ("TaskDeviceGPIO1", "GPIO1", "Pin", "TaskDeviceGPIO"):
                raw_pin = sensor.get(pin_key)
                if raw_pin is None:
                    continue
                try:
                    pin_int = int(raw_pin)
                except (TypeError, ValueError):
                    continue
                if pin_int >= 0:
                    gpio_pin = pin_int
                    break
            task_name = str(sensor.get("TaskName") or "").strip()
            task_values = sensor.get("TaskValues") or []
            value_names: list[str] = []
            for tv in task_values:
                # ESPEasy: "Name". RPiEasy: also "Name".
                value_names.append(str(tv.get("Name") or "").strip())
            value_names = (value_names + ["", "", "", ""])[:4]
            if not task_name and not any(value_names):
                continue
            if gpio_pin is None and task_name:
                override = self.pin_overrides.get(f"{node.unit}/{task_name}")
                if override is not None:
                    gpio_pin = override
            self._on_task(
                TaskConfig(
                    src_unit=node.unit,
                    task_index=task_index,
                    device_number=0,
                    task_name=task_name,
                    value_names=value_names,
                    plugin_type=plugin_type,
                    enabled=enabled,
                    gpio_pin=gpio_pin,
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
        if src_unit in (0, BROADCAST_UNIT):
            _LOGGER.debug(
                "Cannot resolve sender for broadcast sensor data from %s; "
                "dropping packet and probing the source",
                payload.src_ip,
            )
            if (
                self._transport is not None
                and payload.src_ip
                and payload.src_ip != "0.0.0.0"
            ):
                try:
                    self._transport.sendto(
                        self._build_announce(), (payload.src_ip, self.port)
                    )
                except OSError as err:
                    _LOGGER.debug("Probe announce to %s failed: %s", payload.src_ip, err)
            return
        key = (src_unit, payload.task_index)
        self.values[key] = payload.values
        self._touch(src_unit)
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

    async def async_refetch_metadata(self) -> None:
        """Force a /json re-fetch for every known node."""
        self._fetched_meta_for.clear()
        for node in list(self.nodes.values()):
            if node.ip and node.ip != "0.0.0.0":
                self._fetched_meta_for.add(node.ip)
                await self._fetch_node_metadata_safe(node)

    async def async_send_raw_command(self, unit: int, command: str) -> dict[str, Any]:
        """Send an arbitrary ESPEasy command to a unit via P2P + HTTP.

        Returns a dict describing what happened, suitable for logging or for
        a future service response.
        """
        node = self.nodes.get(unit)
        if node is None or not node.ip or node.ip == "0.0.0.0":
            _LOGGER.warning(
                "send_command: unit %d unknown or has no IP", unit
            )
            return {"ok": False, "reason": "unknown_unit"}
        p2p_ok = self.send_p2p_command(node.ip, command)
        url = f"http://{node.ip}:{node.web_port}/control"
        session = async_get_clientsession(self.hass)
        status: int | str = "n/a"
        body = ""
        try:
            async with session.get(
                url, params={"cmd": command}, timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                status = resp.status
                body = (await resp.text())[:200]
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            status = f"error: {err}"
        _LOGGER.info(
            "send_command unit=%d cmd=%r -> p2p=%s http=%s body=%r",
            unit, command, p2p_ok, status, body,
        )
        return {"ok": True, "p2p": p2p_ok, "http_status": status, "body": body}

    def send_p2p_command(self, ip: str, command: str) -> bool:
        """Send a C013 Type-0 command packet to a single node over UDP.

        Returns True if the packet was handed to the OS. RPiEasy executes the
        command; stock ESPEasy mega ignores type-0 today, so callers should
        treat this as best-effort and pair it with an HTTP fallback.
        """
        if self._transport is None or not ip or ip == "0.0.0.0":
            return False
        try:
            self._transport.sendto(build_command_packet(command), (ip, self.port))
            _LOGGER.debug("Sent C013 type-0 cmd %r to %s:%s", command, ip, self.port)
            return True
        except OSError as err:
            _LOGGER.debug("UDP command to %s failed: %s", ip, err)
            return False

    async def async_remove_node(self, unit: int) -> bool:
        """Forget a node: drop in-memory state and remove its HA device.

        Entities for the node disappear because the device they were attached
        to is gone. The node will reappear automatically if it sends a fresh
        Type-1 heartbeat afterwards.
        """
        if unit not in self.nodes and not any(
            u == unit for (u, _) in self.tasks
        ):
            _LOGGER.warning("remove_node: unit %d not known", unit)
            return False
        node = self.nodes.pop(unit, None)
        for key in [k for k in list(self.tasks) if k[0] == unit]:
            self.tasks.pop(key, None)
            self.values.pop(key, None)
        self.last_seen.pop(unit, None)
        self._offline.discard(unit)
        if node is not None and node.ip:
            self._fetched_meta_for.discard(node.ip)
        # Remove the device entry — HA will tear down its child entities.
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(
            identifiers={(DOMAIN, f"unit-{unit}")}
        )
        if device is not None:
            ent_reg = er.async_get(self.hass)
            for ent in er.async_entries_for_device(
                ent_reg, device.id, include_disabled_entities=True
            ):
                ent_reg.async_remove(ent.entity_id)
            registry.async_remove_device(device.id)
        async_dispatcher_send(self.hass, self._signal(SIGNAL_NODE_REMOVED), unit)
        _LOGGER.info("Removed node unit=%d", unit)
        return True

    def diagnostics(self) -> dict[str, Any]:
        return {
            "port": self.port,
            "unit": self.unit,
            "node_count": len(self.nodes),
            "task_count": len(self.tasks),
        }
