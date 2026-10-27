"""Die Dienste und der Lebenszyklus: anfassen, abklemmen, wieder anwerfen.

``__init__.py`` ist der Teil, den die gesamte andere Suite umgeht: Er
bindet den UDP-Sockel, meldet die Dienste an, haengt den Spatial-Adapter
an und raeumt beim Entladen ab. Diese Suite faehrt die Integration ueber
den echten Config-Entry-Manager hoch (siehe conftest ``integriert``) und
ruft die Dienste als Dienste auf -- wie ein Skript im Haus es taete.
"""

from __future__ import annotations

import socket
import time

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.espeasy_p2p import async_setup_entry, async_unload_entry
from custom_components.espeasy_p2p.const import (
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_UNIT,
    DOMAIN,
)
from custom_components.espeasy_p2p.registry import device_for_unit

from conftest import (
    TEST_IP,
    AufzeichnenderSockel,
    knoten,
)


DIENSTE = ["scan", "refetch_metadata", "send_command", "set_gpio_pin", "remove_node"]


# ── Anmeldung ─────────────────────────────────────────────────────────


def test_alle_dienste_sind_angemeldet(integriert, hass: HomeAssistant) -> None:
    for dienst in DIENSTE:
        assert hass.services.has_service(DOMAIN, dienst), dienst


async def test_scan_dienst_broadcastet(integriert, hass: HomeAssistant) -> None:
    await hass.services.async_call(DOMAIN, "scan", {}, blocking=True)
    sockel: AufzeichnenderSockel = integriert._transport  # type: ignore[assignment]
    assert sockel.zielte_auf("255.255.255.255"), "der Scan ist ein Broadcast"


async def test_refetch_dienst_holt_json(
    integriert, hass: HomeAssistant, aioclient_mock
) -> None:
    integriert.nodes[5] = knoten(5)
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/json", json={"Sensors": []})

    await hass.services.async_call(DOMAIN, "refetch_metadata", {}, blocking=True)

    assert aioclient_mock.call_count == 1


async def test_send_command_dienst_trifft_die_einheit(
    integriert, hass: HomeAssistant, aioclient_mock
) -> None:
    integriert.nodes[5] = knoten(5)
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/control", text="OK")

    await hass.services.async_call(
        DOMAIN, "send_command", {"unit": 5, "command": "restart"}, blocking=True
    )
    assert aioclient_mock.call_count == 1
    sockel: AufzeichnenderSockel = integriert._transport  # type: ignore[assignment]
    assert len(sockel.zielte_auf(TEST_IP)) == 1


async def test_send_command_unbekannte_einheit_bleibt_leise(
    integriert, hass: HomeAssistant, aioclient_mock, caplog
) -> None:
    await hass.services.async_call(
        DOMAIN, "send_command", {"unit": 99, "command": "restart"}, blocking=True
    )
    assert aioclient_mock.call_count == 0
    assert "not found in any entry" in caplog.text


