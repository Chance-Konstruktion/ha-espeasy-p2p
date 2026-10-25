"""Der Einstellungsfluss: Benutzerdialog und die Aufgabennachbearbeitung.

Der Options-Fluss ist der Ort, an dem der Nutzer den Integrationsfehler
des Koordinators -- 'das Relay reagiert nicht' -- selbst beheben kann:
Pin setzen, Befehlsvorlage setzen. Hier wird geprueft, dass die
Roundtrips wirklich ankommen: Was im Dialog gesetzt wird, landet in den
Optionen UND im laufenden Koordinator.

Beide Fluesse laufen ueber den echten Flow-Manager
(``hass.config_entries.(options.)flow``), nicht als direkte
Methodenaufrufe -- also inklusive Schema-Validierung durch HA.
"""

from __future__ import annotations

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.espeasy_p2p.config_flow import _SAVE_CHOICE
from custom_components.espeasy_p2p.const import (
    CONF_COMMAND_MAP,
    CONF_DISPLAY_PRECISION,
    CONF_GPIO_PIN_MAP,
    DOMAIN,
)
from custom_components.espeasy_p2p import coordinator as coordinator_modul
from custom_components.espeasy_p2p.protocol import TaskConfig

from conftest import freier_udp_port


@pytest.fixture(autouse=True)
def _eigene_integration(enable_custom_integrations):
    """Der Flow-Manager muss die Custom-Integration finden koennen."""


# ── Benutzerdialog: einmal pro Haus ───────────────────────────────────


@pytest.fixture
async def frisch(
    hass: HomeAssistant, monkeypatch, socket_enabled, enable_custom_integrations
) -> HomeAssistant:
    """Ein hass ohne laufenden Eintrag; was der Flow anlegt, wird im
    Teardown wieder abgebaut (der Anlegevorgang startet naemlich die
    echte Einrichtung -- inklusive UDP-Bindung)."""
    monkeypatch.setattr(coordinator_modul, "detect_local_ip", lambda: "127.0.0.1")
    yield hass
    for e in hass.config_entries.async_entries(DOMAIN):
        if e.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(e.entry_id)
    await hass.async_block_till_done()


async def test_dialog_zeigt_das_leere_formular(frisch: HomeAssistant) -> None:
    ergebnis = await frisch.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert ergebnis["type"] == "form"
    assert ergebnis["step_id"] == "user"


async def test_absenden_legt_einen_geladenen_eintrag_an(
    frisch: HomeAssistant,
) -> None:
    port = freier_udp_port()
    ergebnis = await frisch.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"},
        data={"port": port, "unit": 7, "name": "Testpeer",
              CONF_DISPLAY_PRECISION: 2},
    )
    await frisch.async_block_till_done()

    assert ergebnis["type"] == "create_entry"
    eintraege = frisch.config_entries.async_entries(DOMAIN)
    assert len(eintraege) == 1
    eintrag = eintraege[0]
    assert eintrag.data == {"port": port, "unit": 7, "name": "Testpeer"}
    assert eintrag.options == {CONF_DISPLAY_PRECISION: 2}
    assert eintrag.state is ConfigEntryState.LOADED, (
        "der neu angelegte Eintrag ist sofort scharf geschaltet"
    )
    assert frisch.services.has_service(DOMAIN, "scan")


async def test_ein_zweiter_eintrag_wird_abgelehnt(
    integriert, hass: HomeAssistant
) -> None:
    """Die Integration hoert broadcast-maessig nur einmal auf dem Haus --
    der zweite Versuch wird abgebrochen, nicht still verdoppelt."""
    ergebnis = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert ergebnis["type"] == "abort"
    assert ergebnis["reason"] == "single_instance_allowed"


@pytest.mark.parametrize(
    "daten",
    [
        {"port": 0, "unit": 7, "name": "x", CONF_DISPLAY_PRECISION: 3},
        {"port": 70000, "unit": 7, "name": "x", CONF_DISPLAY_PRECISION: 3},
        {"port": 8266, "unit": 256, "name": "x", CONF_DISPLAY_PRECISION: 3},
    ],
)
async def test_grenzwerte_werden_vom_schema_zurueckgewiesen(
    frisch: HomeAssistant, daten
) -> None:
    """Port und Einheit haben Draht-Grenzen (16 Bit, 1 Byte). Der
    Flow-Manager validiert ab dem zweiten Schritt gegen das Schema --
    genau diese Schicht pruefen wir hier: erst Formular zeigen, dann
    den Grenzwert abschicken."""
    formular = await frisch.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert formular["type"] == "form"
    with pytest.raises(InvalidData):
        await frisch.config_entries.flow.async_configure(formular["flow_id"], daten)


