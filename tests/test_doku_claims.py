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
