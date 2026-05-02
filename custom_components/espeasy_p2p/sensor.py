"""Sensor platform for ESPEasy P2P."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo, format_mac
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    SIGNAL_TASK_DISCOVERED,
    SIGNAL_VALUE_UPDATED,
)
from .coordinator import ESPEasyP2PCoordinator
from .protocol import TaskConfig, TaskValues

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ESPEasyP2PCoordinator = hass.data[DOMAIN][entry.entry_id]

    known: set[tuple[int, int, int]] = set()

    @callback
    def _add_for_task(task: TaskConfig) -> None:
        new_entities: list[ESPEasyP2PValueSensor] = []
        for value_index, value_name in enumerate(task.value_names):
            if not value_name:
                continue
            key = (task.src_unit, task.task_index, value_index)
            if key in known:
                continue
            known.add(key)
            new_entities.append(
                ESPEasyP2PValueSensor(
                    coordinator=coordinator,
                    entry_id=entry.entry_id,
                    src_unit=task.src_unit,
                    task_index=task.task_index,
                    value_index=value_index,
                    task_name=task.task_name,
                    value_name=value_name,
                )
            )
        if new_entities:
            async_add_entities(new_entities)

    # Register for future discoveries
    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{SIGNAL_TASK_DISCOVERED}_{entry.entry_id}", _add_for_task
        )
    )

    # Replay anything already discovered before the platform finished setup
    for task in list(coordinator.tasks.values()):
        _add_for_task(task)


class ESPEasyP2PValueSensor(SensorEntity):
    """A single value (one of up to four) coming from an ESPEasy task."""

    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ESPEasyP2PCoordinator,
        entry_id: str,
        src_unit: int,
        task_index: int,
        value_index: int,
        task_name: str,
        value_name: str,
    ) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._src_unit = src_unit
        self._task_index = task_index
        self._value_index = value_index
        self._attr_name = f"{task_name} {value_name}".strip() or value_name
        self._attr_unique_id = (
            f"espeasy_p2p_{src_unit}_{task_index}_{value_index}"
        )
        node = coordinator.nodes.get(src_unit)
        identifiers = {(DOMAIN, f"unit-{src_unit}")}
        connections = set()
        if node is not None:
            connections.add(("mac", format_mac(node.mac)))
        self._attr_device_info = DeviceInfo(
            identifiers=identifiers,
            connections=connections,
            name=node.name if node else f"ESPEasy unit {src_unit}",
            manufacturer="ESPEasy",
            model=node.node_type_name if node else None,
            sw_version=str(node.build) if node else None,
            configuration_url=(
                f"http://{node.ip}:{node.web_port}" if node and node.ip else None
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

    @callback
    def _handle_update(self, payload: TaskValues) -> None:
        if (
            payload.src_unit != self._src_unit
            or payload.task_index != self._task_index
        ):
            return
        if self._value_index >= len(payload.values):
            return
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        values = self._coordinator.values.get((self._src_unit, self._task_index))
        if not values or self._value_index >= len(values):
            return None
        return values[self._value_index]

    @property
    def available(self) -> bool:
        return (self._src_unit, self._task_index) in self._coordinator.values
