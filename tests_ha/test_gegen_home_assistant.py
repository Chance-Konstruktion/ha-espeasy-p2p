"""Der Spatial-Adapter gegen ein echtes Home Assistant.

Was diese Datei prueft und was nicht -- das gehoert an den Anfang, sonst
liest sich ein gruener Lauf hier groesser, als er ist.

Anders als die Suite in ``tests/`` steht hier nichts Nachgebautes vom
Home-Assistant-Kern: echtes ``hass``, echte Registries, echter Config
Entry. Was hier haelt, haelt gegen die Schnittstelle, die die Integration
im Haus wirklich vorfindet -- und die jede neue HA-Version brechen kann,
ohne dass ein nachgebauter Test etwas davon merkt.

Der Koordinator ist bewusst ein Doppel. Er ist Teil DIESER Integration,
kein fremdes Innenleben; ihn hier echt hochzufahren hiesse, ein
ESPEasy-Netz nachzustellen, und das koennte diese Datei nur behaupten,
nicht pruefen. Getestet wird also der Weg vom Koordinator zum Hub -- und
genau der laeuft ueber Home Assistants echte Bausteine.

Der teuerste Fehler in diesem Weg ist nicht ein falscher Knoten, sondern
eine Ausnahme in ``data()``: Der Hub verwirft dann **die ganze Ebene**
fuer diesen Durchlauf, nicht nur den einen Knoten. Deshalb steht der
leere Fall hier ganz vorn.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from spatial_hub_conformance import check

from custom_components.espeasy_p2p.const import NODE_AGING_INTERVAL
from custom_components.espeasy_p2p.protocol import NodeInfo
from custom_components.espeasy_p2p.spatial import async_setup_spatial

EIGENE_DOMAIN = "espeasy_p2p"


def _einheit(unit: int, name: str) -> NodeInfo:
    """Eine Einheit, wie der Koordinator sie fuehrt.

    Bewusst die echte Datenklasse der Integration und kein eigenes
    Woerterbuch: Beim ersten Anlauf stand hier ``{"name": ...}``, und der
    Adapter liest ``info.name`` -- die Pipeline meldete "'dict' object has
    no attribute 'name'". Mit NodeInfo bricht dieser Test, sobald sich das
    Feld umbenennt, statt an einem Nachbau vorbeizulaufen.
    """
    return NodeInfo(
        unit=unit, name=name, ip=f"192.168.1.{unit}",
        mac=f"aa:bb:cc:dd:ee:{unit:02x}", build=20107,
        node_type=1, web_port=80,
    )


class KoordinatorDoppel:
    """Genau das, was der Adapter vom Koordinator liest -- nicht mehr.

    Absichtlich kein Nachbau der ganzen Klasse: Ein Doppel, das mehr kann
    als gebraucht wird, faellt beim naechsten Umbau nicht auf, sondern
    deckt ihn zu.

    Die Liste stammt nicht aus dem Gedaechtnis, sondern aus dem Adapter::

        grep -o "coordinator\\.[a-zA-Z_]*" custom_components/espeasy_p2p/spatial.py

    Beim ersten Anlauf fehlte ``is_unit_online``, und der Konformitaetssatz
    hat es gemeldet -- nicht als "Attribut fehlt", sondern als das, was es
    im Betrieb waere: *data() raised AttributeError -- the hub would drop
    your whole layer for that refresh.* Genau dieser Satz ist der Grund,
    warum es diese Datei gibt.
    """

    def __init__(self, nodes=None, last_seen=None, offline=()):
        self.name = "Home Assistant"
        self.unit = 0
        self.nodes = nodes or {}
        self.last_seen = last_seen or {}
        self._offline = set(offline)

    def is_unit_online(self, unit: int) -> bool:
        return unit not in self._offline

    def async_schedule_resync(self, *args, **kwargs) -> None:
        """Der Adapter stoesst eine Neuabfrage an; hier passiert nichts."""


@pytest.fixture
def eintrag(hass: HomeAssistant):
    eigener = MockConfigEntry(domain=EIGENE_DOMAIN, title="ESPEasy P2P")
    eigener.add_to_hass(hass)
    return eigener


def _registrierung(hass, eintrag, koordinator):
    async_setup_spatial(hass, eintrag, koordinator)
    providers = hass.data.get("spatial_hub_providers") or {}
    assert EIGENE_DOMAIN in providers, (
        f"Der Adapter hat sich nicht angemeldet. Vorhanden: {sorted(providers)}"
    )
    return providers[EIGENE_DOMAIN]


@pytest.fixture
def leer(hass: HomeAssistant, eintrag):
    return _registrierung(hass, eintrag, KoordinatorDoppel())


@pytest.fixture
def besetzt(hass: HomeAssistant, eintrag):
    """Zwei Einheiten, eine davon offline und nie gesehen.

    Beides absichtlich gemischt: Der Knoten ohne ``last_seen`` laeuft durch
    den Zweig, in dem die Stille unbekannt ist (``silent_for = None``) --
    der Fall, in dem eine Rechnung mit None gern abstuerzt.
    """
    return _registrierung(
        hass,
        eintrag,
        KoordinatorDoppel(
            nodes={1: _einheit(1, "Keller"), 2: _einheit(2, "Dach")},
            last_seen={1: 0.0},
            offline=(2,),
        ),
    )


# ── Anmeldung ─────────────────────────────────────────────────────────


def test_der_adapter_meldet_sich_am_hub_an(leer):
    """Ohne Eintrag in hass.data existiert die Ebene fuer den Hub nicht."""
    assert leer["provider_id"] == EIGENE_DOMAIN
    assert leer["name"]
    assert callable(leer["data"])


@pytest.mark.parametrize("welche", ["leer", "besetzt"])
def test_die_anmeldung_haelt_den_hub_vertrag_ein(welche, request):
    """Der mitgelieferte Konformitaets-Satz, gegen ein echtes hass.

    Beide Zustaende, weil sie verschiedene Wege nehmen: ohne Einheiten
    gibt es keine Kanten, mit Einheiten schon -- und Kanten auf Knoten,
    die es nicht gibt, sind der Klassiker, der Grundrisse zerlegt.
    """
    probleme = check(request.getfixturevalue(welche))
    assert not probleme, "Verstoesse gegen den Hub-Vertrag:\n  " + "\n  ".join(probleme)


# ── Das Verhalten ─────────────────────────────────────────────────────


def test_ohne_einheiten_bleibt_die_ebene_leer_statt_kaputt(leer):
    """Kein Netz heisst leer -- nicht Ausnahme.

    Der teure Fehler waere ein Stapelabzug im Log statt "hier ist nichts":
    Der Hub wirft dann die ganze Ebene weg.
    """
    nutzlast = leer["data"]()
    assert nutzlast["edges"] == []
    # Der virtuelle Peer (Home Assistant selbst) steht immer da.
    assert [k["id"] for k in nutzlast["nodes"]]


def test_jede_kante_zeigt_auf_einen_knoten_den_es_gibt(besetzt):
    nutzlast = besetzt["data"]()
    ids = {k["id"] for k in nutzlast["nodes"]}
    for kante in nutzlast["edges"]:
        assert kante["source"] in ids, f"Kante ins Leere: {kante}"
        assert kante["target"] in ids, f"Kante ins Leere: {kante}"


def test_data_bleibt_ueber_wiederholte_abfragen_ruhig(besetzt):
    """Der Hub fragt im Takt. Ein Zustand, der beim ersten Aufruf angelegt
    und beim zweiten falsch gelesen wird, faellt sonst erst nach Minuten
    auf -- und dann im Betrieb."""
    erste = besetzt["data"]()
    for _ in range(3):
        weitere = besetzt["data"]()
    assert {k["id"] for k in erste["nodes"]} == {k["id"] for k in weitere["nodes"]}


def test_die_nutzlast_ueberlebt_den_websocket(besetzt):
    """Alles geht als JSON an den Browser. Ein datetime, ein set oder eine
    eigene Klasse in den Metadaten nimmt das ganze Modell mit -- fuer
    jeden Anbieter, nicht nur diesen."""
    json.dumps(besetzt["data"]())


# ── Signale ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "signal", ["node_discovered", "node_removed", "node_availability"]
)
async def test_ein_knotensignal_erreicht_den_hub(hass, eintrag, signal):
    """Ein Knotensignal wird zu SIGNAL_DATA_UPDATED -- auf dem Event-Loop.

    Bis SDK 7 hing am Signal ein nacktes ``lambda``. Der Dispatcher von
    Home Assistant haelt so etwas fuer blockierenden Code und schickt es in
    den Executor; dort ruft ``async_notify`` dann ``async_dispatcher_send``
    aus einem fremden Thread, HA 2026.10 bricht das mit "calls
    async_dispatcher_send from a thread other than the event loop" ab, und
    der Hub hoert nie davon. Nur im Log steht es -- deshalb prueft dieser
    Test, dass die Meldung ankommt, nicht bloss, dass nichts abstuerzt.
    """
    from homeassistant.core import callback
    from homeassistant.helpers.dispatcher import (
        async_dispatcher_connect,
        async_dispatcher_send,
    )

    from custom_components.espeasy_p2p.spatial_hub_provider import (
        SIGNAL_DATA_UPDATED,
    )

    _registrierung(hass, eintrag, KoordinatorDoppel())
    gehoert = []
    async_dispatcher_connect(
        hass, SIGNAL_DATA_UPDATED, callback(lambda pid: gehoert.append(pid))
    )

    async_dispatcher_send(
        hass, f"espeasy_p2p_{signal}_{eintrag.entry_id}", _einheit(1, "Keller")
    )
    await hass.async_block_till_done()

    assert gehoert == [EIGENE_DOMAIN]



async def test_freshness_tick_notifies_hub_even_when_sensor_state_is_unchanged(
    hass, eintrag, monkeypatch
):
    """Time-derived edge quality must refresh even without new entity states."""
    import custom_components.espeasy_p2p.spatial as spatial_module

    scheduled = {}

    def fake_track_time_interval(_hass, action, interval):
        scheduled["action"] = action
        scheduled["interval"] = interval
        return lambda: None

    monkeypatch.setattr(
        spatial_module, "async_track_time_interval", fake_track_time_interval
    )
    _registrierung(
        hass,
        eintrag,
        KoordinatorDoppel(
            nodes={1: _einheit(1, "Keller")},
            last_seen={1: 0.0},
        ),
    )

    from homeassistant.core import callback
    from homeassistant.helpers.dispatcher import async_dispatcher_connect
    from custom_components.espeasy_p2p.spatial_hub_provider import SIGNAL_DATA_UPDATED

    gehoert = []
    async_dispatcher_connect(
        hass, SIGNAL_DATA_UPDATED, callback(lambda pid: gehoert.append(pid))
    )

    assert scheduled["interval"] == timedelta(seconds=NODE_AGING_INTERVAL)
    scheduled["action"](None)
    await hass.async_block_till_done()

    assert gehoert == [EIGENE_DOMAIN]