async def test_set_gpio_pin_dienst_persistiert(
    integriert, hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    integriert.nodes[5] = knoten(5)

    await hass.services.async_call(
        DOMAIN, "set_gpio_pin",
        {"unit": 5, "task_name": "Relais", "pin": 26}, blocking=True,
    )
    await hass.async_block_till_done()

    assert integriert.pin_overrides == {"5/Relais": 26}
    # Persistiert in den Optionen des Eintrags -- ueberlebt den Neustart:
    assert entry.options["gpio_pin_map"] == {"5/Relais": 26}




async def test_set_gpio_pin_dienst_lehnt_ungueltige_pins_ab(
    integriert, hass: HomeAssistant, caplog
) -> None:
    integriert.nodes[5] = knoten(5)

    for pin in (-1, 41, 999):
        await hass.services.async_call(
            DOMAIN, "set_gpio_pin",
            {"unit": 5, "task_name": "Relais", "pin": pin},
            blocking=True,
        )

    assert integriert.pin_overrides == {}
    assert "outside supported range 0..40" in caplog.text


async def test_set_gpio_pin_dienst_lehnt_leeren_tasknamen_ab(
    integriert, hass: HomeAssistant, caplog
) -> None:
    integriert.nodes[5] = knoten(5)

    await hass.services.async_call(
        DOMAIN, "set_gpio_pin",
        {"unit": 5, "task_name": "   ", "pin": 13},
        blocking=True,
    )

    assert integriert.pin_overrides == {}
    assert "task_name must not be empty" in caplog.text

async def test_set_gpio_pin_unbekannte_einheit(
    integriert, hass: HomeAssistant, caplog
) -> None:
    await hass.services.async_call(
        DOMAIN, "set_gpio_pin",
        {"unit": 99, "task_name": "x", "pin": 1}, blocking=True,
    )
    assert integriert.pin_overrides == {}
    assert "set_gpio_pin: unit 99 not found" in caplog.text


async def test_remove_node_dienst_ohne_geraet(
    integriert, hass: HomeAssistant
) -> None:
    """Knoten noch ohne Registry-Eintrag: Der Dienst raeumt den Zustand."""
    integriert.nodes[5] = knoten(5, ip="0.0.0.0")
    integriert.tasks[(5, 0)] = type("T", (), {"src_unit": 5, "task_index": 0})()

    await hass.services.async_call(DOMAIN, "remove_node", {"unit": 5}, blocking=True)

    assert integriert.nodes == {}
    assert integriert.tasks == {}


async def test_remove_node_dienst_mit_geraet_entfernt_geraet(
    integriert, hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    geraet = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "unit-5")},
        name="Keller",
    )
    integriert.nodes[5] = knoten(5, ip="0.0.0.0")
    integriert.tasks[(5, 0)] = type("T", (), {"src_unit": 5, "task_index": 0})()

    await hass.services.async_call(
        DOMAIN, "remove_node", {"unit": 5}, blocking=True
    )

    assert integriert.nodes == {}
    assert device_for_unit(hass, 5) is None

async def test_remove_node_unbekannte_einheit(
    integriert, hass: HomeAssistant, caplog
) -> None:
    await hass.services.async_call(DOMAIN, "remove_node", {"unit": 99}, blocking=True)
    assert "remove_node: unit 99 not found" in caplog.text


# ── Geraeteloeschung ueber die Weboberflaeche ─────────────────────────


async def test_geraet_loeschen_vergisst_den_zustand(
    integriert, hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """HA ruft async_remove_config_entry_device, wenn der Nutzer das
    Geraet im UI loescht. Der Knoten darf danach nur bei einem frischen
    Lebenszeichen wiederkommen -- der Speicher ist weg."""
    from custom_components.espeasy_p2p import async_remove_config_entry_device

    gerät = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "unit-5")},
        name="Keller",
    )
    integriert._on_node(knoten(5))
    await hass.async_block_till_done()
    assert 5 in integriert.nodes

    ergebnis = await async_remove_config_entry_device(hass, entry, gerät)

    assert ergebnis is True
    assert 5 not in integriert.nodes
    assert integriert.tasks == {}
    assert integriert.last_seen == {}


async def test_fremdes_geraet_wird_ohne_wirkung_akzeptiert(
    integriert, hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """True fuer alles Fremde ist die Regel -- HA erwartet eine
    Ja/Nein-Antwort, keine Ausnahme. Aber der Zustand bleibt."""
    from custom_components.espeasy_p2p import async_remove_config_entry_device

    gerät = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("irgendein_ding", "xyz")},
    )
    integriert.nodes[5] = knoten(5, ip="0.0.0.0")

    assert await async_remove_config_entry_device(hass, entry, gerät) is True
    assert 5 in integriert.nodes


async def test_geraet_mit_kaputtem_kennzeichen_crasht_nichts(
    integriert, hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    from custom_components.espeasy_p2p import async_remove_config_entry_device

    gerät = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "unit-keinezahl")},
    )
    assert await async_remove_config_entry_device(hass, entry, gerät) is True


