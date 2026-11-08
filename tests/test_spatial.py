"""The floor-plan adapter: the picture, and whether it is an honest one.

This integration cannot measure the path between two ESP nodes -- they
broadcast and we listen. So the plan shows what is actually known: Home
Assistant in the middle, one edge per unit, coloured by how long ago that
unit was last heard from. These tests are mostly about keeping that claim
true.
"""

from __future__ import annotations

import pytest

from espeasy_p2p_files import const  # noqa: E402  (conftest builds it)
from espeasy_p2p_files.spatial import HUB_ID, async_setup_spatial
from homeassistant.helpers import device_registry as dr

from .spatial_hub_conformance import FakeHass, SpatialHubConformance, check


class FakeNode:
    def __init__(self, unit: int, name: str) -> None:
        self.unit, self.name = unit, name
        self.ip = f"192.168.1.{unit}"
        self.mac = f"aa:bb:cc:00:00:{unit:02d}"
        self.build = 20107
        self.node_type_name = "ESP Easy32"


class FakeCoordinator:
    """The parts of the real coordinator the adapter reads."""

    def __init__(self) -> None:
        self.unit = 1
        self.name = "Home Assistant"
        self.nodes: dict[int, FakeNode] = {}
        self.last_seen: dict[int, float] = {}
        self.offline: set[int] = set()
        self.resynced: list[int] = []

    def is_unit_online(self, unit: int) -> bool:
        return unit in self.last_seen and unit not in self.offline

    def async_schedule_resync(self, unit: int) -> None:
        self.resynced.append(unit)


class FakeEntry:
    domain = "espeasy_p2p"
    entry_id = "abc123"

    def __init__(self) -> None:
        self.unloads: list = []

    def async_on_unload(self, callback) -> None:
        self.unloads.append(callback)


@pytest.fixture
def mesh(monkeypatch):
    """Two units: one heard from just now, one silent for a long time."""
    import time

    now = time.monotonic()
    coordinator = FakeCoordinator()
    coordinator.nodes = {5: FakeNode(5, "Garage"), 9: FakeNode(9, "Schuppen")}
    coordinator.last_seen = {5: now, 9: now - const.NODE_OFFLINE_TIMEOUT * 2}
    coordinator.offline = {9}

    hass, entry = FakeHass(), FakeEntry()
    async_setup_spatial(hass, entry, coordinator)
    return hass, entry, coordinator


def _payload(hass):
    return hass.registrations["espeasy_p2p"]["data"]()


# ── The contract ──────────────────────────────────────────


class TestSpatialHub(SpatialHubConformance):
    """The kit a stranger would run, run on ourselves."""

    def build_registration(self):
        coordinator = FakeCoordinator()
        coordinator.nodes = {5: FakeNode(5, "Garage")}
        coordinator.last_seen = {5: 0.0}
        hass = FakeHass()
        async_setup_spatial(hass, FakeEntry(), coordinator)
        return hass.registrations["espeasy_p2p"]


def test_an_empty_mesh_is_not_an_error(mesh):
    """Before the first packet there is one node: us. Not a crash."""
    coordinator = FakeCoordinator()
    hass = FakeHass()
    async_setup_spatial(hass, FakeEntry(), coordinator)

    payload = _payload(hass)

    assert [n["id"] for n in payload["nodes"]] == [HUB_ID]
    assert payload["edges"] == []
    assert check(hass.registrations["espeasy_p2p"]) == []


# ── The picture ───────────────────────────────────────────


def test_every_unit_hangs_off_home_assistant(mesh):
    hass, _, _ = mesh
    payload = _payload(hass)

    assert [n["id"] for n in payload["nodes"]] == [HUB_ID, "unit-5", "unit-9"]
    assert all(e["source"] == HUB_ID for e in payload["edges"]), (
        "an edge between two ESP nodes would be invented -- nothing here "
        "measures the path between them"
    )


