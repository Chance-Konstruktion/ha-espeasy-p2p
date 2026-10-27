"""Die Sensor-Plattform: ein Wert pro Entitaet, und nur echte.

Der Sensor ist die einfache Haelfte der Plattformen -- aber genau hier
entscheidet sich, ob die Oberflaeche vertrauenswuerdig ist: keine
Phantom-Entitaeten fuer unbenannte Slots, keine Schalter als Sensoren,
Zahlen so genau wie eingestellt (nicht genauer), und 'nicht verfuegbar'
genau dann, wenn der Knoten wirklich verschollen ist.
"""

from __future__ import annotations

import time

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.espeasy_p2p.const import NODE_OFFLINE_TIMEOUT
from custom_components.espeasy_p2p.protocol import TaskConfig, TaskValues

from conftest import knoten


def _task(unit: int = 5, name: str = "Klima", idx: int = 0,
          werte: list[str] | None = None) -> TaskConfig:
    return TaskConfig(
        src_unit=unit, task_index=idx, device_number=33, task_name=name,
        value_names=werte if werte is not None else
        ["Temperature", "Humidity", "", ""],
    )


def _werte(unit: int, idx: int, werte: list[float]) -> TaskValues:
    return TaskValues(src_unit=unit, task_index=idx,
                      values=werte + [0.0] * (4 - len(werte)))


def _online(coordinator, unit: int = 5) -> None:
    coordinator.nodes[unit] = knoten(unit)
    coordinator.last_seen[unit] = time.monotonic()


async def _sensor_entity_id(hass: HomeAssistant, unit: int, task: int,
                            wert: int) -> str:
    ent_reg = er.async_get(hass)
    eintraege = [
        e for e in ent_reg.entities.values()
        if e.unique_id == f"espeasy_p2p_{unit}_{task}_{wert}"
    ]
    assert eintraege, f"Kein Sensor fuer unit={unit} task={task} wert={wert} angelegt"
    return eintraege[0].entity_id


# ── Wer wird Sensor, wer nicht? ───────────────────────────────────────


async def test_benannte_werte_werden_sensoren(integriert, hass: HomeAssistant) -> None:
    _online(integriert)
    integriert._on_task(_task())
    await hass.async_block_till_done()

    assert await _sensor_entity_id(hass, 5, 0, 0)
    assert await _sensor_entity_id(hass, 5, 0, 1)


@pytest.mark.parametrize("werte", [["State", "", "", ""], ["Relay", "", "", ""]])
async def test_schalterwerte_bleiben_keine_sensoren(
    integriert, hass: HomeAssistant, werte
) -> None:
    _online(integriert)
    integriert._on_task(_task(werte=werte))
    await hass.async_block_till_done()
    ent_reg = er.async_get(hass)
    sensoren = [e for e in ent_reg.entities.values()
                if e.unique_id.startswith("espeasy_p2p_")
                and e.domain == "sensor"]
    assert sensoren == []


async def test_platzhalter_bekommen_keine_entitaet_und_namen_schon(
    integriert, hass: HomeAssistant
) -> None:
    """Erst Werte (ohne Namen), spaeter die Namen: Der Sensor erscheint
    erst mit echten Namen -- und zwar ohne Neustart, sobald Typ 3 oder
    /json sie liefert."""
    _online(integriert)
    integriert._on_values(_werte(5, 0, [1.0]))
    await hass.async_block_till_done()
    ent_reg = er.async_get(hass)
    assert [e for e in ent_reg.entities.values()
            if e.unique_id.startswith("espeasy_p2p_")] == []

    integriert._on_task(_task(name="Klima"))
    await hass.async_block_till_done()
    assert await _sensor_entity_id(hass, 5, 0, 0)


async def test_erneute_entdeckung_dupliziert_nicht(
    integriert, hass: HomeAssistant
) -> None:
    _online(integriert)
    integriert._on_task(_task())
    await hass.async_block_till_done()
    integriert._on_task(_task())
    await hass.async_block_till_done()
    ent_reg = er.async_get(hass)
    sensoren = [e for e in ent_reg.entities.values()
                if e.unique_id.startswith("espeasy_p2p_")
                and e.domain == "sensor"]
    assert len(sensoren) == 2  # Temperature + Humidity, genau einmal


# ── Zahlen und Anzeige ────────────────────────────────────────────────


