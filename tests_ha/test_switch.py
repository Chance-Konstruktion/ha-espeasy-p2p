"""Die Schalter-Plattform: vom Tasknamen zum echten Befehl auf den Draht.

Der Schalter ist der heikelste Teil der Integration: Hier entscheidet
sich, OB ein Tastendruck im Keller wirklich das Relais zieht. Deshalb
werden hier die echten Kandidatenketten geprueft -- welches Kommando
zuerst probiert wird, wann abgebrochen wird, was mit RPiEasys 'False'
passiert -- und zwar ueber den echten switch-Dienst, nicht durch
direkten Methodenaufruf.

HTTP wird von ``aioclient_mock`` beantwortet, UDP vom Aufzeichner
(siehe conftest): Beides sind Grenzen, an denen die Integration
Fremdverhalten vorfindet -- die Logik dazwischen ist echt.
"""

from __future__ import annotations

import asyncio
import time

import aiohttp
import pytest
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import mock_restore_cache

from custom_components.espeasy_p2p.const import NODE_OFFLINE_TIMEOUT
from custom_components.espeasy_p2p.protocol import NodeInfo, TaskConfig, TaskValues
from custom_components.espeasy_p2p.switch import (
    ESPEasyP2PSwitch,
    _looks_like_success,
    _render_template,
)

from conftest import TEST_IP, AufzeichnenderSockel, knoten


def _task(unit: int = 5, name: str = "Keller", idx: int = 0,
          werte: list[str] | None = None) -> TaskConfig:
    return TaskConfig(
        src_unit=unit, task_index=idx, device_number=33, task_name=name,
        value_names=werte if werte is not None else ["State", "", "", ""],
    )


def _werte(unit: int, idx: int, werte: list[float]) -> TaskValues:
    return TaskValues(src_unit=unit, task_index=idx,
                      values=werte + [0.0] * (4 - len(werte)))


def _online(coordinator, unit: int = 5) -> None:
    """Knoten bekannt und frisch gehoert -- sonst ist jede Entitaet
    sofort 'unavailable' (das ist die echte Verfuegbarkeitsregel)."""
    coordinator.nodes[unit] = knoten(unit)
    coordinator.last_seen[unit] = time.monotonic()


async def _schalter_entity_id(hass: HomeAssistant, unit: int, task: int,
                              wert: int) -> str:
    """Die Entitaet kennt der Registry-Eintrag ueber seine feste unique_id
    -- nicht ueber geratene Entity-Ids."""
    ent_reg = er.async_get(hass)
    eintraege = [
        e for e in ent_reg.entities.values()
        if e.unique_id == f"espeasy_p2p_switch_{unit}_{task}_{wert}"
    ]
    assert eintraege, f"Kein Schalter fuer unit={unit} task={task} wert={wert} angelegt"
    return eintraege[0].entity_id


def _befehle(aioclient_mock) -> list[str]:
    """Die tatsaechlich gesendeten /control-Kommandos, in Reihenfolge."""
    return [
        str(call[1].query["cmd"])
        for call in aioclient_mock.mock_calls
        if call[1].path == "/control"
    ]


# ── Wer wird Schalter, wer nicht? ─────────────────────────────────────


@pytest.mark.parametrize(
    "wert_name", ["State", "Output", "Relay", "Switch", "relay", " state "]
)
async def test_schalterwert_namen_fuehren_zu_schaltern(
    integriert, wert_name, hass: HomeAssistant
) -> None:
    """Gross-/Kleinschreibung und Randraum sind keine Ausrede: Der Wert
    heisst im Feld ' state ' und wird trotzdem geschaltet."""
    integriert._on_task(_task(werte=[wert_name, "", "", ""]))
    await hass.async_block_till_done()
    assert await _schalter_entity_id(hass, 5, 0, 0)


@pytest.mark.parametrize("wert_name", ["Temperature", "Humidity", ""])
async def test_sensorwerte_werden_keine_schalter(
    integriert, wert_name, hass: HomeAssistant
) -> None:
    """Werte ohne Schaltername bleiben Sensoren -- kein Phantom-Toggle
    fuer eine Temperatur."""
    integriert._on_task(_task(werte=[wert_name, "", "", ""]))
    await hass.async_block_till_done()
    ent_reg = er.async_get(hass)
    schalter = [
        e for e in ent_reg.entities.values()
        if e.unique_id.startswith("espeasy_p2p_switch_")
    ]
    assert schalter == []


