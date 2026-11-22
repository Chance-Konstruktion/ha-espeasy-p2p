"""Der Config-Flow muss auf der von hacs.json versprochenen HA-Version
importierbar sein (#3).

``ConfigFlowResult`` gibt es erst seit Home Assistant 2024.4; hacs.json
verspricht aber ``2024.1.0``. Ohne Import-Guard bricht dort der Import
von config_flow -- und damit das komplette Setup -- mit ImportError.
Der Test ersetzt ``homeassistant.config_entries`` durch eine Attrappe
ohne ``ConfigFlowResult`` (HA 2024.1-2024.3) und verlangt, dass der
Module-Import trotzdem klappt und der Name aus ``data_entry_flow``
nachgeruestet wird. Rot ohne den try/except-Import-Guard.
"""

from __future__ import annotations

import importlib
import sys
import types
from types import SimpleNamespace


def _ha_2024_1_config_entries() -> types.ModuleType:
    """Attrappe von homeassistant.config_entries wie in HA 2024.1-2024.3:
    ohne ConfigFlowResult."""

    class _ConfigFlow:
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()

    module = types.ModuleType("homeassistant.config_entries")
    module.ConfigEntry = object
    module.ConfigFlow = _ConfigFlow
    module.OptionsFlow = object
    return module


def test_config_flow_importiert_auch_ohne_configflowresult(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "homeassistant.config_entries",
        _ha_2024_1_config_entries(),
    )
    # Das Frisch-Importieren des Moduls erzwingen; die data_entry_flow-
    # Attrappe aus conftest.py bleibt stehen und stellt FlowResult bereit.
    monkeypatch.delitem(sys.modules, "espeasy_p2p_files.config_flow", raising=False)

    config_flow = importlib.import_module("espeasy_p2p_files.config_flow")

    assert hasattr(config_flow, "ConfigFlowResult")
    assert hasattr(config_flow, "ESPEasyP2PConfigFlow")
    assert hasattr(config_flow, "ESPEasyP2POptionsFlow")
