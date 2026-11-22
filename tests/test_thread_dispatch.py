"""The dispatch contract under HA 2026.11: notify never leaves the loop.

``async_notify`` fires ``SIGNAL_DATA_UPDATED``, and it is reached from two
worlds: the event loop (the freshness tick, dispatcher-connected
listeners) and foreign threads -- a dispatcher-connected listener runs
wherever the *sender* ran, and integrations hand ``async_notify`` to their
own machinery. Since HA 2026.11 the async send is rejected from any thread
but the event loop, which made the hub silently stop hearing updates.

The stub dispatcher in conftest mimics that rejection (armed via
``loop_thread``), so these tests pin the real contract: from a foreign
thread, the notify still arrives -- on the loop, via the thread-safe
``dispatcher_send`` -- and no ``async_dispatcher_send`` is ever called
outside the loop.
"""

from __future__ import annotations

import asyncio
import threading

from espeasy_p2p_files.spatial import async_setup_spatial
from espeasy_p2p_files.spatial_hub_provider import (
    SIGNAL_DATA_UPDATED,
    SpatialHubProvider,
)
from homeassistant.helpers import dispatcher

from .spatial_hub_conformance import FakeHass


class FakeEntry:
    domain = "espeasy_p2p"
    entry_id = "abc123"

    def __init__(self) -> None:
        self.unloads: list = []

    def async_on_unload(self, callback) -> None:
        self.unloads.append(callback)


class FakeCoordinator:
    """The parts of the real coordinator the adapter reads."""

    unit = 1
    name = "Home Assistant"
    nodes: dict = {}
    last_seen: dict = {}
    offline: set = set()

    def is_unit_online(self, unit: int) -> bool:
        return unit in self.last_seen and unit not in self.offline

    def async_schedule_resync(self, unit: int) -> None:
        pass


def _register(hass: FakeHass) -> None:
    """Register the provider exactly as the integration does."""
    async_setup_spatial(hass, FakeEntry(), FakeCoordinator())


def _arm_loop(hass: FakeHass):
    """A real event loop in its own thread, and the check armed on it.

    The loop runs for real so ``call_soon_threadsafe`` behaves like it
    does in Home Assistant: the scheduled callback executes on the loop's
    thread, no matter who scheduled it.
    """
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    hass.loop = loop
    dispatcher.loop_thread = thread.ident
    return loop, thread


def _wait_for(event: threading.Event, timeout: float = 5.0) -> None:
    assert event.wait(timeout), (
        "the hub never heard the update -- the notify was lost on the way"
    )


def _stop(loop, thread) -> None:
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5.0)
    loop.close()
    dispatcher.loop_thread = None


def test_a_foreign_thread_may_call_async_notify() -> None:
    """async_notify handed to foreign machinery must survive the call.

    Integrations are free to call ``provider.async_notify`` from wherever
    their own machinery runs, and HA 2026.11 aborts every
    ``async_dispatcher_send`` that happens outside the event loop. The
    call site decides the safety, not the caller's discipline: the notify
    has to land on the loop, whatever thread rang it.
    """
    hass = FakeHass()
    _register(hass)
    provider = SpatialHubProvider(
        hass,
        provider_id="espeasy_p2p",
        name="ESPEasy P2P",
        data=lambda: {"nodes": [], "edges": []},
    )

    loop, thread = _arm_loop(hass)
    heard = threading.Event()
    dispatcher.async_dispatcher_connect(
        hass, SIGNAL_DATA_UPDATED, lambda _pid: heard.set()
    )

    worker_errors: list[Exception] = []

    def foreign_caller() -> None:
        """Anything the integration runs off the loop."""
        try:
            provider.async_notify()
        except Exception as err:  # noqa: BLE001 - the assertion wants it
            worker_errors.append(err)

    worker = threading.Thread(target=foreign_caller)
    worker.start()
    worker.join(timeout=5.0)

    try:
        assert worker_errors == [], (
            f"async_notify blew up off the loop: {worker_errors[0]}"
        )
        _wait_for(heard)
    finally:
        _stop(loop, thread)


def test_a_loop_scheduled_listener_still_reaches_the_hub() -> None:
    """End to end: integration signal -> listener -> notify -> hub.

    The wiring HA actually runs: the provider connects its notify to the
    integration's own signals. Fire such a signal on the loop -- the way
    the coordinator's UDP callbacks do -- and the hub must hear the
    update on the loop, exactly once.
    """
    hass = FakeHass()
    _register(hass)

    loop, thread = _arm_loop(hass)
    arrivals: list[str] = []
    seen = threading.Event()

    def hub_listener(provider_id) -> None:
        assert threading.get_ident() == dispatcher.loop_thread, (
            "the hub's listener ran outside the event loop"
        )
        arrivals.append(str(provider_id))
        seen.set()

    dispatcher.async_dispatcher_connect(hass, SIGNAL_DATA_UPDATED, hub_listener)

    # The coordinator sends its node signals from the event loop; the
    # stub runs the provider's listener right there, as HA would.
    loop.call_soon_threadsafe(
        dispatcher.async_dispatcher_send,
        hass,
        "espeasy_p2p_node_discovered_abc123",
        object(),
    )

    try:
        _wait_for(seen)
        assert arrivals == ["espeasy_p2p"], arrivals
    finally:
        _stop(loop, thread)


def test_notify_from_the_loop_still_notifies_immediately() -> None:
    """On the loop nothing changes: the hub hears the update at once."""
    hass = FakeHass()
    _register(hass)

    # No real loop here -- the stub models "this thread is the loop", so
    # the send happens synchronously like async_dispatcher_send used to.
    dispatcher.loop_thread = threading.get_ident()
    heard: list = []
    dispatcher.async_dispatcher_connect(
        hass, SIGNAL_DATA_UPDATED, heard.append
    )

    provider = SpatialHubProvider(
        hass,
        provider_id="espeasy_p2p",
        name="ESPEasy P2P",
        data=lambda: {"nodes": [], "edges": []},
    )
    provider.async_notify()

    assert heard == ["espeasy_p2p"], (
        f"loop-context notify lost or retargeted: {heard}"
    )