# ── Options-Fluss: Pins und Vorlagen nachjustieren ────────────────────


def _aufgabe(unit: int = 5, name: str = "Keller", idx: int = 0) -> TaskConfig:
    return TaskConfig(
        src_unit=unit, task_index=idx, device_number=33, task_name=name,
        value_names=["State", "", "", ""],
    )


async def _init(hass: HomeAssistant, eintrag: MockConfigEntry) -> dict:
    return await hass.config_entries.options.async_init(eintrag.entry_id)


async def test_ohne_aufgaben_gibt_es_keinen_aufgabenwaehler(
    hass: HomeAssistant, entry: MockConfigEntry, koordinator
) -> None:
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = koordinator
    ergebnis = await _init(hass, entry)

    assert ergebnis["type"] == "form"
    assert ergebnis["step_id"] == "init"
    assert "task" not in ergebnis["data_schema"].schema, (
        "ohne Schalteraufgaben gibt es nichts zu waehlen -- ein leerer "
        "Selektor waere eine Sackgasse im Dialog"
    )

    close = await hass.config_entries.options.async_configure(
        ergebnis["flow_id"], {"display_precision": 2}
    )
    await hass.async_block_till_done()
    assert close["type"] == "create_entry"
    assert entry.options == {
        CONF_DISPLAY_PRECISION: 2,
        CONF_GPIO_PIN_MAP: {},
        CONF_COMMAND_MAP: {},
    }


async def test_ohne_koordinator_bleibt_der_dialog_benutzbar(
    hass: HomeAssistant, entry: MockConfigEntry, enable_custom_integrations
) -> None:
    """Eintrag existiert, aber keine Integration geladen (z.B. nach dem
    Import): Der Dialog darf trotzdem aufgehen."""
    ergebnis = await _init(hass, entry)
    assert ergebnis["type"] == "form"
    close = await hass.config_entries.options.async_configure(
        ergebnis["flow_id"], {"display_precision": 1}
    )
    assert close["type"] == "create_entry"


async def test_aufgabenauswahl_fuehrt_zum_bearbeiten(
    hass: HomeAssistant, entry: MockConfigEntry, koordinator
) -> None:
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = koordinator
    koordinator._on_task(_aufgabe())
    ergebnis = await _init(hass, entry)

    schema = ergebnis["data_schema"].schema
    selector = next(v for k, v in schema.items() if str(k) == "task")
    labels = [o["label"] for o in selector.config["options"]]
    assert any("Unit 5" in l and "Keller" in l for l in labels)

    weiter = await hass.config_entries.options.async_configure(
        ergebnis["flow_id"], {"display_precision": 3, "task": "5/Keller"}
    )
    assert weiter["type"] == "form"
    assert weiter["step_id"] == "edit"
    # Beschreibung nennt Task und Einheit:
    assert weiter["description_placeholders"]["task"] == "Keller"
    assert weiter["description_placeholders"]["unit"] == "5"


async def test_pin_setzen_und_speichern_aktualisiert_eintrag_und_koordinator(
    hass: HomeAssistant, entry: MockConfigEntry, koordinator
) -> None:
    """Der Rundtrip, um den es geht: Pin im Dialog setzen -> Optionen
    gespeichert UND der laufende Koordinator schaltet sofort damit."""
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = koordinator
    koordinator._on_task(_aufgabe())
    init = await _init(hass, entry)
    edit = await hass.config_entries.options.async_configure(
        init["flow_id"], {"display_precision": 3, "task": "5/Keller"}
    )
    zurueck = await hass.config_entries.options.async_configure(
        edit["flow_id"], {"pin": 26, "cmd": "", "remove": False}
    )
    assert zurueck["type"] == "form" and zurueck["step_id"] == "init"

    close = await hass.config_entries.options.async_configure(
        zurueck["flow_id"], {"display_precision": 3, "task": _SAVE_CHOICE}
    )
    await hass.async_block_till_done()

    assert close["type"] == "create_entry"
    assert entry.options[CONF_GPIO_PIN_MAP] == {"5/Keller": 26}
    assert koordinator.pin_overrides == {"5/Keller": 26}, (
        "der laufende Koordinator uebernimmt ohne Neustart"
    )
    # Und die Konsequenz:
    assert koordinator.get_gpio_pin(5, "Keller") == 26


