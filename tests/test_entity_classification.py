"""Regel-Konformität der Switch-Klassifikation (Durchsicht #4, Posten 3).

Der Modul-Docstring schreibt vor, dass Stems >= 5 Zeichen und eindeutige
Substantive sein müssen — „heiz" hatte beide Anforderungen verletzt und
matchte Verben wie „heizen"/„aufheizen". Diese Suite hält die Regel fest.
"""

from __future__ import annotations

from espeasy_p2p_files import entity_classification as modul
from espeasy_p2p_files.entity_classification import (
    _DEFAULT,
    _RULES,
    classify_switch,
)


def test_alle_stems_folgen_der_eigenen_docstring_regel():
    for regel in _RULES:
        for stem in regel.stems:
            assert len(stem) >= 5, (
                f"Stem {stem!r} hat {len(stem)} Zeichen — der Docstring "
                "verlangt >= 5 (Kurzsteme matchen zu viele Wörter)"
            )


def test_heizen_matcht_keine_heizung_mehr():
    # Verben bleiben unklassifiziert: ein "heizen"-Schalter ist nicht
    # zwangsläufig eine Heizung.
    assert classify_switch("heizen") == _DEFAULT
    assert classify_switch("aufheizen") == _DEFAULT


def test_heizung_und_heizkoerper_bleiben_heizung():
    assert classify_switch("heizung").icon == "mdi:radiator"
    assert classify_switch("fussbodenheizung").icon == "mdi:radiator"
    # Umlaut-Faltung: heizkörper -> heizkoerper
    assert classify_switch("heizkörper").icon == "mdi:radiator"
