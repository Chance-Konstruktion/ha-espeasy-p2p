"""Der Koordinator: Zustand, Entdeckung, Verbindungsleben, Befehle.

Der Koordinator ist die Schaltzentrale: Er entscheidet, wer im Netz
existiert, wann eine Entitaet verfuegbar ist, wann ein Knoten als
verschollen gilt und was bei einem Schaltbefehl tatsaechlich auf den
Draht geht.

Alle Tests laufen gegen ein echtes Home Assistant (echtes Device-Registry,
echter Dispatcher, echte Registries); der UDP-Sockel ist ein Aufzeichner,
damit kein Testpaket das Haus verlaesst. Das /json-HTTP beantwortet
``aioclient_mock`` -- der in HA-Suiten uebliche Weg, echte aiohttp-Calls
kontrolliert zu beantworten.
"""

from __future__ import annotations

import asyncio
import socket
import struct
import time

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.espeasy_p2p.const import (
    DOMAIN,
    BROADCAST_UNIT,
    NODE_OFFLINE_TIMEOUT,
    SIGNAL_NODE_AVAILABILITY,
    SIGNAL_NODE_DISCOVERED,
    SIGNAL_NODE_REMOVED,
    SIGNAL_TASK_DISCOVERED,
    SIGNAL_VALUE_UPDATED,
)
from custom_components.espeasy_p2p import coordinator as coordinator_modul
from custom_components.espeasy_p2p.coordinator import ESPEasyP2PCoordinator
from custom_components.espeasy_p2p.protocol import (
    PACKET_HEADER,
    PACKET_TYPE_INFO,
    PACKET_TYPE_SENSOR_DATA,
    TaskConfig,
    TaskValues,
    build_info_packet,
)
from custom_components.espeasy_p2p.registry import device_for_unit

from conftest import (
    TEST_IP,
    AufzeichnenderSockel,
    freier_udp_port,
    knoten,
    signal_sammler,
)


def task_config(unit: int = 5, idx: int = 0, name: str = "Relais",
                werte: list[str] | None = None) -> TaskConfig:
    return TaskConfig(
        src_unit=unit,
        task_index=idx,
        device_number=33,
        task_name=name,
        value_names=werte if werte is not None else ["State", "", "", ""],
    )


# ── Entdeckung: wer existiert im Netz? ────────────────────────────────


async def test_erste_entdeckung_feuert_signal_und_unicast_hallo(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator, aioclient_mock
) -> None:
    """Der erste Typ-1 macht den Knoten bekannt, feuert das Signal und
    antwortet mit einem Unicast-Hallo, damit der Knoten seine Aufgaben
    nachsendet. Das /json-Nachfassen wird nebenbei angestoessen."""
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/json", exc=aiohttp.ClientError())
    entdeckt = signal_sammler(hass, koordinator, SIGNAL_NODE_DISCOVERED)

    koordinator._on_node(knoten(5))
    await hass.async_block_till_done()

    assert 5 in koordinator.nodes
    assert len(entdeckt) == 1
    assert entdeckt[0].unit == 5
    sockel: AufzeichnenderSockel = koordinator._transport  # type: ignore[assignment]
    assert len(sockel.zielte_auf(TEST_IP)) == 1  # das Hallo


async def test_wiederholtes_lebenszeichen_ist_kein_neuer_knoten(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator, aioclient_mock
) -> None:
    """Alle 30 s kommt Typ 1 vom selben Knoten -- Entdeckungssignal und
    Hallo-Unicast nur beim ersten Mal."""
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/json", exc=aiohttp.ClientError())
    entdeckt = signal_sammler(hass, koordinator, SIGNAL_NODE_DISCOVERED)

    koordinator._on_node(knoten(5))
    await hass.async_block_till_done()
    koordinator._on_node(knoten(5, name="Keller neu"))
    await hass.async_block_till_done()

    assert len(entdeckt) == 1
    # Aber der Name wurde aufgefrischt:
    assert koordinator.nodes[5].name == "Keller neu"