async def test_entfernen_kasten_loescht_pin_und_vorlage(
    hass: HomeAssistant, entry: MockConfigEntry, koordinator
) -> None:
    hass.config_entries.async_update_entry(
        entry,
        options={CONF_GPIO_PIN_MAP: {"5/Keller": 26},
                 CONF_COMMAND_MAP: {"5/Keller": "event,x={state}"},
                 CONF_DISPLAY_PRECISION: 3},
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = koordinator
    koordinator.pin_overrides["5/Keller"] = 26
    koordinator.command_overrides["5/Keller"] = "event,x={state}"
    koordinator._on_task(_aufgabe())

    init = await _init(hass, entry)
    edit = await hass.config_entries.options.async_configure(
        init["flow_id"], {"display_precision": 3, "task": "5/Keller"}
    )
    zurueck = await hass.config_entries.options.async_configure(
        edit["flow_id"], {"remove": True}
    )
    close = await hass.config_entries.options.async_configure(
        zurueck["flow_id"], {"display_precision": 3, "task": _SAVE_CHOICE}
    )
    assert close["type"] == "create_entry"

    assert entry.options[CONF_GPIO_PIN_MAP] == {}
    assert entry.options[CONF_COMMAND_MAP] == {}
    assert koordinator.pin_overrides == {}
    assert koordinator.command_overrides == {}


async def test_vorlage_setzen_und_wieder_leeren(
    hass: HomeAssistant, entry: MockConfigEntry, koordinator
) -> None:
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = koordinator
    koordinator._on_task(_aufgabe())

    init = await _init(hass, entry)
    edit = await hass.config_entries.options.async_configure(
        init["flow_id"], {"display_precision": 3, "task": "5/Keller"}
    )
    # Das echte Frontend laesst ein leeres Pin-Feld einfach weg (None
    # wuerde der Selektor abweisen) -- genau so fährt der Test.
    zurueck = await hass.config_entries.options.async_configure(
        edit["flow_id"], {"cmd": "event,keller={state}"}
    )
    close = await hass.config_entries.options.async_configure(
        zurueck["flow_id"], {"display_precision": 3, "task": _SAVE_CHOICE}
    )
    assert entry.options[CONF_COMMAND_MAP] == {"5/Keller": "event,keller={state}"}

    # Wieder leeren:
    init2 = await _init(hass, entry)
    edit2 = await hass.config_entries.options.async_configure(
        init2["flow_id"], {"display_precision": 3, "task": "5/Keller"}
    )
    assert edit2["description_placeholders"]["state"] == "{state}"
    zurueck2 = await hass.config_entries.options.async_configure(
        edit2["flow_id"], {"cmd": "   "}
    )
    close2 = await hass.config_entries.options.async_configure(
        zurueck2["flow_id"], {"display_precision": 3, "task": _SAVE_CHOICE}
    )
    assert close2["type"] == "create_entry"
    assert entry.options[CONF_COMMAND_MAP] == {}, "Leerraum ist 'kein Befehl'"


async def test_pin_ohne_zahl_wird_nicht_gespeichert(
    hass: HomeAssistant, entry: MockConfigEntry, koordinator
) -> None:
    """Der Zahlenselektor kann im Frontend frei getextet werden; hier
    (am Manager) pruefen wir die Abwehr im Flow selbst."""
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = koordinator
    koordinator._on_task(_aufgabe())
    init = await _init(hass, entry)
    edit = await hass.config_entries.options.async_configure(
        init["flow_id"], {"display_precision": 3, "task": "5/Keller"}
    )
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            edit["flow_id"], {"pin": "keine zahl", "cmd": ""}
        )


async def test_bearbeiten_ohne_auswahl_geht_zurueck_zum_start(
    hass: HomeAssistant, entry: MockConfigEntry, koordinator
) -> None:
    """Eine gerettete Sitzung ohne editing-key darf nicht in eine
    Edit-Sackgasse laufen."""
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = koordinator
    fluss = await hass.config_entries.options.async_init(entry.entry_id)
    # intern: _editing ist None -> async_step_edit leitet zum Init um
    handler = hass.config_entries.options._progress[fluss["flow_id"]]
    weiter = await handler.async_step_edit(None)
    assert weiter["step_id"] == "init"