async def test_jeder_wertslot_sein_eigener_schalter(
    integriert, hass: HomeAssistant
) -> None:
    """Ein Task mit State UND Switch liefert zwei Entitaeten -- Wert 0
    und Wert 1 schalten verschiedene Dinge."""
    integriert._on_task(_task(werte=["State", "Switch", "", ""]))
    await hass.async_block_till_done()
    assert await _schalter_entity_id(hass, 5, 0, 0)
    assert await _schalter_entity_id(hass, 5, 0, 1)


async def test_erneute_entdeckung_dupliziert_nicht(
    integriert, hass: HomeAssistant
) -> None:
    integriert._on_task(_task())
    await hass.async_block_till_done()
    integriert._on_task(_task())
    await hass.async_block_till_done()
    ent_reg = er.async_get(hass)
    schalter = [
        e for e in ent_reg.entities.values()
        if e.unique_id.startswith("espeasy_p2p_switch_")
    ]
    assert len(schalter) == 1


# ── Zustand ───────────────────────────────────────────────────────────


async def test_wert_1_heisst_an_und_wert_0_heisst_aus(
    integriert, hass: HomeAssistant
) -> None:
    _online(integriert)
    integriert._on_task(_task())
    await hass.async_block_till_done()
    entity_id = await _schalter_entity_id(hass, 5, 0, 0)

    integriert._on_values(_werte(5, 0, [1.0]))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_ON

    integriert._on_values(_werte(5, 0, [0.0]))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_OFF


async def test_ohne_wert_ist_der_schalter_unbekannt_nicht_aus(
    integriert, hass: HomeAssistant
) -> None:
    """Zustand 'aus' waere eine Behauptung; ohne Wert ist er unbekannt."""
    _online(integriert)
    integriert._on_task(_task())
    await hass.async_block_till_done()
    entity_id = await _schalter_entity_id(hass, 5, 0, 0)
    assert hass.states.get(entity_id).state == "unknown"


async def test_0_5_und_darueber_gilt_an(integriert, hass: HomeAssistant) -> None:
    """Die Schwelle ist 0.5 -- PWM-Zwischenwerte flackern nicht."""
    _online(integriert)
    integriert._on_task(_task())
    await hass.async_block_till_done()
    entity_id = await _schalter_entity_id(hass, 5, 0, 0)

    integriert._on_values(_werte(5, 0, [0.5]))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_ON


async def test_offline_knoten_macht_schalter_unavailable(
    integriert, hass: HomeAssistant
) -> None:
    """Verschollener Knoten: 'nicht verfuegbar', nicht 'aus' -- und nach
    der Wiederverbindung ist der letzte echte Wert wieder da."""
    _online(integriert)
    integriert._on_task(_task())
    await hass.async_block_till_done()
    entity_id = await _schalter_entity_id(hass, 5, 0, 0)
    integriert._on_values(_werte(5, 0, [1.0]))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_ON

    integriert.last_seen[5] = time.monotonic() - (NODE_OFFLINE_TIMEOUT + 1)
    integriert._evaluate_availability()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    integriert._touch(5)  # Knoten meldet sich wieder
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_ON


# ── Schalten: die Kandidatenkette ─────────────────────────────────────


