"""Config flow for ESPEasy P2P."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_COMMAND_MAP,
    CONF_GPIO_PIN_MAP,
    CONF_NAME,
    CONF_PORT,
    CONF_UNIT,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_UNIT,
    DOMAIN,
    SWITCH_VALUE_NAMES,
)


class ESPEasyP2PConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ESPEasy P2P."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="ESPEasy P2P",
                data={
                    CONF_PORT: user_input[CONF_PORT],
                    CONF_UNIT: user_input[CONF_UNIT],
                    CONF_NAME: user_input[CONF_NAME],
                },
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
                    int, vol.Range(min=1, max=65535)
                ),
                vol.Required(CONF_UNIT, default=DEFAULT_UNIT): vol.All(
                    int, vol.Range(min=1, max=255)
                ),
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return ESPEasyP2POptionsFlow(entry)


# Form fields are named "<task_name>__u<unit>" for the GPIO pin and
# "<task_name>__u<unit>__cmd" for the command template, so the HA UI shows
# readable labels and we can still round-trip them back to the
# "<unit>/<task_name>" keys stored in the options dict.
_FIELD_SEP = "__u"
_CMD_SUFFIX = "__cmd"


def _pin_field_for(unit: int, task_name: str) -> str:
    return f"{task_name}{_FIELD_SEP}{unit}"


def _cmd_field_for(unit: int, task_name: str) -> str:
    return f"{task_name}{_FIELD_SEP}{unit}{_CMD_SUFFIX}"


def _parse_field(field: str) -> tuple[int, str, bool] | None:
    """Return (unit, task_name, is_cmd) or None if the field is unrelated."""
    is_cmd = field.endswith(_CMD_SUFFIX)
    base = field[: -len(_CMD_SUFFIX)] if is_cmd else field
    idx = base.rfind(_FIELD_SEP)
    if idx < 0:
        return None
    task_name = base[:idx]
    try:
        unit = int(base[idx + len(_FIELD_SEP) :])
    except ValueError:
        return None
    return unit, task_name, is_cmd


class ESPEasyP2POptionsFlow(OptionsFlow):
    """Edit the GPIO-pin map for switch-eligible tasks."""

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        coordinator = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
        switch_tasks: list[tuple[int, str]] = []
        if coordinator is not None:
            for (unit, _idx), task in coordinator.tasks.items():
                if not task.task_name:
                    continue
                if not any(
                    (v or "").strip().lower() in SWITCH_VALUE_NAMES
                    for v in task.value_names
                ):
                    continue
                switch_tasks.append((unit, task.task_name))
        switch_tasks.sort()

        current_pins: dict[str, int] = dict(
            self._entry.options.get(CONF_GPIO_PIN_MAP, {})
        )
        current_cmds: dict[str, str] = dict(
            self._entry.options.get(CONF_COMMAND_MAP, {})
        )

        if user_input is not None:
            new_pins: dict[str, int] = {}
            new_cmds: dict[str, str] = {}
            for field, value in user_input.items():
                parsed = _parse_field(field)
                if parsed is None or value is None:
                    continue
                unit, task_name, is_cmd = parsed
                key = f"{unit}/{task_name}"
                if is_cmd:
                    template = str(value).strip()
                    if not template:
                        continue
                    new_cmds[key] = template
                    if coordinator is not None:
                        coordinator.set_command_override(unit, task_name, template)
                else:
                    try:
                        pin = int(value)
                    except (TypeError, ValueError):
                        continue
                    if pin < 0:
                        continue
                    new_pins[key] = pin
                    if coordinator is not None:
                        coordinator.set_pin_override(unit, task_name, pin)
            # Replace the live override dicts wholesale so cleared fields
            # take effect immediately, not just after a restart.
            if coordinator is not None:
                coordinator.pin_overrides = dict(new_pins)
                coordinator.command_overrides = dict(new_cmds)
            return self.async_create_entry(
                title="",
                data={
                    CONF_GPIO_PIN_MAP: new_pins,
                    CONF_COMMAND_MAP: new_cmds,
                },
            )

        if not switch_tasks:
            return self.async_abort(reason="no_switch_tasks")

        pin_selector = NumberSelector(
            NumberSelectorConfig(min=0, max=40, step=1, mode=NumberSelectorMode.BOX)
        )
        schema_dict: dict[Any, Any] = {}
        for unit, task_name in switch_tasks:
            key = f"{unit}/{task_name}"
            pin_default = current_pins.get(key)
            cmd_default = current_cmds.get(key)
            schema_dict[
                vol.Optional(
                    _pin_field_for(unit, task_name),
                    description={"suggested_value": pin_default}
                    if pin_default is not None
                    else None,
                )
            ] = pin_selector
            schema_dict[
                vol.Optional(
                    _cmd_field_for(unit, task_name),
                    description={"suggested_value": cmd_default}
                    if cmd_default
                    else None,
                )
            ] = str

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "task_count": str(len(switch_tasks)),
                "state": "{state}",
            },
        )