async def test_zweite_verteidigungsgrenze_weist_unmengen_ab(
    hass: HomeAssistant, entry: MockConfigEntry, koordinator
) -> None:
    """Der Zahlenselektor (0..6) weist anstrechende Werte schon beim
    Manager ab -- die erste Verteidigungslinie."""
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = koordinator
    ergebnis = await _init(hass, entry)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            ergebnis["flow_id"], {"display_precision": 99}
        )


@pytest.mark.parametrize(
    ("eingabe", "erwartet"),
    [(99, 6), (-3, 0), ("quatsch", 3), (4.7, 4)],
)
async def test_genaugkeit_wird_im_fluss_geklemmt_oder_behalten(
    hass: HomeAssistant, entry: MockConfigEntry, koordinator, eingabe, erwartet
) -> None:
    """Die Grenzen sind 0..6; Unlesbares laesst den alten Wert stehen.

    Direkt am Handler, weil der Manager die Werte vorher mit dem
    Selektorschema abfaengt -- hier soll genau der Nachbearbeitungs-
    Code des Flusses selbst geprueft werden (zweite Verteidigungslinie,
    fuer Wege, die am Selektor vorbeikommen)."""
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = koordinator
    from custom_components.espeasy_p2p.config_flow import ESPEasyP2POptionsFlow

    fluss = ESPEasyP2POptionsFlow(entry)
    fluss.hass = hass
    ergebnis = await fluss.async_step_init({"display_precision": eingabe})
    assert ergebnis["type"] == "create_entry"
    assert ergebnis["data"][CONF_DISPLAY_PRECISION] == erwartet


async def test_verwaiste_schluessel_bleiben_sichtbar(
    hass: HomeAssistant, entry: MockConfigEntry, koordinator
) -> None:
    """Der Knoten 7 ist weg, sein Pin-Override steht noch in den
    Optionen: Der Dialog zeigt ihn, statt ihn still zu verschlucken."""
    hass.config_entries.async_update_entry(
        entry, options={CONF_GPIO_PIN_MAP: {"7/Weg": 2}, CONF_DISPLAY_PRECISION: 3}
    )
    ergebnis = await _init(hass, entry)

    schema = ergebnis["data_schema"].schema
    selector = next(v for k, v in schema.items() if str(k) == "task")
    labels = [o["label"] for o in selector.config["options"]]
    assert any("Unit 7" in l and "Weg" in l for l in labels)


# ── Nachbearbeitung direkt am Fluss (Zweite Verteidigungslinie) ───────


async def test_pin_als_text_wird_still_ignoriert(
    hass: HomeAssistant, entry: MockConfigEntry, koordinator
) -> None:
    """Am Manager faengt der Selektor Text ab (siehe oben). Direkt am
    Handler zeigt sich die zweite Schicht: int() schlaegt fehl, der Pin
    wird NICHT gespeichert, der Fluss laeuft weiter."""
    from custom_components.espeasy_p2p.config_flow import ESPEasyP2POptionsFlow

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = koordinator
    koordinator._on_task(_aufgabe())

    fluss = ESPEasyP2POptionsFlow(entry)
    fluss.hass = hass
    await fluss.async_step_init({"display_precision": 3, "task": "5/Keller"})
    ergebnis = await fluss.async_step_edit({"pin": "abc", "cmd": "event,x={state}"})

    assert ergebnis["step_id"] == "init"
    ergebnis2 = await fluss.async_step_init(
        {"display_precision": 3, "task": _SAVE_CHOICE}
    )
    assert ergebnis2["data"][CONF_GPIO_PIN_MAP] == {}, "Text ist kein Pin"
    assert ergebnis2["data"][CONF_COMMAND_MAP] == {"5/Keller": "event,x={state}"}


async def test_ohne_genauigkeitsangabe_bleibt_bisheriger_wert(
    hass: HomeAssistant, entry: MockConfigEntry, koordinator
) -> None:
    from custom_components.espeasy_p2p.config_flow import ESPEasyP2POptionsFlow

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = koordinator

    fluss = ESPEasyP2POptionsFlow(entry)
    fluss.hass = hass
    ergebnis = await fluss.async_step_init({"task": _SAVE_CHOICE})
    assert ergebnis["data"][CONF_DISPLAY_PRECISION] == 3
