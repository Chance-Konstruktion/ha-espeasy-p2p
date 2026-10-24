"""Die Namens-Deutung: aus freivergebenen Tasknamen wird Icon und Klasse.

Die Regeln stehen in ``entity_classification.py`` und sind eine
Wertentscheidung, kein Zufall: Reihenfolge (Pumpe vor Heizung), scharfe
Trennung zwischen Ganzwort-Tokens und Stamm-Teiltreffern, Umlaut-Faltung.
Jeder Test hier haelt eine dieser Entscheidungen fest -- mit den Namen,
fuer die sie getroffen wurden.
"""

from __future__ import annotations

import pytest
from homeassistant.components.switch import SwitchDeviceClass

from custom_components.espeasy_p2p.entity_classification import classify_switch


def _icon(name: str) -> str | None:
    return classify_switch(name).icon


# ── Der neutrale Fall ─────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["", "   ", "\x00", "123", "!!"])
def test_ohne_treffbaren_namen_bleibt_der_neutrale_schalter(name):
    ergebnis = classify_switch(name)
    assert ergebnis.device_class is None
    assert ergebnis.icon == "mdi:toggle-switch-variant"


# ── Tokens: das ganze Wort muss treffen ───────────────────────────────


@pytest.mark.parametrize("name", ["LED", "Wand LED", "Lampe", "Deckenlicht"])
def test_licht_trifft_auf_ganze_woerter(name):
    assert _icon(name) == "mdi:lightbulb"


@pytest.mark.parametrize("name", ["scheduled", "infant", "motor", "Ledgerliste"])
def test_licht_trifft_nicht_im_wortinneren(name):
    """'led' in 'scheduled' waere der Klassiker -- deshalb Token-Modus."""
    assert _icon(name) == "mdi:toggle-switch-variant"


def test_garage_und_tor_als_ganzes_wort():
    assert _icon("Garage") == "mdi:garage"
    assert _icon("Garagentor") == "mdi:toggle-switch-variant", (
        "'tor' ist zu kurz und mehrdeutig, um im Wortinneren zu treffen"
    )


def test_luefter_mit_umlaut_wird_gefaltet():
    """'Lüfter' wird zu 'luefter' gefaltet -- der Stamm steht so in den
    Regeln, nicht die Umlautform."""
    assert _icon("Lüfter") == "mdi:fan"


# ── Stems: der deutsche Nominalstamm trifft im Kompositum ─────────────


def test_heizpumpe_ist_eine_pumpe_nicht_eine_heizung():
    """Die Reihenfolge der Regeln ist die Aussage: 'pumpe' schlaegt
    'heiz' -- eine Heizpumpe bewaessert nichts, aber sie pumpt."""
    assert _icon("Heizpumpe") == "mdi:water-pump"
    assert _icon("Umwälzpumpe") == "mdi:water-pump"


def test_komposita_ueber_stamm():
    assert _icon("Kaffeemaschine") == "mdi:coffee-maker"
    assert _icon("Regenwassertonne") == "mdi:toggle-switch-variant"
    assert _icon("Bewässerung Garten") == "mdi:sprinkler-variant"
    assert _icon("Heizstab Boiler") == "mdi:water-boiler"


def test_steckdose_und_kaffee_bekommen_die_outlet_klasse():
    steckdose = classify_switch("Steckdose Wohnzimmer")
    assert steckdose.device_class is SwitchDeviceClass.OUTLET
    assert steckdose.icon == "mdi:power-socket-eu"

    kaffee = classify_switch("Kaffeemaschine")
    assert kaffee.device_class is SwitchDeviceClass.OUTLET


def test_relais_ist_generisch_niedrig_priorisiert():
    assert _icon("Relais") == "mdi:electric-switch"
    assert _icon("Relay Pump") == "mdi:water-pump", (
        "das Pumpe-Wort gewinnt, auch wenn 'Relay' im Namen steht"
    )


# ── Trennung und Gross-/Kleinschreibung ───────────────────────────────


def test_sonderzeichen_als_worttrenner():
    assert _icon("Licht – Flur") == "mdi:lightbulb"
    assert _icon("Pumpe/Garten") == "mdi:water-pump"
    # Unterstrich trennt wie Leerraum -- und "brunnen" ist ein Stamm der
    # PUMPEN-Regel, die vor der Ventil-Regel steht. Erste Regel gewinnt.
    assert _icon("Ventil_Brunnen") == "mdi:water-pump"
    assert _icon("Ventil Garten") == "mdi:valve"


def test_grossschreibung_stoert_nicht():
    assert _icon("PUMPE") == "mdi:water-pump"
    assert _icon("GARAGE Tor") == "mdi:garage"


# ── Der Wertname zaehlt mit ───────────────────────────────────────────


def test_wertname_wird_mitgedeutet():
    """Task heisst 'Hilfsmittel', Wert heisst 'Relay' -- zusammen faellt
    der generische Schalter raus, aber die Pumpe gewinnt trotzdem."""
    assert classify_switch("Hilfsmittel", "Relay").icon == "mdi:electric-switch"
    assert classify_switch("Hilfsmittel", "Pumpenzustand").icon == "mdi:water-pump"


def test_leerer_taskname_mit_wertname():
    assert classify_switch("", "Temperature").icon == "mdi:toggle-switch-variant", (
        "Sensorwerte sind hier bewusst nicht gedeutet -- Klassifizierung "
        "von Sensoren ist Sache des Sensor-Moduls"
    )


def test_ergebnis_ist_eingefroren():
    """Gefrorene Dataclasses: niemand biegt das Icon zur Laufzeit um."""
    ergebnis = classify_switch("Pumpe")
    with pytest.raises(Exception):
        ergebnis.icon = "mdi:etwas-anderes"


def test_leerer_text_liefert_keine_tokens():
    """Der Direktaufruf, den classify_switch selbst nie erreichen kann
    (der verbundene Text hat immer mindestens das Leerzeichen) -- und
    genau deshalb ist der Zweig hier festgehalten."""
    from custom_components.espeasy_p2p.entity_classification import _tokens

    assert _tokens("") == []