async def test_wert_landet_im_zustand_mit_defaultgenauigkeit(
    integriert, hass: HomeAssistant
) -> None:
    """Default sind 3 Nachkommastellen. HA 2026.x uebersetzt die Option
    in eine Registry-Option ('suggested_display_precision'); das Runden
    geschieht im Frontend -- der Zustandsstring im Kern bleibt str(float)."""
    _online(integriert)
    integriert._on_task(_task())
    await hass.async_block_till_done()
    entity_id = await _sensor_entity_id(hass, 5, 0, 0)

    integriert._on_values(_werte(5, 0, [21.5]))
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "21.5"
    registry_option = er.async_get(hass).async_get(entity_id).options
    assert registry_option == {"sensor": {"suggested_display_precision": 3}}


@pytest.mark.parametrize("entry_optionen", [{"display_precision": 1}], indirect=True)
async def test_anzeigegenauigkeit_aus_den_optionen(
    integriert, hass: HomeAssistant
) -> None:
    _online(integriert)
    integriert._on_task(_task())
    await hass.async_block_till_done()
    entity_id = await _sensor_entity_id(hass, 5, 0, 0)

    integriert._on_values(_werte(5, 0, [21.5]))
    await hass.async_block_till_done()

    registry_option = er.async_get(hass).async_get(entity_id).options
    assert registry_option == {"sensor": {"suggested_display_precision": 1}}, (
        "die eingestellte Genauigkeit muss beim Entitaetseintrag ankommen"
    )
    assert hass.states.get(entity_id).state == "21.5"


async def test_ohne_wert_bleibt_unbekannt(integriert, hass: HomeAssistant) -> None:
    """Slot mit Namen, aber ohne empfangenen Wert: 'unknown', nicht 0."""
    _online(integriert)
    integriert._on_task(_task(werte=["Temperature", "", "", ""]))
    await hass.async_block_till_done()
    entity_id = await _sensor_entity_id(hass, 5, 0, 0)
    assert hass.states.get(entity_id).state == "unknown"


async def test_wertindex_jenseits_der_empfangenen_werte(
    integriert, hass: HomeAssistant
) -> None:
    _online(integriert)
    integriert._on_task(_task(werte=["Temperature", "", "", ""]))
    await hass.async_block_till_done()
    entity_id = await _sensor_entity_id(hass, 5, 0, 0)
    integriert._on_values(_werte(5, 0, [21.5]))  # nur Slot 0 belegt
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "21.5"


# ── Klassifizierung: Name bestimmt Klasse und Einheit ─────────────────


async def test_temperatur_bekommt_grad_celsius(
    integriert, hass: HomeAssistant
) -> None:
    _online(integriert)
    integriert._on_task(_task(werte=["Temperature", "", "", ""]))
    await hass.async_block_till_done()
    entity_id = await _sensor_entity_id(hass, 5, 0, 0)
    zustand = hass.states.get(entity_id)
    assert zustand.attributes["device_class"] == "temperature"
    assert zustand.attributes["unit_of_measurement"] == "°C"


async def test_luftfeuchte_bekommt_prozent(
    integriert, hass: HomeAssistant
) -> None:
    _online(integriert)
    integriert._on_task(_task(werte=["Humidity", "", "", ""]))
    await hass.async_block_till_done()
    entity_id = await _sensor_entity_id(hass, 5, 0, 0)
    zustand = hass.states.get(entity_id)
    assert zustand.attributes["device_class"] == "humidity"
    assert zustand.attributes["unit_of_measurement"] == "%"


async def test_druck_bekommt_hectopascal(
    integriert, hass: HomeAssistant
) -> None:
    _online(integriert)
    integriert._on_task(_task(werte=["Pressure", "", "", ""]))
    await hass.async_block_till_done()
    entity_id = await _sensor_entity_id(hass, 5, 0, 0)
    zustand = hass.states.get(entity_id)
    assert zustand.attributes["device_class"] == "pressure"
    assert zustand.attributes["unit_of_measurement"] == "hPa"


async def test_plugin_als_rueckfallebene_fuer_deutsche_namen(
    integriert, hass: HomeAssistant
) -> None:
    """Task heisst 'Keller' (nichts deutbar), aber der Plugintyp sagt
    DS18b20: Auch dann Temperature-Klasse -- der Nutzer benennt frei,
    die Firmware kennt ihren Sensor."""
    _online(integriert)
    aufgabe = _task(werte=["Istwert", "", "", ""])
    aufgabe.plugin_type = "Environment - DS18b20"
    integriert._on_task(aufgabe)
    await hass.async_block_till_done()
    entity_id = await _sensor_entity_id(hass, 5, 0, 0)
    assert hass.states.get(entity_id).attributes["device_class"] == "temperature"


