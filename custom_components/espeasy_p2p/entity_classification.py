"""Heuristic classification of ESPEasy tasks into HA device_class + icon.

Users name tasks freely in the ESPEasy web UI ("Heizstab Boiler",
"Pumpe Garten", "Garage Tor", ...). We use those names to pick a sensible
icon (and where applicable a SwitchDeviceClass) so the dashboard shows
something useful instead of a generic toggle.

Matching has two modes per rule:

* **tokens**: the keyword must equal a full whitespace-separated token
  in the (normalised) name. Use this for short or ambiguous keywords
  ("led", "fan", "tor") that would otherwise match "scheduled", "infant",
  "motor", ...

* **stems**: the keyword must appear as a substring of any token. Use
  this for unambiguous German noun stems that survive compounding —
  e.g. "pumpe" in "Heizpumpe" / "Umwälzpumpe", "kaffee" in
  "Kaffeemaschine". Only use stems that are >= 5 chars and clearly
  unique to the domain.

Rules are evaluated top-to-bottom; the first match wins. Put more
specific rules above generic ones — "Heizpumpe" must hit the pump rule,
not the heating rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from homeassistant.components.switch import SwitchDeviceClass


@dataclass(frozen=True)
class SwitchPresentation:
    device_class: SwitchDeviceClass | None
    icon: str | None


@dataclass(frozen=True)
class _Rule:
    presentation: SwitchPresentation
    tokens: tuple[str, ...] = field(default_factory=tuple)
    stems: tuple[str, ...] = field(default_factory=tuple)


_DEFAULT = SwitchPresentation(device_class=None, icon="mdi:toggle-switch-variant")

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _tokens(text: str) -> list[str]:
    if not text:
        return []
    folded = text.lower().translate(_UMLAUTS)
    return [t for t in _NON_ALNUM.split(folded) if t]


# Order matters — most specific first.
_RULES: tuple[_Rule, ...] = (
    # Pumps must beat heating (Heizpumpe, Umwälzpumpe).
    _Rule(
        SwitchPresentation(None, "mdi:water-pump"),
        tokens=("pump",),
        stems=("pumpe", "brunnen", "zirkulation", "circulation"),
    ),
    # Irrigation / sprinkler.
    _Rule(
        SwitchPresentation(None, "mdi:sprinkler-variant"),
        tokens=("sprinkler", "irrigation", "rasen"),
        stems=("bewaesserung",),
    ),
    # Valves.
    _Rule(
        SwitchPresentation(None, "mdi:valve"),
        tokens=("valve",),
        stems=("ventil",),
    ),
    # Boiler / heating rod / hot water.
    _Rule(
        SwitchPresentation(None, "mdi:water-boiler"),
        stems=("heizstab", "boiler", "warmwasser", "durchlauferhitzer"),
    ),
    # General heating.
    _Rule(
        SwitchPresentation(None, "mdi:radiator"),
        tokens=("heater", "radiator", "thermostat"),
        stems=("heizung", "heating", "heiz"),
    ),
    # Fans / ventilation.
    _Rule(
        SwitchPresentation(None, "mdi:fan"),
        tokens=("fan",),
        stems=("luefter", "ventilator", "abluft", "zuluft"),
    ),
    # Lighting.
    _Rule(
        SwitchPresentation(None, "mdi:lightbulb"),
        tokens=("light", "lamp", "led"),
        stems=("licht", "lampe", "leuchte", "beleuchtung"),
    ),
    # Garage / gate.
    _Rule(
        SwitchPresentation(None, "mdi:garage"),
        tokens=("garage", "gate"),
        stems=("rolltor",),
    ),
    # Door / lock.
    _Rule(
        SwitchPresentation(None, "mdi:door"),
        tokens=("door", "lock", "tuer"),
        stems=("schloss",),
    ),
    # Power outlet / socket.
    _Rule(
        SwitchPresentation(SwitchDeviceClass.OUTLET, "mdi:power-socket-eu"),
        tokens=("outlet", "socket", "plug"),
        stems=("steckdose",),
    ),
    # Coffee / espresso.
    _Rule(
        SwitchPresentation(SwitchDeviceClass.OUTLET, "mdi:coffee-maker"),
        tokens=("coffee", "espresso"),
        stems=("kaffee",),
    ),
    # Generic relay — low priority, many tasks include "Relay" by default.
    _Rule(
        SwitchPresentation(None, "mdi:electric-switch"),
        tokens=("relay",),
        stems=("relais",),
    ),
)


def classify_switch(task_name: str, value_name: str = "") -> SwitchPresentation:
    """Pick (device_class, icon) for a switch entity by task/value name.

    Returns a neutral toggle icon if nothing matches.
    """
    tokens = _tokens(f"{task_name} {value_name}")
    if not tokens:
        return _DEFAULT
    token_set = set(tokens)
    for rule in _RULES:
        if rule.tokens and token_set.intersection(rule.tokens):
            return rule.presentation
        if rule.stems and any(stem in tok for tok in tokens for stem in rule.stems):
            return rule.presentation
    return _DEFAULT
