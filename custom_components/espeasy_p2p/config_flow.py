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
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
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


_SAVE_CHOICE = "__save__"


class ESPEasyP2POptionsFlow(OptionsFlow):
    """Edit GPIO-pin and command-template overrides per task.

    Two-step flow: pick a task from the list, edit its pin / template, then
    optionally pick another or save & close. Much friendlier than one giant
    form with N×2 generic-looking fields.
    """

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._pins: dict[str, int] = dict(entry.options.get(CONF_GPIO_PIN_MAP, {}))
        self._cmds: dict[str, str] = dict(entry.options.get(CONF_COMMAND_MAP, {}))
        self._editing: str | None = None

    def _switch_tasks(self) -> list[tuple[int, str]]:
        coordinator = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
        out: list[tuple[int, str]] = []
        if coordinator is not None:
            for (unit, _idx), task in coordinator.tasks.items():
                if not task.task_name:
                    continue
                if not any(
                    (v or "").strip().lower() in SWITCH_VALUE_NAMES
                    for v in task.value_names
                ):
                    continue
                out.append((unit, task.task_name))
        out.sort()
        return out

    def _all_keys(self) -> list[str]:
        """Discovered tasks plus orphan keys that still live in options."""
        seen = {f"{u}/{n}" for u, n in self._switch_tasks()}
        return sorted(seen | set(self._pins) | set(self._cmds))

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        keys = self._all_keys()
        if not keys:
            return self.async_abort(reason="no_switch_tasks")

        if user_input is not None:
            choice = user_input.get("task")
            if choice == _SAVE_CHOICE or not choice:
                return self._save_and_close()
            self._editing = choice
            return await self.async_step_edit()

        options: list[SelectOptionDict] = []
        for key in keys:
            unit_str, _, task_name = key.partition("/")
            label = f"Unit {unit_str} · {task_name}"
            extras: list[str] = []
            pin = self._pins.get(key)
            if pin is not None:
                extras.append(f"pin {pin}")
            cmd = self._cmds.get(key)
            if cmd:
                extras.append(f"cmd: {cmd}")
            if extras:
                label += "  —  " + ", ".join(extras)
            options.append(SelectOptionDict(value=key, label=label))
        options.append(
            SelectOptionDict(value=_SAVE_CHOICE, label="Save and close")
        )

        schema = vol.Schema(
            {
                vol.Required("task"): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        mode=SelectSelectorMode.LIST,
                        custom_value=False,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={"task_count": str(len(keys))},
        )

    async def async_step_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        key = self._editing
        if key is None:
            return await self.async_step_init()
        unit_str, _, task_name = key.partition("/")

        if user_input is not None:
            if user_input.get("remove"):
                self._pins.pop(key, None)
                self._cmds.pop(key, None)
            else:
                pin_raw = user_input.get("pin")
                if pin_raw is None or pin_raw == "":
                    self._pins.pop(key, None)
                else:
                    try:
                        self._pins[key] = int(pin_raw)
                    except (TypeError, ValueError):
                        pass
                cmd_raw = (user_input.get("cmd") or "").strip()
                if cmd_raw:
                    self._cmds[key] = cmd_raw
                else:
                    self._cmds.pop(key, None)
            self._editing = None
            return await self.async_step_init()

        pin_default = self._pins.get(key)
        cmd_default = self._cmds.get(key, "")
        schema_dict: dict[Any, Any] = {}
        schema_dict[
            vol.Optional(
                "pin",
                description={"suggested_value": pin_default}
                if pin_default is not None
                else None,
            )
        ] = NumberSelector(
            NumberSelectorConfig(min=0, max=40, step=1, mode=NumberSelectorMode.BOX)
        )
        schema_dict[
            vol.Optional(
                "cmd",
                description={"suggested_value": cmd_default}
                if cmd_default
                else None,
            )
        ] = TextSelector(TextSelectorConfig(multiline=False))
        schema_dict[vol.Optional("remove", default=False)] = BooleanSelector()

        return self.async_show_form(
            step_id="edit",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "task": task_name,
                "unit": unit_str,
                "state": "{state}",
            },
        )

    def _save_and_close(self) -> ConfigFlowResult:
        coordinator = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
        if coordinator is not None:
            coordinator.pin_overrides = dict(self._pins)
            coordinator.command_overrides = dict(self._cmds)
        return self.async_create_entry(
            title="",
            data={
                CONF_GPIO_PIN_MAP: dict(self._pins),
                CONF_COMMAND_MAP: dict(self._cmds),
            },
        )
