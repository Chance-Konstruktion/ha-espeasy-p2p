"""Put the P2P mesh on Spatial Hub's floor plan, if it is installed.

What this integration actually knows about topology is one thing: which
units have been heard from, and how long ago. It does not measure link
quality between two ESP nodes -- they broadcast, and we listen. So the
picture drawn here is the honest one: Home Assistant in the middle, one
edge out to each unit, coloured by how fresh that unit's last packet is.

That makes a node about to fall off the mesh visible before it does,
which is the thing a floor plan is actually good for.

Nothing here is required for the integration to work. With no hub
installed, `spatial_provider` writes a dict nobody reads.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

# Deliberately no import of the coordinator: this module reads five
# attributes off it and nothing else, so typing it as Any keeps the
# adapter importable on its own -- which is what lets the conformance kit
# run without Home Assistant installed.
from .const import (
    DOMAIN,
    NODE_AGING_INTERVAL,
    NODE_OFFLINE_TIMEOUT,
    SIGNAL_NODE_AVAILABILITY,
    SIGNAL_NODE_DISCOVERED,
    SIGNAL_NODE_REMOVED,
)
from .spatial_hub_provider import action, edge, spatial_provider, node
from .registry import device_for_unit

# The hub's own node, so the star has a centre. Namespaced by the provider
# id like everything else, so it cannot collide with a unit id.
HUB_ID = "home-assistant"

# A packet within a third of the offline timeout is a healthy node; the
# rest of the way to the timeout is the warning band. Same three words
# every provider speaks, so a renderer colours it without knowing ESPEasy.
_FRESH = NODE_OFFLINE_TIMEOUT / 3


def _quality(silent_for: float | None) -> str:
    if silent_for is None:
        return "unknown"
    if silent_for <= _FRESH:
        return "good"
    if silent_for <= NODE_OFFLINE_TIMEOUT:
        return "fair"
    return "poor"


def _device_of(hass: HomeAssistant, unit: int) -> Any:
    """This unit's device in the registry, if it has been created yet.

    The device is created by the coordinator anyway, so the user has
    already had the chance to say where it hangs -- asking again in a
    floor-plan editor is the work this whole project exists to avoid.
    """
    return device_for_unit(hass, unit)


def _entity_of(hass: HomeAssistant, device: Any) -> str | None:
    """One entity of this unit, to hang the popup's doors on.

    Naming an entity is the shortest possible node definition, and the hub
    is built around that: from one entity it finds the device, and from
    the device every other entity the unit has. Sending nodes without one
    is why the popup came up empty -- no more-info, no device page, no way
    through to the settings. It read as "this thing has nothing", when in
    fact nothing had been offered.

    Which entity it is hardly matters, as long as it is always the same
    one, so the popup does not reshuffle between two refreshes.
    """
    if device is None:
        return None
    try:
        from homeassistant.helpers import entity_registry as er

        entries = er.async_entries_for_device(
            er.async_get(hass), device.id, include_disabled_entities=False
        )
    except Exception:  # noqa: BLE001 - a popup is never worth an exception
        return None
    if not entries:
        return None
    # Diagnostics last: a unit should open as the thing it is, not as its
    # uptime counter.
    return sorted(
        entries,
        key=lambda entry: (
            getattr(entry, "entity_category", None) is not None,
            entry.entity_id,
        ),
    )[0].entity_id


def async_setup_spatial(
    hass: HomeAssistant, entry: Any, coordinator: Any
) -> None:
    """One call, and the mesh is on the plan."""

    def data() -> dict[str, list[dict[str, Any]]]:
        now = time.monotonic()
        nodes: list[dict[str, Any]] = [
            node(
                HUB_ID,
                label=coordinator.name or "Home Assistant",
                state="online",
                icon="mdi:home-assistant",
                unit=coordinator.unit,
                role="Virtueller Peer",
            )
        ]
        edges: list[dict[str, Any]] = []

        for unit, info in sorted(coordinator.nodes.items()):
            seen = coordinator.last_seen.get(unit)
            silent_for = None if seen is None else round(now - seen, 1)
            online = coordinator.is_unit_online(unit)
            device = _device_of(hass, unit)
            nodes.append(
                node(
                    f"unit-{unit}",
                    label=info.name or f"Unit {unit}",
                    entity_id=_entity_of(hass, device),
                    area_id=device.area_id if device else None,
                    state="online" if online else "offline",
                    icon="mdi:chip" if online else "mdi:chip-off",
                    actions=[action("resync", "Neu abfragen")],
                    unit=unit,
                    ip=info.ip,
                    mac=info.mac,
                    typ=info.node_type_name,
                    build=info.build,
                    letztes_paket_vor=silent_for,
                )
            )
            edges.append(
                edge(
                    HUB_ID,
                    f"unit-{unit}",
                    value=silent_for,
                    quality=_quality(silent_for),
                    # Nothing measured the path between these two; we only
                    # know a packet arrived. Dashed says "inferred".
                    dashed=True,
                )
            )

        return {"nodes": nodes, "edges": edges}

    async def run(kind: str, item_id: str, action_id: str, payload: dict) -> dict:
        if action_id != "resync" or not item_id.startswith("unit-"):
            return {"success": False, "error": "unbekannte Aktion"}
        coordinator.async_schedule_resync(int(item_id.removeprefix("unit-")))
        return {"success": True}

    provider = spatial_provider(
        hass,
        entry,
        name="ESPEasy P2P",
        icon="mdi:access-point-network",
        data=data,
        action=run,
        # No DataUpdateCoordinator here: this integration owns a UDP socket
        # and says so itself. These are the signals it already fires.
        signals=[
            f"{SIGNAL_NODE_DISCOVERED}_{entry.entry_id}",
            f"{SIGNAL_NODE_REMOVED}_{entry.entry_id}",
            f"{SIGNAL_NODE_AVAILABILITY}_{entry.entry_id}",
        ],
    )

    # The model contains time-derived data (silent_for / edge quality), not
    # just entity states. A node can keep sending identical values, which
    # causes no HA state_changed event; without a periodic hint its edge can
    # remain "good" until the online/offline transition at 120 seconds.
    @callback
    def _notify_freshness_tick(_now: datetime) -> None:
        provider.async_notify()

    if entry is not None and hasattr(entry, "async_on_unload"):
        entry.async_on_unload(
            async_track_time_interval(
                hass,
                _notify_freshness_tick,
                timedelta(seconds=NODE_AGING_INTERVAL),
            )
        )