async def test_mit_gelerntem_pin_geht_gpio_zuerst(
    integriert, hass: HomeAssistant, aioclient_mock
) -> None:
    """gpio,<pin>,<state> ist der zuverlaessigste Weg -- er hat Vorfahrt,
    und mit seinem Erfolg ist die Kette zu Ende."""
    _online(integriert)
    integriert._on_task(_task())
    await hass.async_block_till_done()
    entity_id = await _schalter_entity_id(hass, 5, 0, 0)
    integriert.tasks[(5, 0)].gpio_pin = 13
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/control", text="BCM13 set to 1")

    await hass.services.async_call(
        SWITCH_DOMAIN, "turn_on", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert aioclient_mock.call_count == 1
    assert _befehle(aioclient_mock) == ["gpio,13,1"]
    sockel: AufzeichnenderSockel = integriert._transport  # type: ignore[assignment]
    assert sockel.gesendet, "der P2P-Begleiter ist mitgegangen"
    # Erfolg: der lokale Wert wird auf 1 gezogen -- der Schalter steht an,
    # BEVOR der Knoten das naechste Paket schickt.
    assert integriert.values[(5, 0)] == [1.0, 0.0, 0.0, 0.0]
    assert hass.states.get(entity_id).state == STATE_ON


async def test_ohne_pin_wird_taskvaluesetandrun_versucht_dann_taskname(
    integriert, hass: HomeAssistant, aioclient_mock
) -> None:
    """RPiEasy ohne Pin-Angabe: 'False' auf den ersten Kandidaten ist
    KEIN Abbruch -- der Taskname-Kandidat wird noch probiert."""
    _online(integriert)
    integriert._on_task(_task())
    await hass.async_block_till_done()
    entity_id = await _schalter_entity_id(hass, 5, 0, 0)
    url = f"http://{TEST_IP}:80/control"
    # Der Mock-Matcher unterscheidet ueber die Query: der erste Kandidat
    # wird mit 'False' abgewiesen, der zweite gilt als Erfolg.
    aioclient_mock.request("GET", url,
                           params={"cmd": "taskvaluesetandrun,Keller,State,1"},
                           text="False")
    aioclient_mock.request("GET", url, params={"cmd": "Keller,1"}, text="OK")

    await hass.services.async_call(
        SWITCH_DOMAIN, "turn_on", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert _befehle(aioclient_mock) == [
        "taskvaluesetandrun,Keller,State,1",
        "Keller,1",
    ]
    assert integriert.values[(5, 0)] == [1.0, 0.0, 0.0, 0.0]


async def test_eigene_vorlage_hat_alleinige_vorfahrt(
    integriert, hass: HomeAssistant, aioclient_mock
) -> None:
    """Ein Nutzer, der eine Vorlage setzt, hat einen Grund -- kein
    Fallback maskiert ihn."""
    _online(integriert)
    integriert._on_task(_task())
    await hass.async_block_till_done()
    entity_id = await _schalter_entity_id(hass, 5, 0, 0)
    integriert.tasks[(5, 0)].gpio_pin = 13  # wuerde ohne Vorlage gewinnen
    integriert.set_command_override(5, "Keller", "event,keller={state}")
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/control", text="OK")

    await hass.services.async_call(
        SWITCH_DOMAIN, "turn_on", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert _befehle(aioclient_mock) == ["event,keller=1"]


async def test_vorlage_mit_gross_platzhalter(
    integriert, hass: HomeAssistant, aioclient_mock
) -> None:
    _online(integriert)
    integriert._on_task(_task())
    await hass.async_block_till_done()
    entity_id = await _schalter_entity_id(hass, 5, 0, 0)
    integriert.set_command_override(5, "Keller", "GROSS{STATE}")
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/control", text="OK")

    await hass.services.async_call(
        SWITCH_DOMAIN, "turn_off", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    assert _befehle(aioclient_mock) == ["GROSS0"]


async def test_gescheiterte_kandidaten_lassen_den_zustand_stehen(
    integriert, hass: HomeAssistant, aioclient_mock, caplog
) -> None:
    """Alle Kandidaten abgelehnt ('False' ist RPiEasys Nein): Der
    Schalter springt NICHT lokal -- sonst luegt die Oberflaeche."""
    _online(integriert)
    integriert._on_task(_task())
    await hass.async_block_till_done()
    entity_id = await _schalter_entity_id(hass, 5, 0, 0)
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/control", text="False")
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/control", text="False")

    await hass.services.async_call(
        SWITCH_DOMAIN, "turn_on", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert (5, 0) not in integriert.values
    assert hass.states.get(entity_id).state == "unknown"
    assert "no known GPIO pin" in caplog.text


async def test_http_fehler_ist_auch_kein_erfolg(
    integriert, hass: HomeAssistant, aioclient_mock
) -> None:
    _online(integriert)
    integriert._on_task(_task())
    await hass.async_block_till_done()
    entity_id = await _schalter_entity_id(hass, 5, 0, 0)
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/control", exc=aiohttp.ClientError())

    await hass.services.async_call(
        SWITCH_DOMAIN, "turn_on", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    assert (5, 0) not in integriert.values


async def test_ohne_knoteninformation_wird_nicht_geschaltet(
    integriert, hass: HomeAssistant, aioclient_mock
) -> None:
    """Keine IP => nichts senden, keine halben Versuche."""
    _online(integriert)
    integriert._on_task(_task())
    await hass.async_block_till_done()
    entity_id = await _schalter_entity_id(hass, 5, 0, 0)
    integriert.nodes[5] = NodeInfo(unit=5, name="Keller", ip="0.0.0.0", mac="",
                                   build=1, node_type=1, web_port=80)

    await hass.services.async_call(
        SWITCH_DOMAIN, "turn_on", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    assert aioclient_mock.call_count == 0
    sockel: AufzeichnenderSockel = integriert._transport  # type: ignore[assignment]
    assert sockel.gesendet == []


async def test_einschalten_loest_gezielten_resync_aus(
    integriert, hass: HomeAssistant, aioclient_mock
) -> None:
    """Ein Relaistimer kann abschalten, ohne zu funken -- nach dem
    Einschalten wird genau dieser Knoten nachgefragt, kein anderer."""
    _online(integriert)
    integriert._on_task(_task())
    await hass.async_block_till_done()
    entity_id = await _schalter_entity_id(hass, 5, 0, 0)
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/control", text="OK")
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/json", json={"Sensors": []})

    await hass.services.async_call(
        SWITCH_DOMAIN, "turn_on", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    # Der erste Burst-Schlag kommt nach 3 s Schlaf -- dorthin pollieren,
    # statt die vollen 31 s des Bursts zu warten.
    for _ in range(120):
        if any(c[1].path == "/json" for c in aioclient_mock.mock_calls):
            break
        await asyncio.sleep(0.05)

    jsons = [c for c in aioclient_mock.mock_calls if c[1].path == "/json"]
    assert jsons, "nach dem Einschalten wurde /json nachgefragt"
    fremde = [c for c in aioclient_mock.mock_calls if c[1].host != TEST_IP]
    assert fremde == [], "der Resync ist gezielt -- nur dieser Knoten"


# ── Wiederherstellung nach Neustart ───────────────────────────────────


async def test_letzter_zustand_wird_wiederhergestellt(
    hass: HomeAssistant, entry, koordinator
) -> None:
    """Nach einem HA-Neustart zeigt der Schalter den letzten Zustand --
    bis der Knoten selbst wieder funkt."""
    mock_restore_cache(hass, (State("switch.keller", "on"),))
    entitaet = ESPEasyP2PSwitch(koordinator, entry.entry_id, 5, 0, 0)
    entitaet.hass = hass
    entitaet.entity_id = "switch.keller"
    await entitaet.async_added_to_hass()

    assert koordinator.values[(5, 0)] == [1.0, 0.0, 0.0, 0.0]


async def test_live_wert_schlaegt_wiederherstellung(
    hass: HomeAssistant, entry, koordinator
) -> None:
    """Der Koordinator hat schon einen echten Wert empfangen -- dann
    wird der nicht von der Wiederherstellung ueberschrieben."""
    koordinator.values[(5, 0)] = [0.0, 0.0, 0.0, 0.0]
    mock_restore_cache(hass, (State("switch.keller", "on"),))
    entitaet = ESPEasyP2PSwitch(koordinator, entry.entry_id, 5, 0, 0)
    entitaet.hass = hass
    entitaet.entity_id = "switch.keller"
    await entitaet.async_added_to_hass()

    assert koordinator.values[(5, 0)] == [0.0, 0.0, 0.0, 0.0]


async def test_restore_unterstuetzt_nur_an_aus(
    hass: HomeAssistant, entry, koordinator
) -> None:
    """'unavailable' oder 'unknown' im Speicher sind keine Zustände zum
    Wiederherstellen."""
    mock_restore_cache(hass, (State("switch.keller", STATE_UNAVAILABLE),))
    entitaet = ESPEasyP2PSwitch(koordinator, entry.entry_id, 5, 0, 0)
    entitaet.hass = hass
    entitaet.entity_id = "switch.keller"
    await entitaet.async_added_to_hass()

    assert (5, 0) not in koordinator.values


# ── Die kleinen Helfer, direkt ────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "erwartet"),
    [
        ("", False),
        ("False", False),
        ("false\n", False),
        ("0", False),
        ("Unknown command", False),
        ("OK", True),
        ("BCM13 set to 1", True),
        ('{"log":"ok"}', True),
    ],
)
def test_das_ist_ein_erfolg(text, erwartet):
    assert _looks_like_success(text) is erwartet


def test_vorlagen_rendering_laesst_fremde_klammern_stehen():
    assert _render_template("gpio,{state}", 1) == "gpio,1"
    assert _render_template("{STATE}", 0) == "0"
    assert _render_template('json {"a":1}', 1) == 'json {"a":1}'


# ── Randfaelle direkt an der Entitaet ─────────────────────────────────


async def test_abgelehnter_vorlage_wird_ausdruecklich_gemeldet(
    hass: HomeAssistant, entry, koordinator, aioclient_mock, caplog
) -> None:
    """Eigene Vorlage, Knoten sagt Nein: Die Warnung nennt die Vorlage
    und zeigt auf die Optionen -- das ist der Weg, wie der Nutzer erfährt,
    dass sein Kommando nicht ankam."""
    koordinator.nodes[5] = knoten(5)
    koordinator.last_seen[5] = time.monotonic()
    koordinator.tasks[(5, 0)] = _task()
    koordinator.set_command_override(5, "Keller", "event,keller={state}")
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/control", text="False")

    entitaet = ESPEasyP2PSwitch(koordinator, entry.entry_id, 5, 0, 0)
    entitaet.hass = hass
    entitaet.entity_id = "switch.keller"

    await entitaet._send_command(1)

    assert "custom command template" in caplog.text
    assert "event,keller={state}" in caplog.text
    assert (5, 0) not in koordinator.values


async def test_kurzer_wertpuffer_wird_beim_schalten_aufgefuellt(
    hass: HomeAssistant, entry, koordinator, aioclient_mock
) -> None:
    """Der Zustandspuffer kommt vom Draht und hat immer 4 Slots -- aber
    defensiv muss auch ein gekuerzter Puffer das Schalten ueberleben."""
    koordinator.nodes[5] = knoten(5)
    koordinator.last_seen[5] = time.monotonic()
    koordinator.tasks[(5, 0)] = _task(werte=["State", "Switch", "", ""])
    koordinator.values[(5, 0)] = [1.0]  # bewusst zu kurz
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/control", text="OK")

    entitaet = ESPEasyP2PSwitch(koordinator, entry.entry_id, 5, 0, 2)
    entitaet.hass = hass
    entitaet.entity_id = "switch.slot2"

    await entitaet._send_command(1)

    # Der Schalt-Pfad polstert nur bis zum benoetigten Slot auf (der
    # /json-Pfad haelt den Puffer immer 4 lang); entscheidend ist, dass
    # Slot 2 sauber beschrieben wurde und Slots davor unangetastet sind.
    assert koordinator.values[(5, 0)] == [1.0, 0.0, 1.0]


def test_schalter_ohne_aufgabe_heisst_nach_slot(
    hass: HomeAssistant, entry, koordinator
) -> None:
    entitaet = ESPEasyP2PSwitch(koordinator, entry.entry_id, 5, 0, 0)
    assert entitaet.name == "Task 0"


async def test_fremde_signale_bewegen_den_schalter_nicht(
    integriert, hass: HomeAssistant
) -> None:
    """Werte von unit 6 / task 1 duerfen den Keller-Schalter nicht
    anfassen -- auch nicht mit einem Fehler."""
    _online(integriert)
    integriert._on_task(_task())
    await hass.async_block_till_done()
    entity_id = await _schalter_entity_id(hass, 5, 0, 0)
    integriert._on_values(_werte(5, 0, [1.0]))
    await hass.async_block_till_done()
    vorher = hass.states.get(entity_id).state

    fremd = TaskValues(src_unit=6, task_index=0, values=[0.0, 0, 0, 0])
    integriert._on_values(fremd)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == vorher
