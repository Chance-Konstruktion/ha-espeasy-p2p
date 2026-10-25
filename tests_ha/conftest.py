"""Gemeinsame Bausteine fuer die echten-HA-Suiten der P2P-Integration.

Bewusst nur normale Fixtures, keine autouse: Die bestehende Suite
(test_gegen_home_assistant.py) soll von dieser Datei nichts merken.
"""

from __future__ import annotations

import asyncio
import random
import socket
from typing import Any

import pytest
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.espeasy_p2p.const import DOMAIN
from custom_components.espeasy_p2p.coordinator import ESPEasyP2PCoordinator
from custom_components.espeasy_p2p.protocol import NodeInfo

# Dokumentationsbereich (RFC 5737) und Schleife -- niemals echte Geraete.
TEST_IP = "192.0.2.50"


def knoten(
    unit: int,
    name: str = "Keller",
    ip: str = TEST_IP,
    mac: str = "aa:bb:cc:dd:ee:ff",
    build: int = 20107,
    node_type: int = 5,
    web_port: int = 80,
) -> NodeInfo:
    """Ein Knoten, wie er ueber Typ-1 hereinkommt."""
    return NodeInfo(
        unit=unit, name=name, ip=ip, mac=mac, build=build,
        node_type=node_type, web_port=web_port,
    )


class AufzeichnenderSockel:
    """Ein UDP-Transportersatz, der sich merkt, was wohin gegangen waere.

    Der SUT ist der Koordinator, nicht der Betriebssystem-Sockel: Die
    Tests pruefen, WELCHE Pakete der Koordinator an WEN schickt -- und
    halten sie dabei an, statt echte Broadcasts ins Netzwerk zu feuern.
    """

    def __init__(self) -> None:
        self.gesendet: list[tuple[bytes, tuple[str, int]]] = []
        self.fehler_bei: str | None = None  # ip, bei der sendto OSError wirft

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        if self.fehler_bei is not None and addr[0] == self.fehler_bei:
            raise OSError("Netz unzugaenglich (Test-Injektion)")
        self.gesendet.append((data, addr))

    def zielte_auf(self, ip: str) -> list[bytes]:
        return [data for data, (ziel_ip, _port) in self.gesendet if ziel_ip == ip]

    def close(self) -> None:  # aiohttp-Transport-Interface
        pass


def signal_sammler(hass: HomeAssistant, coordinator: ESPEasyP2PCoordinator, basis: str) -> list:
    """Verbindet einen echten Dispatcher-Signalhoerer und gibt die Liste
    zurueck, in der die Nutzlasten landen.

    Der Hoerer ist ein HA-@callback: Der Dispatcher stuft nackte
    Funktionen als blockierenden Code ein und schickt sie in den
    Executor -- dann kommt im synchronen Testabschnitt nie etwas an.
    """
    gehoert: list = []

    @callback
    def hoerer(*args: Any) -> None:
        gehoert.append(args[0] if len(args) == 1 else args)

    async_dispatcher_connect(hass, coordinator._signal(basis), hoerer)
    return gehoert


def freier_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
def entry_optionen(request) -> dict:
    """Override-Hebel fuer Tests, die Options brauchen (z.B. Anzeigegenauigkeit).

    Indirekt parametrisierbar:
    ``@pytest.mark.parametrize("entry_optionen", [{...}], indirect=True)``
    """
    return getattr(request, "param", {})


@pytest.fixture
def entry(hass: HomeAssistant, entry_optionen: dict) -> MockConfigEntry:
    """Ein Konfigurationseintrag mit Port, Einheit und Namen.

    Der Port ist zufaellig, ohne ihn vorher zu binden -- pytest-socket
    erlaubt das Binden nur Tests mit ``socket_enabled``, und fuer die
    meisten Tests ist der Port ohnehin nur eine Zahl im Zustand.
    """
    eintrag = MockConfigEntry(
        domain=DOMAIN,
        data={"port": random.randint(30000, 60000), "unit": 250,
              "name": "Home Assistant"},
        options=entry_optionen,
    )
    eintrag.add_to_hass(hass)
    return eintrag


@pytest.fixture
def koordinator(hass: HomeAssistant, entry: MockConfigEntry) -> ESPEasyP2PCoordinator:
    """Der echte Koordinator, aber ohne Sockel: Der UDP-Teil wird durch
    den Aufzeichner ersetzt (async_start wird nicht gerufen)."""
    c = ESPEasyP2PCoordinator(
        hass,
        entry.entry_id,
        entry.data["port"],
        entry.data["unit"],
        entry.data["name"],
    )
    c._transport = AufzeichnenderSockel()
    return c


@pytest.fixture
async def integriert(
    hass: HomeAssistant, entry: MockConfigEntry, monkeypatch, socket_enabled,
    enable_custom_integrations,
) -> ESPEasyP2PCoordinator:
    """Die Integration wirklich hochfahren: async_setup über den
    Config-Entry-Manager, echte Plattformen, echte Dienste.

    ``detect_local_ip`` wird auf die Schleife festgenagelt (sonst
    vermittelt der Netzwerk-Probelauf Flakiness), und nachdem der echte
    Sockel gebunden ist, wird er durch den Aufzeichner ersetzt -- so
    verlaesst kein Testpaket den Rechner, aber alles andere bleibt echt.
    """
    from custom_components.espeasy_p2p import coordinator as coordinator_modul

    monkeypatch.setattr(coordinator_modul, "detect_local_ip", lambda: "127.0.0.1")

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    c: ESPEasyP2PCoordinator = hass.data[DOMAIN][entry.entry_id]
    echter_sockel = c._transport
    aufzeichner = AufzeichnenderSockel()
    c._transport = aufzeichner

    try:
        yield c
    finally:
        c._transport = echter_sockel
        await hass.config_entries.async_unload(entry.entry_id)
        # Der gezielte Resync-Burst (3+8+20 s Schlaf) wird vom Unload
        # nicht abgebrochen -- hier nachholen, damit kein Test auf die
        # vollen 31 Sekunden wartet.
        for task in list(asyncio.all_tasks(hass.loop)):
            if task is not asyncio.current_task() and not task.done():
                task.cancel()
        await hass.async_block_till_done()
