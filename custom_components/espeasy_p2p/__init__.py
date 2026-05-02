"""The ESPEasy P2P integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_PORT, DEFAULT_PORT, DOMAIN
from .coordinator import ESPEasyP2PCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ESPEasy P2P from a config entry."""
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
    coordinator = ESPEasyP2PCoordinator(hass, entry.entry_id, port)
    try:
        await coordinator.async_start()
    except OSError as err:
        _LOGGER.error("Failed to bind UDP port %s: %s", port, err)
        return False

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    coordinator: ESPEasyP2PCoordinator | None = hass.data.get(DOMAIN, {}).pop(
        entry.entry_id, None
    )
    if coordinator is not None:
        await coordinator.async_stop()
    return unload_ok
