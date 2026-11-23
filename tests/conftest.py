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
import threading
import types
from types import SimpleNamespace
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

    components = _module("homeassistant.components")
    switch_component = _module("homeassistant.components.switch")

    class SwitchDeviceClass:
        # Die echten HA-Member; coreless tests vergleichen nur Identitaet.
        OUTLET = "outlet"
        SWITCH = "switch"

    switch_component.SwitchDeviceClass = SwitchDeviceClass

    _module("homeassistant.helpers")

    dispatcher = _module("homeassistant.helpers.dispatcher")
    # A dispatcher that really dispatches: the adapter's whole liveness
    # story is "we fire signals", so a no-op stub would test nothing.
    _listeners: dict[str, list] = {}

    def _connect(hass: Any, signal: str, target) -> Any:
        _listeners.setdefault(signal, []).append(target)
        return lambda: _listeners[signal].remove(target)

    # Home Assistant 2026.11 rejects async_dispatcher_send from any thread
    # other than the event loop. The stub mimics that check so the suite
    # can pin the shim's contract: dispatching rides the event loop. The
    # check is armed only while a test sets loop_thread -- by default the
    # coreless suite has no loop, and everything runs on the main thread.
    dispatcher.loop_thread = None

    def _send(hass: Any, signal: str, *args: Any) -> None:
        if (
            dispatcher.loop_thread is not None
            and threading.get_ident() != dispatcher.loop_thread
        ):
            raise RuntimeError(
                "async_dispatcher_send called from a thread other than "
                "the event loop"
            )
        for target in list(_listeners.get(signal, [])):
            target(*args)

    def _send_threadsafe(hass: Any, signal: str, *args: Any) -> None:
        """dispatcher_send: the thread-safe variant, like HA's own."""
        loop = getattr(hass, "loop", None)
        if loop is not None and hasattr(loop, "call_soon_threadsafe"):
            loop.call_soon_threadsafe(_send, hass, signal, *args)
        else:
            _send(hass, signal, *args)

    dispatcher.async_dispatcher_connect = _connect
    dispatcher.async_dispatcher_send = _send
    dispatcher.dispatcher_send = _send_threadsafe
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

    # The switch platform (tests/test_switch_command.py) drives the real
    # entity class, so the bases it inherits from and the few helpers it
    # imports need stubs too.
    components = _module("homeassistant.components")

    switch_component = _module("homeassistant.components.switch")

    class _SwitchEntity:
        def async_write_ha_state(self):
            writes = getattr(self, "state_writes", 0)
            self.state_writes = writes + 1

    class SwitchDeviceClass:  # pragma: no cover - attribute namespace only
        SWITCH = "switch"
        OUTLET = "outlet"

    switch_component.SwitchEntity = _SwitchEntity
    switch_component.SwitchDeviceClass = SwitchDeviceClass
    components.switch = switch_component

    config_entries = _module("homeassistant.config_entries")
    config_entries.ConfigEntry = object

    aiohttp_client = _module("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_get_clientsession = lambda hass: hass.clientsession

    device_registry.DeviceInfo = dict
    device_registry.format_mac = lambda mac: str(mac)

    entity_platform = _module("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = object

    # switch.py meldet ein Nein des Knotens als HomeAssistantError (#4).
    exceptions = _module("homeassistant.exceptions")

    class HomeAssistantError(Exception):
        pass

    exceptions.HomeAssistantError = HomeAssistantError

    restore_state = _module("homeassistant.helpers.restore_state")

    class _RestoreEntity:
        async def async_get_last_state(self):
            return None

    restore_state.RestoreEntity = _RestoreEntity

    # The config flow (tests/test_config_flow_import.py) builds its forms
    # with the selector helpers.
    selector = _module("homeassistant.helpers.selector")

    class _Selector:
        def __init__(self, config=None):
            self.config = config

    class _SelectorConfig(dict):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

    selector.BooleanSelector = _Selector
    selector.NumberSelector = _Selector
    selector.SelectSelector = _Selector
    selector.TextSelector = _Selector
    selector.SelectOptionDict = _SelectorConfig
    selector.NumberSelectorConfig = _SelectorConfig
    selector.SelectSelectorConfig = _SelectorConfig
    selector.TextSelectorConfig = _SelectorConfig
    selector.NumberSelectorMode = SimpleNamespace(BOX="box")
    selector.SelectSelectorMode = SimpleNamespace(LIST="list")

    data_entry_flow = _module("homeassistant.data_entry_flow")
    data_entry_flow.FlowResult = dict


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
    """Every test gets an empty, disarmed dispatcher.

    Without this, providers registered by earlier tests stay connected and
    a later test counts their notifications too -- which looked like the
    adapter telling the hub fifty times about one packet. The loop-thread
    check is disarmed as well, so an armed check never leaks into the
    next test when one of these fails.
    """
    from homeassistant.helpers import dispatcher

    dispatcher.listeners.clear()
    dispatcher.loop_thread = None
    yield
    dispatcher.listeners.clear()
    dispatcher.loop_thread = None