def test_a_silent_node_looks_silent(mesh):
    hass, _, _ = mesh
    nodes = {n["id"]: n for n in _payload(hass)["nodes"]}
    edges = {e["target"]: e for e in _payload(hass)["edges"]}

    assert nodes["unit-5"]["state"] == "online"
    assert edges["unit-5"]["quality"] == "good"
    assert nodes["unit-9"]["state"] == "offline"
    assert edges["unit-9"]["quality"] == "poor"


def test_the_warning_band_exists_at_all(mesh):
    """A node about to drop off must be visible before it does.

    Only two colours -- fine and gone -- would make the plan useless for
    the one thing it is good at here.
    """
    hass, _, coordinator = mesh
    import time

    coordinator.last_seen[5] = time.monotonic() - const.NODE_OFFLINE_TIMEOUT * 0.8

    edges = {e["target"]: e for e in _payload(hass)["edges"]}
    assert edges["unit-5"]["quality"] == "fair"


def test_edges_are_dashed_because_nothing_measured_them(mesh):
    hass, _, _ = mesh
    assert all(e["dashed"] for e in _payload(hass)["edges"])


def test_a_node_never_heard_from_is_unknown_not_good(mesh):
    hass, _, coordinator = mesh
    coordinator.nodes[7] = FakeNode(7, "Neu")

    edges = {e["target"]: e for e in _payload(hass)["edges"]}
    assert edges["unit-7"]["quality"] == "unknown"


def test_the_area_comes_from_the_device_the_user_already_placed(mesh):
    """Asking again in a floor-plan editor is the work we are avoiding."""
    hass, _, _ = mesh
    dr.registry.areas[("espeasy_p2p", "unit-5")] = "garage"

    nodes = {n["id"]: n for n in _payload(hass)["nodes"]}
    assert nodes["unit-5"]["area_id"] == "garage"
    assert not nodes["unit-9"].get("area_id"), "an unplaced device invents nothing"


def test_the_metadata_is_what_you_would_want_in_the_popup(mesh):
    hass, _, _ = mesh
    node = next(n for n in _payload(hass)["nodes"] if n["id"] == "unit-5")

    assert node["metadata"]["ip"] == "192.168.1.5"
    assert node["metadata"]["typ"] == "ESP Easy32"
    assert node["metadata"]["letztes_paket_vor"] is not None


# ── Liveness, which is the whole reason for signals= ──────


def test_the_hub_hears_about_a_new_node_without_a_coordinator(mesh):
    """No DataUpdateCoordinator here. The signals we already fire do it."""
    from homeassistant.helpers import dispatcher

    _, entry, _ = mesh
    told: list[str] = []
    dispatcher.async_dispatcher_connect(
        None, "spatial_hub_data_updated", told.append
    )

    dispatcher.async_dispatcher_send(
        None, f"{const.SIGNAL_NODE_DISCOVERED}_{entry.entry_id}", None
    )
    dispatcher.async_dispatcher_send(
        None, f"{const.SIGNAL_NODE_AVAILABILITY}_{entry.entry_id}", 5
    )

    assert told == ["espeasy_p2p", "espeasy_p2p"]


def test_unloading_stops_the_chatter(mesh):
    from homeassistant.helpers import dispatcher

    hass, entry, _ = mesh
    told: list[str] = []
    dispatcher.async_dispatcher_connect(
        None, "spatial_hub_data_updated", told.append
    )

    for unload in entry.unloads:
        unload()
    dispatcher.async_dispatcher_send(
        None, f"{const.SIGNAL_NODE_REMOVED}_{entry.entry_id}", 5
    )

    assert told == []
    assert "espeasy_p2p" not in hass.registrations


# ── The action ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resync_reaches_the_right_unit(mesh):
    hass, _, coordinator = mesh
    run = hass.registrations["espeasy_p2p"]["action"]

    assert await run("node", "unit-9", "resync", {}) == {"success": True}
    assert coordinator.resynced == [9]