# ── Entladen und Wiederanwerfen ───────────────────────────────────────


async def test_entladen_klemmt_alles_ab(
    integriert, hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert hass.data.get(DOMAIN, {}) == {}
    for dienst in DIENSTE:
        assert not hass.services.has_service(DOMAIN, dienst), dienst
    assert integriert._transport is None, "der UDP-Sockel ist zu"





async def test_fehlgeschlagenes_platform_unload_belaesst_koordinator_und_dienste(
    integriert, hass: HomeAssistant, entry: MockConfigEntry, monkeypatch
) -> None:
    """A failed platform unload must not orphan remaining entities."""
    original = hass.config_entries.async_unload_platforms

    async def fail_unload(_entry, _platforms):
        return False

    monkeypatch.setattr(hass.config_entries, "async_unload_platforms", fail_unload)
    try:
        result = await async_unload_entry(hass, entry)
    finally:
        monkeypatch.setattr(hass.config_entries, "async_unload_platforms", original)

    assert result is False
    assert hass.data[DOMAIN][entry.entry_id] is integriert
    assert integriert._transport is not None
    assert hass.services.has_service(DOMAIN, "scan")

async def test_leerer_eintrag_bekommt_die_defaults_zurueckgeschrieben(
    hass: HomeAssistant, monkeypatch, socket_enabled, enable_custom_integrations
) -> None:
    """Aeltere Eintraege kamen mit leerem Daten-Paket daher -- beim Laden
    werden die aufgeloesten Defaults persistiert."""
    from custom_components.espeasy_p2p import coordinator as coordinator_modul

    monkeypatch.setattr(coordinator_modul, "detect_local_ip", lambda: "127.0.0.1")
    eintrag = MockConfigEntry(domain=DOMAIN, data={})
    eintrag.add_to_hass(hass)

    assert await hass.config_entries.async_setup(eintrag.entry_id)
    await hass.async_block_till_done()

    try:
        assert eintrag.data == {
            "port": DEFAULT_PORT, "unit": DEFAULT_UNIT, "name": DEFAULT_NAME,
        }
    finally:
        await hass.config_entries.async_unload(eintrag.entry_id)
        await hass.async_block_till_done()


async def test_besetzter_port_fuehrt_zu_not_ready(
    hass: HomeAssistant, entry: MockConfigEntry, monkeypatch, socket_enabled,
    enable_custom_integrations,
) -> None:
    """Der UDP-Port ist belegt (Doppelstart, Reload-Rennzustand): Die
    Einrichtung wirft ConfigEntryNotReady -- HA retryt mit Rueckstau,
    statt den Eintrag endgueltig zu begraben."""
    from custom_components.espeasy_p2p import coordinator as coordinator_modul

    # Nur die Netzwerk-Erkennung wird stillgelegt; der Bind-Konflikt
    # unten ist der echte Testgegenstand.
    monkeypatch.setattr(coordinator_modul, "detect_local_ip", lambda: "127.0.0.1")
    # Ohne SO_REUSEADDR: Genau dann scheitert der zweite Bind -- der
    # Reload-Rennzustand, gegen den der NotReady-Pfad existiert. (Mit
    # REUSEADDR auf beiden Seiten liesse Linux den Doppel-Bind still zu.)
    besetzt = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        besetzt.bind(("0.0.0.0", entry.data["port"]))
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)
    finally:
        besetzt.close()


async def test_nach_entladen_und_wiederanwerfen_lebt_alles(
    hass: HomeAssistant, entry: MockConfigEntry, monkeypatch, socket_enabled,
    enable_custom_integrations,
) -> None:
    """Reload (z.B. nach Options-Aenderung): Sockel neu, Dienste wieder
    da, und der zweite Start ist nicht schlechter als der erste."""
    from custom_components.espeasy_p2p import coordinator as coordinator_modul

    monkeypatch.setattr(coordinator_modul, "detect_local_ip", lambda: "127.0.0.1")

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, "scan")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.services.has_service(DOMAIN, "scan")

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, "scan")
    assert DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]
