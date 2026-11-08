"""Die Versprechen der Doku gegen den Stand des Codes.

HACS zeigt bei dieser Integration info.md an (hacs.json setzt
render_readme auf false), und die README tragen Nutzer ohnehin mit --
genau dort hat die Durchsicht (#1) Aussagen gefunden, die der Code nicht
haelt. Jeder Test hier pinnt eine korrigierte Aussage; verschwindet die
Korrektur wieder, faellt der Test auf.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _lies(name: str) -> str:
    return (_REPO / name).read_text(encoding="utf-8")


def test_info_md_zaehlt_keinen_verfuegbarkeits_sensor():
    """Fund 1: Ein `available`-Sensor existiert nicht (die README korrigiert
    sich an dieser Stelle bereits selbst); info.md darf das nicht
    weiterhin versprechen."""
    text = _lies("info.md")
    assert "`available` sensor" not in text
    assert "unavailable" in text, (
        "die Watchdog-Aussage muss die echte Verfügbarkeit beschreiben"
    )


def test_info_md_leugnet_den_30_sekunden_abgleich_nicht():
    """Fund 2: Es gibt einen 30-Sekunden-/json-Abgleich (const.py:
    VALUE_REFRESH_INTERVAL, coordinator._value_refresh_loop) -- info.md
    behauptete das Gegenteil ('no polling', 'pushed the moment it
    changes')."""
    text = _lies("info.md")
    assert "no polling" not in text
    assert "pushed the moment it changes" not in text
    assert "30-second `/json`" in text


def test_info_md_nennt_den_wertnamen_nicht_die_plugin_id_als_massgebend():
    """Fund 3: _classify ordnet primaer nach dem Wertnamen zu (Temperature/
    Humidity/Pressure/...), die Plugin-ID ist nur Ruefallebene
    (sensor.py:_classify; der /json-Abgleich liest 'Type' nur als
    plugin_type ein)."""
    text = _lies("info.md")
    assert "from the plugin ID" not in text
    assert "value name" in text


def test_info_md_verspricht_keine_sensor_restaurierung():
    """Fund 4: Nur Schalter restaurieren ihren Zustand ueber den Neustart
    (switch.py async_added_to_hass); Sensorwerte starten bewusst leer und
    leben mit dem naechsten Paket oder /json-Abgleich wieder auf -- die
    README (Zeile 74-75) sagt es richtig."""
    text = _lies("info.md")
    assert "last values survive HA restarts" not in text
    assert "sensor values" in text and "resume" in text


def test_info_md_gibt_den_einheitenbereich_wie_die_readme_an():
    """Fund 13: 255 ist Broadcast-Marker (const.BROADCAST_UNIT, README
    Known limitations); gueltige Knotennummern sind 1-254. info.md sagte
    'unique 1-255 per node' und widersprach damit der README."""
    text = _lies("info.md")
    assert "1–255 per node" not in text
    assert "1–254 per node" in text


def test_readme_beschreibt_den_sensorrahmen_ohne_plugin_id():
    """Fund 5: Der Kopf ist 0xFF-Magie + Typbyte (const.PACKET_HEADER und
    PACKET_TYPE_*), es gibt kein "protocol version"-Byte; und der
    Typ-5-Sensorframe traegt keine Plugin-ID (SENSOR_DATA_STRUCT) -- die
    steckt nur im Typ-3-Configframe (_handle_sensor_data, device_number).
    Gegencheck gegen die im Docstring genannte rpieasy-Referenz: der Code
    stimmt exakt, nur die Beschreibung nicht."""
    text = _lies("README.md")
    assert "protocol version" not in text
    assert "0xFF" in text
    assert "- **Plugin ID**" not in text
    assert "type-3 config frame" in text


def test_readme_beitragen_anleitung_isst_was_es_gibt():
    """Fund 6: requirements_test.txt existiert nicht (nur
    requirements_test_ha.txt), und der Klonordner heisst espeasy-p2p,
    nicht ha-espeasy-p2p -- der Contributing-Block brach an beiden
    Stellen. Die Installationszeile entspricht jetzt dem, was die CI
    fuer die kernlose Suite auch anzieht (pytest + pytest-asyncio)."""
    text = _lies("README.md")
    assert "requirements_test.txt" not in text.replace(
        "requirements_test_ha.txt", ""
    )
    assert "cd espeasy-p2p" in text
    assert "cd ha-espeasy-p2p" not in text
    assert "requirements_test_ha.txt" in text
