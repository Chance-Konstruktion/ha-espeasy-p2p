"""Regression guards for Home Assistant 2026.11 compatibility."""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "espeasy_p2p"
FLOW_RESULT_METHODS = {
    "async_show_form",
    "async_show_menu",
    "async_show_progress",
    "async_external_step",
}


def _source_files() -> list[Path]:
    return sorted(INTEGRATION.rglob("*.py"))


def test_config_flow_results_pass_step_id() -> None:
    """Newer Core warns when custom flows omit the current step ID."""
    failures: list[str] = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in FLOW_RESULT_METHODS:
                continue
            if any(keyword.arg == "step_id" for keyword in node.keywords):
                continue
            failures.append(f"{path.relative_to(INTEGRATION)}:{node.lineno} " f"{node.func.attr}() without step_id")

    assert not failures, "\n".join(failures)


def test_device_info_does_not_pass_deprecated_via_device_keyword() -> None:
    """Use a parent device ID when a DeviceInfo parent link is needed."""
    failures: list[str] = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "DeviceInfo":
                continue
            if any(keyword.arg == "via_device" for keyword in node.keywords):
                failures.append(f"{path.relative_to(INTEGRATION)}:{node.lineno} " "DeviceInfo(via_device=...)")

    assert not failures, "\n".join(failures)


def test_manifest_keeps_the_hub_integration_type() -> None:
    """The manifest must identify this push-based, multi-device integration."""
    manifest_path = INTEGRATION / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["integration_type"] == "hub"
    assert manifest["iot_class"] == "local_push"