def test_benannter_task_meldet_neue_slots_und_umbenennungen(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    updates = signal_sammler(hass, koordinator, SIGNAL_TASK_DISCOVERED)
    koordinator._on_task(task_config(name="Klima", werte=["Temperature", "", "", ""]))
    koordinator._on_task(
        task_config(name="Klima", werte=["Temperature", "Humidity", "", ""])
    )
    koordinator._on_task(
        task_config(name="Raumklima", werte=["Temperature", "Humidity", "", ""])
    )

    assert [task.task_name for task in updates] == ["Klima", "Klima", "Raumklima"]
    assert updates[1].value_names[1] == "Humidity"


def test_c013_taskpakete_loeschen_keine_json_metadaten(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    json_task = TaskConfig(
        src_unit=5, task_index=0, device_number=0,
        task_name="Relais", value_names=["State", "", "", ""],
        plugin_type="Output - GPIO", enabled=False, gpio_pin=26,
    )
    koordinator._on_task(json_task)
    updates = signal_sammler(hass, koordinator, SIGNAL_TASK_DISCOVERED)

    # Type-3 carries names and a plugin number, but not these HTTP fields.
    c013_task = task_config(name="Relais", werte=["State", "", "", ""])
    koordinator._on_task(c013_task)
    merged = koordinator.tasks[(5, 0)]
    assert merged.device_number == 33
    assert merged.plugin_type == "Output - GPIO"
    assert merged.enabled is False
    assert merged.gpio_pin == 26

    # Identical Type-3 packets are not UI updates.
    koordinator._on_task(task_config(name="Relais", werte=["State", "", "", ""]))
    assert updates == []

    # A later HTTP snapshot can update its own fields without losing the ID.
    koordinator._on_task(
        TaskConfig(
            src_unit=5, task_index=0, device_number=0,
            task_name="Relais", value_names=["State", "", "", ""],
            plugin_type="Output - GPIO", enabled=True, gpio_pin=13,
        )
    )
    assert koordinator.tasks[(5, 0)].device_number == 33
    assert koordinator.tasks[(5, 0)].enabled is True
    assert koordinator.tasks[(5, 0)].gpio_pin == 13
    assert len(updates) == 1

def test_eigenes_echo_wird_ignoriert(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    """Der Broadcast des Scans kommt bei uns selbst wieder an -- der
    eigene Peer (gleiche Einheit UND gleicher Name) ist kein Knoten."""
    koordinator._on_node(knoten(250, name="Home Assistant"))
    assert koordinator.nodes == {}
    # Ein anderer Name auf derselben Einheit ist trotzdem ein Knoten:
    koordinator._on_node(knoten(250, name="Fremdgeraet"))
    assert 250 in koordinator.nodes


async def test_knoten_ohne_ip_bekommt_keinen_unicast(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    koordinator._on_node(knoten(5, ip="0.0.0.0"))
    sockel: AufzeichnenderSockel = koordinator._transport  # type: ignore[assignment]
    assert sockel.gesendet == []


async def test_device_registry_eintrag_wird_gefuehrt(
    hass: HomeAssistant, entry: MockConfigEntry,
    koordinator: ESPEasyP2PCoordinator, aioclient_mock,
) -> None:
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/json", exc=aiohttp.ClientError())
    koordinator._on_node(knoten(5, mac="aa:bb:cc:dd:ee:0f"))
    koordinator._on_node(knoten(6, mac="00:00:00:00:00:00"))
    await hass.async_block_till_done()

    gerät5 = device_for_unit(hass, 5)
    gerät6 = device_for_unit(hass, 6)
    assert gerät5 is not None and gerät6 is not None
    assert gerät5.name == "Keller"
    assert gerät5.model == "RPiEasy"
    assert ("mac", "aa:bb:cc:dd:ee:0f") in gerät5.connections
    assert gerät6.connections == set()
    assert gerät5.configuration_url == f"http://{TEST_IP}:80"


async def test_knoten_ohne_ip_bekommt_keine_konfigurations_url(
    hass: HomeAssistant, entry: MockConfigEntry,
    koordinator: ESPEasyP2PCoordinator, aioclient_mock,
) -> None:
    aioclient_mock.request("GET", "http://0.0.0.0:80/json", exc=aiohttp.ClientError())
    koordinator._on_node(knoten(5, ip="0.0.0.0"))
    await hass.async_block_till_done()
    gerät = device_for_unit(hass, 5)
    assert gerät.configuration_url is None


async def test_registry_fehler_zerreisst_die_entdeckung_nicht(
    hass: HomeAssistant, entry: MockConfigEntry,
    koordinator: ESPEasyP2PCoordinator, aioclient_mock, caplog,
) -> None:
    """RPiEasy hat hier schon Ausnahmen geworfen: Der Registry-Griff ist
    eingewickelt, Entdeckung und Signal laufen trotzdem."""
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/json", exc=aiohttp.ClientError())

    def explodiert(node):
        raise RuntimeError("Registry kaputt")

    entdeckt = signal_sammler(hass, koordinator, SIGNAL_NODE_DISCOVERED)
    koordinator._update_device_registry = explodiert  # type: ignore[method-assign]

    koordinator._on_node(knoten(5))
    await hass.async_block_till_done()

    assert 5 in koordinator.nodes
    assert len(entdeckt) == 1
    assert "Failed to update HA device entry" in caplog.text


# ── Werte und der Broadcast-Sonderfall ────────────────────────────────


def test_werte_von_bekannter_einheit_landen_im_zustand(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    empfangen = signal_sammler(hass, koordinator, SIGNAL_VALUE_UPDATED)

    koordinator._on_values(_werte(5, 0, [1.0]))

    assert koordinator.values[(5, 0)] == [1.0, 0.0, 0.0, 0.0]
    assert len(empfangen) == 1
    assert empfangen[0].src_unit == 5
    # Ohne bekannte Aufgabe entsteht ein Platzhalter (ohne Namen!), damit
    # keine Phantomentitaeten entstehen:
    assert koordinator.tasks[(5, 0)].task_name == ""
    assert koordinator.tasks[(5, 0)].value_names == ["", "", "", ""]


def _werte(unit: int, idx: int, werte: list[float], ip: str = "") -> TaskValues:
    return TaskValues(
        src_unit=unit, task_index=idx, values=werte + [0.0] * (4 - len(werte)),
        src_ip=ip,
    )


def test_broadcast_255_wird_ueber_die_absender_ip_aufgeloest(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    """Manche Firmware sendet Werte mit Quell-Einheit 255. Der echte
    Absender steht nur in der UDP-Quelladresse -- bekannt => aufgeloest."""
    koordinator._on_node(knoten(5, ip=TEST_IP))
    empfangen = signal_sammler(hass, koordinator, SIGNAL_VALUE_UPDATED)

    koordinator._on_values(_werte(BROADCAST_UNIT, 0, [2.0], ip=TEST_IP))

    assert len(empfangen) == 1
    assert empfangen[0].src_unit == 5, "Nachgesehene bekommen die echte Einheit"
    assert koordinator.values[(5, 0)] == [2.0, 0.0, 0.0, 0.0]


def test_broadcast_unbekannter_absender_wird_probe_angeschoben_und_verworfen(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    """Unbekannter Broadcast: Werte nicht raten, sondern den Absender
    anfunken (Scan-Unicast), damit er sich zu erkennen gibt."""
    empfangen = signal_sammler(hass, koordinator, SIGNAL_VALUE_UPDATED)

    koordinator._on_values(_werte(BROADCAST_UNIT, 0, [9.0], ip="192.0.2.77"))

    assert empfangen == []
    assert (BROADCAST_UNIT, 0) not in koordinator.values
    sockel: AufzeichnenderSockel = koordinator._transport  # type: ignore[assignment]
    assert len(sockel.zielte_auf("192.0.2.77")) == 1


def test_einheit_0_wird_wie_broadcast_behandelt(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    empfangen = signal_sammler(hass, koordinator, SIGNAL_VALUE_UPDATED)
    koordinator._on_values(_werte(0, 0, [1.0], ip="192.0.2.99"))
    assert empfangen == []


# ── Aufgaben: Platzhalter und echte Namen ─────────────────────────────


def test_platzhalter_dann_echte_namen_feuern_genau_einmal(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    """Erst kommen Werte (Platzhalter), dann die Namen (/json oder Typ 3):
    Genau dieser Uebergang ist die Entitaet-Entdeckung -- nicht doppelt,
    nicht nie."""
    gefunden = signal_sammler(hass, koordinator, SIGNAL_TASK_DISCOVERED)

    koordinator._on_values(_werte(5, 0, [1.0]))
    koordinator._on_task(task_config(unit=5, name="Relais", werte=["State", "", "", ""]))
    koordinator._on_task(task_config(unit=5, name="Relais", werte=["State", "", "", ""]))

    assert len(gefunden) == 1


def test_namenlose_aufgabe_feuert_nicht(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    gefunden = signal_sammler(hass, koordinator, SIGNAL_TASK_DISCOVERED)
    koordinator._on_task(task_config(unit=5, name="", werte=["", "", "", ""]))
    assert gefunden == []


# ── Verfuegbarkeit und Wiederverbindung ────────────────────────────────


def test_unbekannte_einheit_ist_offline(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    assert koordinator.is_unit_online(5) is False


def test_stiller_knoten_faellt_ins_offline_mit_signal(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    koordinator._on_node(knoten(5, ip="0.0.0.0"))
    veraendert = signal_sammler(hass, koordinator, SIGNAL_NODE_AVAILABILITY)
    assert koordinator.is_unit_online(5) is True

    koordinator.last_seen[5] = time.monotonic() - (NODE_OFFLINE_TIMEOUT + 1)
    koordinator._evaluate_availability()

    assert koordinator.is_unit_online(5) is False
    assert veraendert == [5]


def test_wiederverbindung_feuert_rueckkehr_signal(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    """Der Strom war weg, der Knoten kommt wieder: Das Rueckkehr-Signal
    ist es, das die Entitaeten aus 'nicht verfuegbar' holt."""
    veraendert = signal_sammler(hass, koordinator, SIGNAL_NODE_AVAILABILITY)
    koordinator.last_seen[5] = time.monotonic() - (NODE_OFFLINE_TIMEOUT + 1)
    koordinator._evaluate_availability()
    assert koordinator.is_unit_online(5) is False
    assert veraendert == [5]

    koordinator._touch(5)  # das naechste Lebenszeichen

    assert koordinator.is_unit_online(5) is True
    assert veraendert == [5, 5]


def test_wertpaket_toucht_die_einheit(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    koordinator.last_seen[5] = time.monotonic() - (NODE_OFFLINE_TIMEOUT + 1)
    koordinator._evaluate_availability()
    assert koordinator.is_unit_online(5) is False

    koordinator._on_values(_werte(5, 0, [1.0]))
    assert koordinator.is_unit_online(5) is True


def test_aging_bleibt_stabil_ohne_weitere_aenderung(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    """Kein Taktprobieren: Mehrere Durchlaeufe ohne Zustandswechsel
    feuern nichts -- die Signale tragen Uebergange, keinen Takt."""
    veraendert = signal_sammler(hass, koordinator, SIGNAL_NODE_AVAILABILITY)
    koordinator.last_seen[5] = time.monotonic() - (NODE_OFFLINE_TIMEOUT + 1)
    koordinator._evaluate_availability()
    koordinator._evaluate_availability()
    koordinator._evaluate_availability()
    assert veraendert == [5]


# ── /json: Namen und Werte nachlernen ─────────────────────────────────


def _sensors_nutzlast(sensors: list[dict]) -> dict:
    return {"Sensors": sensors}


async def test_json_lernt_namen_und_synchronisiert_werte(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator, aioclient_mock
) -> None:
    """TaskNumber ist 1-basiert im JSON, 0-basiert auf dem Draht --
    genau dieser Versatz wird hier festgehalten."""
    aioclient_mock.request(
        "GET",
        f"http://{TEST_IP}:80/json",
        json=_sensors_nutzlast(
            [
                {
                    "TaskNumber": 1,
                    "TaskName": "Temperatur",
                    "TaskEnabled": True,
                    "Type": "Environment - DS18b20",
                    "TaskDeviceGPIO1": 13,
                    "TaskValues": [
                        {"Name": "Temperature", "Value": 21.5},
                        {"Name": "", "Value": 0},
                    ],
                }
            ]
        ),
    )
    wert_signale = signal_sammler(hass, koordinator, SIGNAL_VALUE_UPDATED)
    koordinator.nodes[5] = knoten(5)
    koordinator._meta_inflight.add(TEST_IP)

    await koordinator._fetch_node_metadata_safe(koordinator.nodes[5])

    assert (5, 0) in koordinator.tasks
    aufgabe = koordinator.tasks[(5, 0)]
    assert aufgabe.task_name == "Temperatur"
    assert aufgabe.value_names == ["Temperature", "", "", ""]
    assert aufgabe.gpio_pin == 13
    assert aufgabe.plugin_type == "Environment - DS18b20"
    assert koordinator.values[(5, 0)] == [21.5, 0.0, 0.0, 0.0]
    assert len(wert_signale) == 1
    assert koordinator._meta_inflight == set(), "In-Flight-Marke immer raeumen"





async def test_json_task_mit_ungueltiger_tasknummer_blockiert_nicht_die_restlichen(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator, aioclient_mock
) -> None:
    aioclient_mock.request(
        "GET",
        f"http://{TEST_IP}:80/json",
        json=_sensors_nutzlast(
            [
                None,
                {"TaskNumber": "not-a-number", "TaskName": "Kaputt",
                 "TaskValues": [{"Name": "V", "Value": 1}]},
                {"TaskNumber": 1, "TaskName": "Valide",
                 "TaskValues": [None, {"Name": "Temperature", "Value": 21.5}]},
            ]
        ),
    )
    koordinator.nodes[5] = knoten(5)

    await koordinator._fetch_node_metadata_safe(koordinator.nodes[5])

    assert (5, 0) in koordinator.tasks
    assert koordinator.tasks[(5, 0)].task_name == "Valide"
    assert koordinator.tasks[(5, 0)].value_names == ["", "Temperature", "", ""]
    assert koordinator.values[(5, 0)] == [0.0, 21.5, 0.0, 0.0]


async def test_veraltete_json_antwort_stellt_entfernten_knoten_nicht_wieder_her(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator, monkeypatch
) -> None:
    node = knoten(5)

    class DelayedResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def json(self, content_type=None):
            # Simulate deletion while the HTTP response is still in flight.
            koordinator.forget_node_state(5)
            return _sensors_nutzlast(
                [{"TaskNumber": 1, "TaskName": "Alt",
                  "TaskValues": [{"Name": "Temperature", "Value": 19.0}]}]
            )

    class FakeSession:
        def get(self, _url, **_kwargs):
            return DelayedResponse()

    monkeypatch.setattr(
        coordinator_modul, "async_get_clientsession", lambda _hass: FakeSession()
    )
    koordinator.nodes[5] = node

    await koordinator._fetch_node_metadata_safe(node)

    assert 5 not in koordinator.nodes
    assert koordinator.tasks == {}
    assert koordinator.values == {}
    assert koordinator.last_seen == {}


async def test_json_ohne_aenderung_feuert_kein_wertsignal(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator, aioclient_mock
) -> None:
    nutzlast = _sensors_nutzlast(
        [{"TaskNumber": 2, "TaskName": "T", "TaskValues": [{"Name": "V", "Value": 7}]}]
    )
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/json", json=nutzlast)
    koordinator.nodes[5] = knoten(5)
    wert_signale = signal_sammler(hass, koordinator, SIGNAL_VALUE_UPDATED)

    koordinator._meta_inflight.add(TEST_IP)
    await koordinator._fetch_node_metadata_safe(koordinator.nodes[5])
    koordinator._meta_inflight.add(TEST_IP)
    await koordinator._fetch_node_metadata_safe(koordinator.nodes[5])

    assert koordinator.values[(5, 1)] == [7.0, 0.0, 0.0, 0.0]
    assert len(wert_signale) == 1, "gleiche Werte: keine Updates, kein Flackern"


async def test_json_deaktivierte_aufgabe_wird_uebersprungen(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator, aioclient_mock
) -> None:
    aioclient_mock.request(
        "GET",
        f"http://{TEST_IP}:80/json",
        json=_sensors_nutzlast(
            [
                {"TaskNumber": 1, "TaskName": "Aus", "TaskEnabled": False,
                 "TaskValues": [{"Name": "V", "Value": 1}]},
                {"TaskNumber": 2, "TaskName": "AusAlsText", "TaskEnabled": "false",
                 "TaskValues": [{"Name": "V", "Value": 1}]},
                {"TaskNumber": 3, "TaskName": "AnAlsText", "TaskEnabled": "true",
                 "TaskValues": [{"Name": "V", "Value": 2}]},
            ]
        ),
    )
    koordinator.nodes[5] = knoten(5)
    koordinator._meta_inflight.add(TEST_IP)
    await koordinator._fetch_node_metadata_safe(koordinator.nodes[5])

    assert (5, 0) not in koordinator.tasks
    assert (5, 1) not in koordinator.tasks, "'false' als Text ist auch aus"
    assert (5, 2) in koordinator.tasks, "'true' als Text ist an"


async def test_json_gpio_feldkette_und_kaputte_angaben(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator, aioclient_mock
) -> None:
    """Die Kette TaskDeviceGPIO1 → GPIO1 → Pin → TaskDeviceGPIO, und was
    mit 'abc' und negativen Zahlen passiert."""
    aioclient_mock.request(
        "GET",
        f"http://{TEST_IP}:80/json",
        json=_sensors_nutzlast(
            [
                {"TaskNumber": 1, "TaskName": "A", "GPIO1": 4,
                 "TaskValues": [{"Name": "V", "Value": 1}]},
                {"TaskNumber": 2, "TaskName": "B", "TaskDeviceGPIO1": "abc",
                 "TaskValues": [{"Name": "V", "Value": 1}]},
                {"TaskNumber": 3, "TaskName": "C", "TaskDeviceGPIO1": -2,
                 "TaskValues": [{"Name": "V", "Value": 1}]},
                {"TaskNumber": 4, "TaskName": "D",
                 "TaskValues": [{"Name": "V", "Value": 1}]},
            ]
        ),
    )
    koordinator.nodes[5] = knoten(5)
    koordinator._meta_inflight.add(TEST_IP)
    await koordinator._fetch_node_metadata_safe(koordinator.nodes[5])

    assert koordinator.tasks[(5, 0)].gpio_pin == 4
    assert koordinator.tasks[(5, 1)].gpio_pin is None, "Text ist kein Pin"
    assert koordinator.tasks[(5, 2)].gpio_pin is None, "negativ ist kein Pin"
    assert koordinator.tasks[(5, 3)].gpio_pin is None


async def test_json_pin_override_greift_wenn_firmware_schweigt(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator, aioclient_mock
) -> None:
    """RPiEasy exponiert den Pin nicht -- der Nutzer-Override uebernimmt."""
    aioclient_mock.request(
        "GET",
        f"http://{TEST_IP}:80/json",
        json=_sensors_nutzlast(
            [{"TaskNumber": 1, "TaskName": "Relais",
              "TaskValues": [{"Name": "State", "Value": 0}]}]
        ),
    )
    koordinator.nodes[5] = knoten(5)
    koordinator.pin_overrides["5/Relais"] = 26
    koordinator._meta_inflight.add(TEST_IP)
    await koordinator._fetch_node_metadata_safe(koordinator.nodes[5])

    assert koordinator.tasks[(5, 0)].gpio_pin == 26


@pytest.mark.parametrize(
    "antwort",
    [
        {"status": 500},
        {"exc": aiohttp.ClientError()},
        {"text": "kein json", "status": 200},
        {"json": {"Kein": "Sensor"}, "status": 200},
    ],
)
async def test_json_fehlerpfade_lernen_nichts(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator, aioclient_mock, antwort
) -> None:
    """500, Verbindungsfehler, HTML statt JSON, leere Sensorliste: alles
    nichts gelernt, In-Flight-Marke trotzdem geraeumt -- der naechste
    Herzschlag darf es nochmal versuchen."""
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/json", **antwort)
    koordinator.nodes[5] = knoten(5)
    koordinator._meta_inflight.add(TEST_IP)

    await koordinator._fetch_node_metadata_safe(koordinator.nodes[5])

    assert koordinator.tasks == {}
    assert koordinator.values == {}
    assert koordinator._meta_inflight == set()


async def test_json_kontakt_zaehlt_als_lebenszeichen(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator, aioclient_mock
) -> None:
    """RPiEasy funkt nach einem Stromausfall spaet -- HTTP-Erreichbarkeit
    allein muss die Entitaeten verfuegbar machen."""
    aioclient_mock.request(
        "GET", f"http://{TEST_IP}:80/json",
        json=_sensors_nutzlast([{"TaskNumber": 1, "TaskName": "T",
                                 "TaskValues": [{"Name": "V", "Value": 1}]}]),
    )
    koordinator.nodes[5] = knoten(5)
    koordinator.last_seen[5] = time.monotonic() - (NODE_OFFLINE_TIMEOUT + 1)
    koordinator._evaluate_availability()
    assert koordinator.is_unit_online(5) is False

    koordinator._meta_inflight.add(TEST_IP)
    await koordinator._fetch_node_metadata_safe(koordinator.nodes[5])

    assert koordinator.is_unit_online(5) is True


async def test_entdeckung_fasst_nach_bis_namen_da_sind(
    hass: HomeAssistant, entry: MockConfigEntry,
    koordinator: ESPEasyP2PCoordinator, aioclient_mock,
) -> None:
    """Der erste /json-Treffer nach einem Neustart ist oft leer. Der
    Koordinator darf nicht 'einmal probiert, nie wieder' denken: Bei jedem
    Herzschlag wird nachgefasst, bis mindestens ein benannter Task da ist."""
    # Erster Herzschlag: leerer JSON-Koerper.
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/json", json={"Sensors": []})
    koordinator._on_node(knoten(5))
    await hass.async_block_till_done()
    assert koordinator.tasks == {}

    # Zweiter Herzschlag: diesmal liefert der Knoten alles.
    aioclient_mock.clear_requests()
    aioclient_mock.request(
        "GET", f"http://{TEST_IP}:80/json",
        json=_sensors_nutzlast([{"TaskNumber": 1, "TaskName": "Relais",
                                 "TaskValues": [{"Name": "State", "Value": 0}]}]),
    )
    koordinator._on_node(knoten(5))
    await hass.async_block_till_done()

    assert (5, 0) in koordinator.tasks
    assert koordinator.tasks[(5, 0)].task_name == "Relais"


# ── Befehle auf den Draht ─────────────────────────────────────────────


def test_p2p_ohne_sockel_geht_nicht(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    c = ESPEasyP2PCoordinator(hass, entry.entry_id, 8266, 250, "HA")
    assert c.send_p2p_command(TEST_IP, "gpio,1,1") is False


def test_p2p_baut_ein_typ0_paket(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    assert koordinator.send_p2p_command(TEST_IP, "gpio,13,1") is True
    sockel: AufzeichnenderSockel = koordinator._transport  # type: ignore[assignment]
    gesendet = sockel.zielte_auf(TEST_IP)
    assert len(gesendet) == 1
    assert gesendet[0][0:2] == bytes([PACKET_HEADER, 0])
    assert gesendet[0][2:].rstrip(b"\x00") == b"gpio,13,1"


def test_p2p_sockelfehler_ist_false_nicht_absturz(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    koordinator._transport.fehler_bei = TEST_IP  # type: ignore[union-attr]
    assert koordinator.send_p2p_command(TEST_IP, "gpio,1,1") is False


def test_p2p_an_null_ip_geht_nicht(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    assert koordinator.send_p2p_command("0.0.0.0", "gpio,1,1") is False
    assert koordinator.send_p2p_command("", "gpio,1,1") is False


async def test_rohbefehl_unbekannte_einheit(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    ergebnis = await koordinator.async_send_raw_command(99, "restart")
    assert ergebnis == {"ok": False, "reason": "unknown_unit"}


async def test_rohbefehl_geht_ueber_udp_und_http(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator, aioclient_mock
) -> None:
    koordinator.nodes[5] = knoten(5)
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/control", text="OK")

    ergebnis = await koordinator.async_send_raw_command(5, "restart")

    assert ergebnis["ok"] is True
    assert ergebnis["p2p"] is True
    assert ergebnis["http_status"] == 200
    assert ergebnis["body"] == "OK"
    # Der Befehl selbst reist als ?cmd=-Parameter:
    assert "cmd=restart" in str(aioclient_mock.mock_calls[0][1])
    sockel: AufzeichnenderSockel = koordinator._transport  # type: ignore[assignment]
    assert len(sockel.zielte_auf(TEST_IP)) == 1


async def test_rohbefehl_http_fehler_wird_gemeldet_nicht_geworfen(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator, aioclient_mock
) -> None:
    koordinator.nodes[5] = knoten(5)
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/control", exc=aiohttp.ClientError())
    ergebnis = await koordinator.async_send_raw_command(5, "restart")
    assert ergebnis["ok"] is True
    assert str(ergebnis["http_status"]).startswith("error:")


# ── Aufraeumen: Knoten vergessen ──────────────────────────────────────


def test_zustand_vergessen_raeumt_alles(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    koordinator.nodes[5] = knoten(5)  # ohne _on_node: kein /json-Nachfassen
    koordinator.tasks[(5, 0)] = task_config()
    koordinator.values[(5, 0)] = [1.0, 0, 0, 0]
    koordinator.last_seen[5] = time.monotonic()
    koordinator._meta_inflight.add(TEST_IP)

    koordinator.forget_node_state(5)

    assert koordinator.nodes == {}
    assert koordinator.tasks == {}
    assert koordinator.values == {}
    assert koordinator.last_seen == {}
    assert koordinator._meta_inflight == set()


async def test_remove_node_unbekannte_einheit_ist_false(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    assert await koordinator.async_remove_node(99) is False


async def test_remove_node_ohne_geraet_allein_moeglich(
    hass: HomeAssistant, entry: MockConfigEntry, koordinator: ESPEasyP2PCoordinator
) -> None:
    """Node bekannt, aber noch kein Registry-Eintrag (das Registry-Update
    war noch nicht dran): Entfernen funktioniert trotzdem."""
    entfernt = signal_sammler(hass, koordinator, SIGNAL_NODE_REMOVED)
    koordinator.nodes[5] = knoten(5, ip="0.0.0.0")

    assert await koordinator.async_remove_node(5) is True
    assert entfernt == [5]
    assert koordinator.nodes == {}


async def test_remove_node_mit_geraet_entfernt_entitaeten_geraet_und_signal(
    hass: HomeAssistant, entry: MockConfigEntry, koordinator: ESPEasyP2PCoordinator
) -> None:
    """A node with a registry device must be fully removed without exceptions."""
    geraet = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "unit-5")},
        name="Keller",
    )
    ent_reg = er.async_get(hass)
    entitaet = ent_reg.async_get_or_create(
        "switch", DOMAIN, "einmalig", device_id=geraet.id
    )
    koordinator.nodes[5] = knoten(5, ip="0.0.0.0")
    koordinator.tasks[(5, 0)] = task_config()
    koordinator.values[(5, 0)] = [1.0, 0, 0, 0]
    entfernt = signal_sammler(hass, koordinator, SIGNAL_NODE_REMOVED)

    assert await koordinator.async_remove_node(5) is True

    assert 5 not in koordinator.nodes
    assert (5, 0) not in koordinator.tasks
    assert (5, 0) not in koordinator.values
    assert ent_reg.async_get(entitaet.entity_id) is None
    assert device_for_unit(hass, 5) is None
    assert entfernt == [5]

# ── Overrides ─────────────────────────────────────────────────────────


def test_gpio_vorrang_override_vor_gelerntem_pin(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    koordinator.tasks[(5, 0)] = task_config(unit=5, name="Relais")
    koordinator.tasks[(5, 0)].gpio_pin = 4
    koordinator.pin_overrides["5/Relais"] = 26

    assert koordinator.get_gpio_pin(5, "Relais") == 26
    assert koordinator.get_gpio_pin(5, "Unbekannt") is None
    assert koordinator.get_gpio_pin(5, "") is None


def test_pin_override_patcht_aufgabe_im_speicher(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    koordinator.tasks[(5, 0)] = task_config(unit=5, name="Relais")
    koordinator.set_pin_override(5, "Relais", 26)

    assert koordinator.pin_overrides["5/Relais"] == 26
    assert koordinator.tasks[(5, 0)].gpio_pin == 26, (
        "laufende Entitaeten sehen den neuen Pin sofort, ohne Neustart"
    )


def test_befehlsvorlage_setzen_und_entleeren(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    koordinator.set_command_override(5, "Relais", "event,relais={state}")
    assert koordinator.get_command_template(5, "Relais") == "event,relais={state}"

    koordinator.set_command_override(5, "Relais", "")
    assert koordinator.get_command_template(5, "Relais") is None
    assert koordinator.get_command_template(5, "") is None


# ── Scan ──────────────────────────────────────────────────────────────


def test_scan_broadcastet_und_unicastet_bekannte_peers(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    koordinator.nodes[5] = knoten(5, ip=TEST_IP)
    koordinator.nodes[6] = knoten(6, ip="0.0.0.0")  # ohne IP: kein Unicast

    koordinator.async_scan()

    sockel: AufzeichnenderSockel = koordinator._transport  # type: ignore[assignment]
    broadcast = sockel.zielte_auf("255.255.255.255")
    assert len(broadcast) == 1
    assert broadcast[0][1] == PACKET_TYPE_INFO
    assert len(sockel.zielte_auf(TEST_IP)) == 1
    assert sockel.zielte_auf("0.0.0.0") == []


def test_scan_broadcast_fehler_stoppt_nicht_die_unicasts(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator, caplog
) -> None:
    """Container ohne Routing: Der Broadcast scheitert -- die Unicasts
    an bekannte Peers gehen trotzdem raus."""
    koordinator.nodes[5] = knoten(5, ip=TEST_IP)
    koordinator._transport.fehler_bei = "255.255.255.255"  # type: ignore[union-attr]

    koordinator.async_scan()

    assert len(koordinator._transport.zielte_auf(TEST_IP)) == 1  # type: ignore[union-attr]
    assert "Failed to broadcast scan" in caplog.text


def test_scan_ohne_sockel_ist_stumm(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    c = ESPEasyP2PCoordinator(hass, entry.entry_id, 8266, 250, "HA")
    c.async_scan()  # darf nichts tun, schon gar nicht abstuerzen


# ── Nachfragen: gezielter Resync ──────────────────────────────────────


async def test_resync_unbekannte_einheit_macht_nichts(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    koordinator.async_schedule_resync(99)
    koordinator.nodes[5] = knoten(5, ip="0.0.0.0")
    koordinator.async_schedule_resync(5)
    await hass.async_block_till_done()
    assert koordinator._meta_inflight == set()


async def test_resync_burst_holt_mehrfach_und_ueberspringt_inflight(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator, aioclient_mock
) -> None:
    """Ein Relaistimer schaltet zurueck -- der Burst liest mehrfach kurz
    hintereinander. Ist eine Abfrage noch in der Luft, wird nicht doppelt
    gepollt."""
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/json", json={"Sensors": []})
    node = knoten(5)

    await koordinator._resync_burst(node, (0, 0, 0))
    assert aioclient_mock.call_count >= 1

    # In-Flight: der Burst springt die Abfrage aus.
    koordinator._meta_inflight.add(TEST_IP)
    aufrufe = aioclient_mock.call_count
    await koordinator._resync_burst(node, (0,))
    assert aioclient_mock.call_count == aufrufe
    koordinator._meta_inflight.discard(TEST_IP)





async def test_knoten_entfernen_bricht_geplanten_resync_ab(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    koordinator.nodes[5] = knoten(5)
    koordinator.async_schedule_resync(5, delays=(3600,))
    await asyncio.sleep(0)

    assert any(unit == 5 for unit in koordinator._background_tasks.values())

    assert await koordinator.async_remove_node(5) is True
    await hass.async_block_till_done()
    # Let asyncio run task done-callbacks that remove completed jobs from
    # the coordinator's ownership map.
    await asyncio.sleep(0)

    assert koordinator._background_tasks == {}
    assert koordinator._meta_inflight == set()


async def test_geplanter_resync_fuehrt_zu_json_abfrage(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator, aioclient_mock
) -> None:
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/json", json={"Sensors": []})
    koordinator.nodes[5] = knoten(5)

    koordinator.async_schedule_resync(5, delays=(0.01, 0.02))
    await asyncio.sleep(0.1)
    await hass.async_block_till_done()

    assert aioclient_mock.call_count >= 1, "geplanter Burst hat gefragt"


async def test_alles_neu_abfragen_refetch(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator, aioclient_mock
) -> None:
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/json", json={"Sensors": []})
    koordinator.nodes[5] = knoten(5)
    koordinator.nodes[6] = knoten(6, ip="0.0.0.0")  # ohne IP: uebersprungen

    await koordinator.async_refetch_metadata()

    assert koordinator._meta_inflight == set()


def test_diagnose_zaehlt(
    hass: HomeAssistant, koordinator: ESPEasyP2PCoordinator
) -> None:
    koordinator.nodes[5] = knoten(5, ip="0.0.0.0")
    koordinator.tasks[(5, 0)] = task_config()
    assert koordinator.diagnostics() == {
        "port": koordinator.port, "unit": koordinator.unit,
        "node_count": 1, "task_count": 1,
    }


# ── Lebenszyklus mit echtem Sockel ────────────────────────────────────


async def test_start_bindet_sockel_und_stop_raeumt_ab(
    hass: HomeAssistant, entry: MockConfigEntry, monkeypatch, socket_enabled
) -> None:
    """Der echte Lebenslauf: binden, hoeren, aufhoeren. detect_local_ip
    wird auf die Schleife festgenagelt, damit der Test hermetisch bleibt."""
    monkeypatch.setattr(coordinator_modul, "detect_local_ip", lambda: "127.0.0.1")
    c = ESPEasyP2PCoordinator(
        hass, entry.entry_id, freier_udp_port(), 250, "Home Assistant"
    )

    await c.async_start()

    assert c._transport is not None
    assert c.local_ip == "127.0.0.1"
    assert c._announce_task is not None and not c._announce_task.done()

    await c.async_stop()

    assert c._transport is None
    assert c._announce_task is None
    assert c._aging_task is None
    assert c._value_task is None
    await hass.async_block_till_done()


async def test_ende_zu_ende_leitungsverkehr(
    hass: HomeAssistant, entry: MockConfigEntry, monkeypatch, socket_enabled
) -> None:
    """Typ-1 und Typ-5 durch einen echten UDP-Sockel auf der Schleife:
    Die Kette Paket → Protokoll → Koordinator → Registry/Zustand, ohne
    eine einzige Attrappe auf dem Weg."""
    monkeypatch.setattr(coordinator_modul, "detect_local_ip", lambda: "127.0.0.1")
    port = freier_udp_port()
    c = ESPEasyP2PCoordinator(hass, entry.entry_id, port, 250, "Home Assistant")
    await c.async_start()

    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Typ 1: ein Knoten meldet sich.
        sender.sendto(
            build_info_packet(unit=7, name="Keller", ip="127.0.0.1", web_port=59999),
            ("127.0.0.1", port),
        )
        for _ in range(100):
            if 7 in c.nodes:
                break
            await asyncio.sleep(0.02)
        assert 7 in c.nodes, "Typ-1 ist nicht bis zum Koordinator gekommen"

        # Typ 5: Werte vom selben Knoten.
        werte_paket = bytes(
            [PACKET_HEADER, PACKET_TYPE_SENSOR_DATA, 7, 0, 0, 0, 0, 0]
        ) + struct.pack("<4f", 1.0, 0.0, 0.0, 0.0)
        sender.sendto(werte_paket, ("127.0.0.1", port))
        for _ in range(100):
            if (7, 0) in c.values:
                break
            await asyncio.sleep(0.02)
        assert c.values[(7, 0)] == [1.0, 0.0, 0.0, 0.0]

        await hass.async_block_till_done()
        assert device_for_unit(hass, 7) is not None
    finally:
        sender.close()
        await c.async_stop()
        await hass.async_block_till_done()


# ── Die Nachfrage-Grenze ──────────────────────────────────────────────


async def test_gelernte_namen_stoppen_die_json_nachfrage(
    hass: HomeAssistant, entry: MockConfigEntry,
    koordinator: ESPEasyP2PCoordinator, aioclient_mock,
) -> None:
    """Sobald ein benannter Task da ist, pollt der Koordinator nicht
    weiter nach -- das ist das Gate, das den Dauerverkehr verhindert."""
    aioclient_mock.request(
        "GET", f"http://{TEST_IP}:80/json",
        json=_sensors_nutzlast([{"TaskNumber": 1, "TaskName": "Relais",
                                 "TaskValues": [{"Name": "State", "Value": 0}]}]),
    )
    koordinator._on_node(knoten(5))
    await hass.async_block_till_done()
    aufrufe = aioclient_mock.call_count
    assert aufrufe >= 1

    koordinator._on_node(knoten(5))  # naechster Herzschlag
    await hass.async_block_till_done()
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()

    assert aioclient_mock.call_count == aufrufe, (
        "mit Namen im Haus ist Schluss mit dem /json-Nachfragen"
    )


async def test_unicast_hallo_fehler_ist_nur_ein_log(
    hass: HomeAssistant, entry: MockConfigEntry,
    koordinator: ESPEasyP2PCoordinator, aioclient_mock, caplog,
) -> None:
    """Schlaegt das Hallo-Unicast fehl (OSError), ist die Entdeckung
    trotzdem komplett -- der Knoten funkt ja ohnehin."""
    aioclient_mock.request("GET", f"http://{TEST_IP}:80/json", exc=aiohttp.ClientError())
    sockel: AufzeichnenderSockel = koordinator._transport  # type: ignore[assignment]
    sockel.fehler_bei = TEST_IP

    koordinator._on_node(knoten(5))
    await hass.async_block_till_done()

    assert 5 in koordinator.nodes
    assert "Unicast hello" in caplog.text
