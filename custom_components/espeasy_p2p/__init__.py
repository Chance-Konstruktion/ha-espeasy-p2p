"""The ESPEasy P2P integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall

from .const import (
    CONF_COMMAND_MAP,
    CONF_GPIO_PIN_MAP,
    CONF_NAME,
    CONF_PORT,
    CONF_UNIT,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_UNIT,
    DOMAIN,
    SERVICE_REFETCH_METADATA,
    SERVICE_REMOVE_NODE,
    SERVICE_SCAN,
    SERVICE_SEND_COMMAND,
    SERVICE_SET_GPIO_PIN,
)
from .coordinator import ESPEasyP2PCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ESPEasy P2P from a config entry."""
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
    unit = entry.data.get(CONF_UNIT, DEFAULT_UNIT)
    name = entry.data.get(CONF_NAME, DEFAULT_NAME)
    _LOGGER.info(
        "Loading ESPEasy P2P entry %s with port=%s unit=%s name=%s (raw data=%s)",
        entry.entry_id, port, unit, name, dict(entry.data),
    )
    # If the entry was created by an older version of the integration with
    # an empty data dict, persist the resolved defaults so the next reload
    # has the correct values without surprises.
    if not entry.data or any(
        key not in entry.data for key in (CONF_PORT, CONF_UNIT, CONF_NAME)
    ):
        hass.config_entries.async_update_entry(
            entry,
            data={CONF_PORT: port, CONF_UNIT: unit, CONF_NAME: name},
        )
        _LOGGER.info("Backfilled missing config entry data with defaults")
    pin_overrides = dict(entry.options.get(CONF_GPIO_PIN_MAP, {}))
    command_overrides = dict(entry.options.get(CONF_COMMAND_MAP, {}))
    coordinator = ESPEasyP2PCoordinator(
        hass,
        entry.entry_id,
        port,
        unit,
        name,
        pin_overrides=pin_overrides,
        command_overrides=command_overrides,
    )
    try:
        await coordinator.async_start()
    except OSError as err:
        _LOGGER.error("Failed to bind UDP port %s: %s", port, err)
        return False

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.services.has_service(DOMAIN, SERVICE_SCAN):

        async def _handle_scan(_call: ServiceCall) -> None:
            for c in hass.data.get(DOMAIN, {}).values():
                c.async_scan()

        async def _handle_refetch(_call: ServiceCall) -> None:
            for c in hass.data.get(DOMAIN, {}).values():
                await c.async_refetch_metadata()

        async def _handle_send_command(call: ServiceCall) -> None:
            unit = int(call.data["unit"])
            command = str(call.data["command"])
            for c in hass.data.get(DOMAIN, {}).values():
                if unit in c.nodes:
                    await c.async_send_raw_command(unit, command)
                    return
            _LOGGER.warning("send_command: unit %d not found in any entry", unit)

        async def _handle_set_gpio_pin(call: ServiceCall) -> None:
            unit = int(call.data["unit"])
            task_name = str(call.data["task_name"]).strip()
            pin = int(call.data["pin"])
            for entry_id, c in hass.data.get(DOMAIN, {}).items():
                if unit not in c.nodes:
                    continue
                c.set_pin_override(unit, task_name, pin)
                cfg_entry = hass.config_entries.async_get_entry(entry_id)
                if cfg_entry is not None:
                    new_options = dict(cfg_entry.options)
                    new_options[CONF_GPIO_PIN_MAP] = dict(c.pin_overrides)
                    hass.config_entries.async_update_entry(
                        cfg_entry, options=new_options
                    )
                _LOGGER.info(
                    "set_gpio_pin: unit=%d task=%r pin=%d (persisted)",
                    unit, task_name, pin,
                )
                return
            _LOGGER.warning("set_gpio_pin: unit %d not found", unit)

        async def _handle_remove_node(call: ServiceCall) -> None:
            unit = int(call.data["unit"])
            for c in hass.data.get(DOMAIN, {}).values():
                if await c.async_remove_node(unit):
                    return
            _LOGGER.warning("remove_node: unit %d not found", unit)

        hass.services.async_register(DOMAIN, SERVICE_SCAN, _handle_scan)
        hass.services.async_register(
            DOMAIN, SERVICE_REFETCH_METADATA, _handle_refetch
        )
        hass.services.async_register(
            DOMAIN, SERVICE_SEND_COMMAND, _handle_send_command
        )
        hass.services.async_register(
            DOMAIN, SERVICE_SET_GPIO_PIN, _handle_set_gpio_pin
        )
        hass.services.async_register(
            DOMAIN, SERVICE_REMOVE_NODE, _handle_remove_node
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    coordinator: ESPEasyP2PCoordinator | None = hass.data.get(DOMAIN, {}).pop(
        entry.entry_id, None
    )
    if coordinator is not None:
        await coordinator.async_stop()
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_SCAN)
        hass.services.async_remove(DOMAIN, SERVICE_REFETCH_METADATA)
        hass.services.async_remove(DOMAIN, SERVICE_SEND_COMMAND)
        hass.services.async_remove(DOMAIN, SERVICE_SET_GPIO_PIN)
        hass.services.async_remove(DOMAIN, SERVICE_REMOVE_NODE)
    return unload_ok