@pytest.mark.asyncio
async def test_a_made_up_action_is_refused_rather_than_guessed(mesh):
    hass, _, coordinator = mesh
    run = hass.registrations["espeasy_p2p"]["action"]

    assert (await run("node", "unit-9", "explode", {}))["success"] is False
    assert (await run("node", HUB_ID, "resync", {}))["success"] is False
    assert coordinator.resynced == []


@pytest.mark.asyncio
async def test_a_broken_unit_id_is_refused_not_raised(mesh):
    """'unit-xyz' must not throw a ValueError out of the hub action --
    the hub gets a clean no, like for an unknown action."""
    hass, _, coordinator = mesh
    run = hass.registrations["espeasy_p2p"]["action"]

    assert (await run("node", "unit-xyz", "resync", {}))["success"] is False
    assert (await run("node", "unit-", "resync", {}))["success"] is False
    assert coordinator.resynced == []


# ── The doors in the popup ────────────────────────────────────────────


def test_a_node_names_an_entity_so_the_popup_has_something_to_open(mesh):
    """The popup came up empty in a real house: no more-info, no device
    page, no way through to the settings. Nothing was broken -- nothing
    had been offered. Naming one entity is what the hub builds all of it
    from, and this integration never named one."""
    from homeassistant.helpers import entity_registry as er

    hass, _, _ = mesh
    dr.registry.known.add(("espeasy_p2p", "unit-5"))
    er.registry.entities["dev-unit-5"] = [
        er.Entry("sensor.unit_5_temperature"),
        er.Entry("switch.unit_5_relay"),
    ]

    node = next(n for n in _payload(hass)["nodes"] if n["id"] == "unit-5")
    assert node["entity_id"] == "sensor.unit_5_temperature"


def test_the_unit_opens_as_itself_not_as_its_uptime_counter(mesh):
    from homeassistant.helpers import entity_registry as er

    hass, _, _ = mesh
    dr.registry.known.add(("espeasy_p2p", "unit-5"))
    er.registry.entities["dev-unit-5"] = [
        er.Entry("sensor.unit_5_uptime", entity_category="diagnostic"),
        er.Entry("switch.unit_5_relay"),
    ]

    node = next(n for n in _payload(hass)["nodes"] if n["id"] == "unit-5")
    assert node["entity_id"] == "switch.unit_5_relay", "diagnostics come last"


def test_the_same_entity_is_named_on_every_refresh(mesh):
    """A popup that reshuffles between two refreshes is a popup nobody
    trusts."""
    from homeassistant.helpers import entity_registry as er

    hass, _, _ = mesh
    dr.registry.known.add(("espeasy_p2p", "unit-5"))
    er.registry.entities["dev-unit-5"] = [
        er.Entry("switch.b"), er.Entry("sensor.a"), er.Entry("sensor.c"),
    ]

    first = next(n for n in _payload(hass)["nodes"] if n["id"] == "unit-5")
    er.registry.entities["dev-unit-5"].reverse()
    second = next(n for n in _payload(hass)["nodes"] if n["id"] == "unit-5")

    assert first["entity_id"] == second["entity_id"] == "sensor.a"


def test_a_unit_with_no_device_yet_names_nothing(mesh):
    """Just heard from, not registered yet. Better silent than wrong."""
    hass, _, _ = mesh

    node = next(n for n in _payload(hass)["nodes"] if n["id"] == "unit-9")
    assert not node.get("entity_id")


def test_a_device_with_no_entities_names_nothing(mesh):
    from homeassistant.helpers import entity_registry as er

    hass, _, _ = mesh
    dr.registry.known.add(("espeasy_p2p", "unit-5"))
    er.registry.entities["dev-unit-5"] = []

    node = next(n for n in _payload(hass)["nodes"] if n["id"] == "unit-5")
    assert not node.get("entity_id")


def test_the_area_still_comes_from_the_device(mesh):
    """The entity lookup must not have cost the area on the way past."""
    hass, _, _ = mesh
    dr.registry.areas[("espeasy_p2p", "unit-5")] = "garage"

    nodes = {n["id"]: n for n in _payload(hass)["nodes"]}
    assert nodes["unit-5"]["area_id"] == "garage"
