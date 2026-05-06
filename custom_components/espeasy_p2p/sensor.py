"""Sensor platform for ESPEasy P2P."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfPressure,
    UnitOfTemperature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo, format_mac
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    SIGNAL_NODE_AVAILABILITY,
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
        # Only create entities for value slots that have a real name. The
        # coordinator may insert a placeholder TaskConfig with empty names
        # when sensor data arrives before a Type-3 broadcast or before the
        # /json fallback completes; we wait for the real names to appear
        # so the user does not get phantom entities for unused slots.
        new_entities: list[ESPEasyP2PValueSensor] = []
        for value_index, value_name in enumerate(task.value_names):
            if not value_name:
                continue
            if _is_switch_value(value_name):
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
    _attr_suggested_display_precision = 3

    def __init__(
        self,
        coordinator: ESPEasyP2PCoordinator,
        entry_id: str,
        src_unit: int,
        task_index: int,
        value_index: int,
    ) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._src_unit = src_unit
        self._task_index = task_index
        self._value_index = value_index
        self._attr_unique_id = (
            f"espeasy_p2p_{src_unit}_{task_index}_{value_index}"
        )
        node = coordinator.nodes.get(src_unit)
        identifiers = {(DOMAIN, f"unit-{src_unit}")}
        connections = set()
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
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_NODE_AVAILABILITY}_{self._entry_id}",
                self._handle_availability,
            )
        )

    @callback
    def _handle_availability(self, unit: int) -> None:
        if unit == self._src_unit:
            self.async_write_ha_state()

    @callback
    def _handle_update(self, payload: TaskValues) -> None:
        if (
            payload.src_unit != self._src_unit
            or payload.task_index != self._task_index
        ):
            return
        self.async_write_ha_state()

    @callback
    def _handle_task_update(self, task: TaskConfig) -> None:
        # When a real Type-3 arrives after we created entities from
        # synthetic placeholder names, refresh the entity name and ensure
        # the device-info reflects any newly learned node metadata.
        if task.src_unit != self._src_unit or task.task_index != self._task_index:
            return
        self.async_write_ha_state()

    @property
    def name(self) -> str | None:
        task = self._coordinator.tasks.get((self._src_unit, self._task_index))
        if task and self._value_index < len(task.value_names):
            value_name = task.value_names[self._value_index]
            if value_name:
                if task.task_name:
                    return f"{task.task_name} {value_name}".strip()
                return value_name
        if task and task.task_name:
            return f"{task.task_name} value {self._value_index + 1}"
        return f"Task {self._task_index} value {self._value_index + 1}"

    @property
    def device_class(self) -> SensorDeviceClass | None:
        return _classify(self._task(), self._value_index)[0]

    @property
    def native_unit_of_measurement(self) -> str | None:
        return _classify(self._task(), self._value_index)[1]

    def _task(self) -> TaskConfig | None:
        return self._coordinator.tasks.get((self._src_unit, self._task_index))

    @property
    def native_value(self) -> float | None:
        values = self._coordinator.values.get((self._src_unit, self._task_index))
        if not values or self._value_index >= len(values):
            return None
        return values[self._value_index]

    @property
    def available(self) -> bool:
        if not self._coordinator.is_unit_online(self._src_unit):
            return False
        return (self._src_unit, self._task_index) in self._coordinator.values


def _classify(
    task: TaskConfig | None, value_index: int
) -> tuple[SensorDeviceClass | None, str | None]:
    """Map an ESPEasy task/value to a HA device class + unit.

    Driven primarily by the value name (Temperature/Humidity/Pressure) so it
    works for Dummy Devices that mirror real sensors. Plugin Type is a
    secondary hint (e.g. 'Environment - DS18b20').
    """
    if task is None:
        return (None, None)
    value_name = ""
    if 0 <= value_index < len(task.value_names):
        value_name = task.value_names[value_index].strip().lower()
    plugin = task.plugin_type.lower()
    if "temperature" in value_name or "ds18b20" in plugin or "dht" in plugin:
        return (SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS)
    if "humidity" in value_name:
        return (SensorDeviceClass.HUMIDITY, PERCENTAGE)
    if "pressure" in value_name:
        return (SensorDeviceClass.PRESSURE, UnitOfPressure.HPA)
    return (None, None)
