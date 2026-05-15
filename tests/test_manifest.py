"""Smoke tests for manifest + hacs.json shape."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "custom_components" / "espeasy_p2p" / "manifest.json"
HACS = ROOT / "hacs.json"


def test_manifest_required_keys() -> None:
    data = json.loads(MANIFEST.read_text())
    for key in (
        "domain",
        "name",
        "codeowners",
        "config_flow",
        "documentation",
        "iot_class",
        "issue_tracker",
        "requirements",
        "version",
    ):
        assert key in data, f"manifest missing {key}"
    assert data["domain"] == "espeasy_p2p"
    assert data["config_flow"] is True


def test_hacs_required_keys() -> None:
    data = json.loads(HACS.read_text())
    assert "name" in data
    assert "homeassistant" in data
