"""Minimal Home Assistant API stubs for the coreless floor-plan conformance kit.

The fast test suite needs only pytest, not the full Home Assistant dependency.
The adapter's registry, dispatcher and interval-timer APIs are stubbed here;
real HA integration behavior belongs in tests_ha/.

Everything stubbed below is stubbed because the adapter *uses* it. If a
future change reaches for more of Home Assistant, the import will fail
loudly here, which is the right moment to notice.
"""

from __future__ import annotations

import sys
import types
from typing import Any


from pathlib import Path

INTEGRATION = Path(__file__).resolve().parents[1] / "custom_components" / "espeasy_p2p"


def _module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__path__ = []  # a package, so submodules may be stubbed too
    sys.modules[name] = module
    return module


if "homeassistant" not in sys.modules:
    _module("homeassistant")

    core = _module("homeassistant.core")
    core.HomeAssistant = object
    core.callback = lambda func: func

    _module("homeassistant.helpers")

    dispatcher = _module("homeassistant.helpers.dispatcher")
    # A dispatcher that really dispatches: the adapter's whole liveness
    # story is "we fire signals", so a no-op stub would test nothing.
    _listeners: dict[str, list] = {}

    def _connect(hass: Any, signal: str, target) -> Any:
        _listeners.setdefault(signal, []).append(target)
        return lambda: _listeners[signal].remove(target)

    def _send(hass: Any, signal: str, *args: Any) -> None:
        for target in list(_listeners.get(signal, [])):
            target(*args)

    dispatcher.async_dispatcher_connect = _connect
    dispatcher.async_dispatcher_send = _send
    dispatcher.listeners = _listeners

    # The spatial adapter uses a periodic Home Assistant callback to refresh
    # time-derived node quality. The coreless conformance tests only need the
    # unsubscribe contract; the real timer is exercised in tests_ha/.
    event = _module("homeassistant.helpers.event")
    event.async_track_time_interval = (
        lambda hass, action, interval: (lambda: None)
    )

    device_registry = _module("homeassistant.helpers.device_registry")

    class _Device:
        def __init__(self, device_id: str, area_id: str | None) -> None:
            self.id = device_id
            self.area_id = area_id

    class _Registry:
        """Devices the user already placed, keyed by their identifiers.

        A device exists for every unit the coordinator has seen; `areas`
        only records the ones the user actually assigned a room to.
        """

        def __init__(self) -> None:
            self.areas: dict[tuple, str] = {}
            self.known: set = set()

        def async_get_device(self, identifiers):
            for identifier in identifiers:
                if identifier in self.areas or identifier in self.known:
                    return _Device(f"dev-{identifier[1]}",
                                   self.areas.get(identifier))
            return None

    _registry = _Registry()
    device_registry.async_get = lambda hass: _registry
    device_registry.registry = _registry

    # The entity registry, for the one question the adapter asks it: which
    # entities does this device have?
    entity_registry = _module("homeassistant.helpers.entity_registry")

    class _Entry:
        def __init__(self, entity_id: str, entity_category=None) -> None:
            self.entity_id = entity_id
            self.entity_category = entity_category

    class _EntityRegistry:
        def __init__(self) -> None:
            # device id -> list of _Entry
            self.entities: dict[str, list] = {}

    _entities = _EntityRegistry()
    entity_registry.async_get = lambda hass: _entities
    entity_registry.async_entries_for_device = (
        lambda registry, device_id, include_disabled_entities=False:
        registry.entities.get(device_id, [])
    )
    entity_registry.registry = _entities
    entity_registry.Entry = _Entry


# The integration's own package __init__ pulls in half of Home Assistant to
# set up config entries and platforms. The floor-plan adapter needs none of
# that, so it is imported through a bare package that shares the directory
# and skips the __init__ entirely. Anything the adapter really needs is
# stubbed above and will fail loudly if that stops being enough.
_package = types.ModuleType("espeasy_p2p_files")
_package.__path__ = [str(INTEGRATION)]
sys.modules["espeasy_p2p_files"] = _package


import pytest


@pytest.fixture(autouse=True)
def _fresh_dispatcher():
    """Every test gets an empty dispatcher.

    Without this, providers registered by earlier tests stay connected and
    a later test counts their notifications too -- which looked like the
    adapter telling the hub fifty times about one packet.
    """
    from homeassistant.helpers import dispatcher

    dispatcher.listeners.clear()
    yield
    dispatcher.listeners.clear()
