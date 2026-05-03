"""Switch platform for ESPEasy P2P.

Tasks whose value is named "State", "Output", "Relay" or "Switch" are exposed
as toggleable switches. Toggling sends:
    GET http://<node-ip>:<webport>/control?cmd=<taskname>,<0|1>
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo, format_mac
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    SIGNAL_TASK_DISCOVERED,
    SIGNAL_VALUE_UPDATED,
    SWITCH_VALUE_NAMES,
)
from .coordinator import ESPEasyP2PCoordinator
from .protocol import TaskConfig, TaskValues

_LOGGER = logging.getLogger(__name__)


def _is_switch_value(value_name: str) -> bool:
    return value_name.strip().lower() in SWITCH_VALUE_NAMES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ESPEasyP2PCoordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[tuple[int, int, int]] = set()

    @callback
    def _add_for_task(task: TaskConfig) -> None:
        new_entities: list[ESPEasyP2PSwitch] = []
        for value_index, value_name in enumerate(task.value_names):
            if not value_name or not _is_switch_value(value_name):
                continue
            key = (task.src_unit, task.task_index, value_index)
            if key in known:
                continue
            known.add(key)
            new_entities.append(
                ESPEasyP2PSwitch(
                    coordinator=coordinator,
                    entry_id=entry.entry_id,
                    src_unit=task.src_unit,
                    task_index=task.task_index,
                    value_index=value_index,
                )
            )
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{SIGNAL_TASK_DISCOVERED}_{entry.entry_id}", _add_for_task
        )
    )
    for task in list(coordinator.tasks.values()):
        _add_for_task(task)


class ESPEasyP2PSwitch(SwitchEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry_id, src_unit, task_index, value_index):
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._src_unit = src_unit
        self._task_index = task_index
        self._value_index = value_index
        self._attr_unique_id = f"espeasy_p2p_switch_{src_unit}_{task_index}_{value_index}"
        node = coordinator.nodes.get(src_unit)
        identifiers = {(DOMAIN, f"unit-{src_unit}")}
        connections: set[tuple[str, str]] = set()
        if node is not None and node.mac and node.mac != "00:00:00:00:00:00":
            connections.add(("mac", format_mac(node.mac)))
        self._attr_device_info = DeviceInfo(
            identifiers=identifiers,
            connections=connections,
            name=node.name if node else f"ESPEasy unit {src_unit}",
            manufacturer="ESPEasy",
            model=node.node_type_name if node else None,
            sw_version=str(node.build) if node else None,
            configuration_url=(
                f"http://{node.ip}:{node.web_port}"
                if node and node.ip and node.ip != "0.0.0.0"
                else None
            ),
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_VALUE_UPDATED}_{self._entry_id}",
                self._handle_update,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_TASK_DISCOVERED}_{self._entry_id}",
                self._handle_task_update,
            )
        )

    @callback
    def _handle_update(self, payload: TaskValues) -> None:
        if payload.src_unit != self._src_unit or payload.task_index != self._task_index:
            return
        self.async_write_ha_state()

    @callback
    def _handle_task_update(self, task: TaskConfig) -> None:
        if task.src_unit != self._src_unit or task.task_index != self._task_index:
            return
        self.async_write_ha_state()

    @property
    def name(self) -> str | None:
        task = self._coordinator.tasks.get((self._src_unit, self._task_index))
        if task and task.task_name:
            return task.task_name
        return f"Task {self._task_index}"

    @property
    def is_on(self) -> bool | None:
        values = self._coordinator.values.get((self._src_unit, self._task_index))
        if not values or self._value_index >= len(values):
            return None
        return values[self._value_index] >= 0.5

    @property
    def available(self) -> bool:
        node = self._coordinator.nodes.get(self._src_unit)
        return node is not None and bool(node.ip) and node.ip != "0.0.0.0"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._send_command(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send_command(0)

    async def _send_command(self, state: int) -> None:
        node = self._coordinator.nodes.get(self._src_unit)
        if node is None or not node.ip or node.ip == "0.0.0.0":
            _LOGGER.warning("Cannot toggle — no node info for unit %d", self._src_unit)
            return
        task = self._coordinator.tasks.get((self._src_unit, self._task_index))
        task_name = (task.task_name if task else "") or f"task{self._task_index}"
        url = f"http://{node.ip}:{node.web_port}/control"
        params = {"cmd": f"{task_name},{state}"}
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                body = await resp.text()
                _LOGGER.debug("Sent %s ?cmd=%s -> HTTP %d %s", url, params["cmd"], resp.status, body[:120])
                if resp.status >= 400:
                    return
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning("Switch command to %s failed: %s", url, err)
            return
        # Optimistic update
        values = list(self._coordinator.values.get((self._src_unit, self._task_index)) or [0.0, 0.0, 0.0, 0.0])
        while len(values) <= self._value_index:
            values.append(0.0)
        values[self._value_index] = float(state)
        self._coordinator.values[(self._src_unit, self._task_index)] = values
        self.async_write_ha_state()