async def test_dht_plugin_keeps_humidity_value_as_humidity(
    integriert, hass: HomeAssistant
) -> None:
    """A DHT plugin exposes multiple differently typed value slots."""
    _online(integriert)
    aufgabe = _task(werte=["Temperature", "Humidity", "", ""])
    aufgabe.plugin_type = "Environment - DHT22"
    integriert._on_task(aufgabe)
    await hass.async_block_till_done()

    temperatur_id = await _sensor_entity_id(hass, 5, 0, 0)
    feuchte_id = await _sensor_entity_id(hass, 5, 0, 1)
    temperatur = hass.states.get(temperatur_id)
    feuchte = hass.states.get(feuchte_id)

    assert temperatur.attributes["device_class"] == "temperature"
    assert temperatur.attributes["unit_of_measurement"] == "°C"
    assert feuchte.attributes["device_class"] == "humidity"
    assert feuchte.attributes["unit_of_measurement"] == "%"


async def test_namenloser_task_bekommt_slotnamen(
    integriert, hass: HomeAssistant
) -> None:
    _online(integriert)
    integriert._on_task(_task(name="", werte=["Temperature", "", "", ""]))
    await hass.async_block_till_done()
    entity_id = await _sensor_entity_id(hass, 5, 0, 0)
    zustand = hass.states.get(entity_id)
    # has_entity_name: HA setzt den Geraetenamen davor (Geraet heisst
    # hier wie der Knoten "Keller").
    assert zustand.attributes["friendly_name"] == "Keller Temperature"


# ── Verfuegbarkeit ────────────────────────────────────────────────────


async def test_verschollener_knoten_wird_unavailable(
    integriert, hass: HomeAssistant
) -> None:
    _online(integriert)
    integriert._on_task(_task())
    await hass.async_block_till_done()
    entity_id = await _sensor_entity_id(hass, 5, 0, 0)
    integriert._on_values(_werte(5, 0, [21.5]))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "21.5"

    integriert.last_seen[5] = time.monotonic() - (NODE_OFFLINE_TIMEOUT + 1)
    integriert._evaluate_availability()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    integriert._touch(5)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "21.5"


async def test_nie_gesehener_knoten_ist_auch_unavailable(
    integriert, hass: HomeAssistant
) -> None:
    """Entity existiert (Task kam rein), aber der Knoten wurde noch nie
    gesehen: verfuegbar ist anders gelogen."""
    integriert._on_task(_task())
    await hass.async_block_till_done()
    entity_id = await _sensor_entity_id(hass, 5, 0, 0)
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE


# ── Randfaelle direkt an der Entitaet ─────────────────────────────────


def test_sensorname_faellt_auf_slotnamen_zurueck(
    hass: HomeAssistant, entry, koordinator
) -> None:
    """Task da, aber der Slot hat keinen Namen: 'Klima value 3'."""
    from custom_components.espeasy_p2p.sensor import ESPEasyP2PValueSensor

    koordinator.tasks[(5, 0)] = _task(werte=["", "", "", ""])
    entitaet = ESPEasyP2PValueSensor(koordinator, entry.entry_id, 5, 0, 2)
    assert entitaet.name == "Klima value 3"


def test_sensorname_ohne_aufgabe_nennt_task_und_slot(
    hass: HomeAssistant, entry, koordinator
) -> None:
    from custom_components.espeasy_p2p.sensor import ESPEasyP2PValueSensor

    entitaet = ESPEasyP2PValueSensor(koordinator, entry.entry_id, 5, 0, 1)
    assert entitaet.name == "Task 0 value 2"


async def test_fremde_signale_bewegen_den_sensor_nicht(
    integriert, hass: HomeAssistant
) -> None:
    _online(integriert)
    integriert._on_task(_task())
    await hass.async_block_till_done()
    entity_id = await _sensor_entity_id(hass, 5, 0, 0)
    integriert._on_values(_werte(5, 0, [21.5]))
    await hass.async_block_till_done()
    vorher = hass.states.get(entity_id).state

    fremd = _werte(6, 0, [99.0])
    integriert._on_values(fremd)
    fremder_task = _task(unit=6, name="Woanders")
    integriert._on_task(fremd_task := fremder_task)
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == vorher
