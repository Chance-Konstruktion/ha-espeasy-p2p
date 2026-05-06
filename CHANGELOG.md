# Changelog

All notable changes to this integration are documented here. Dates use
`YYYY-MM-DD`. Starting with `260506`, the integration uses **date-based
version numbers** (`YYMMDD`) instead of semver — they sort chronologically
and the release date is obvious at a glance.

## 260506

### Added
- Documented the proven **`event,<name>` + ESPEasy rule** pattern for
  switching relays from HA on stock ESPEasy mega. See README.

### Fixed
- Options-flow description triggered `Translation error: UNCLOSED_TAG` in
  the HA frontend because it contained `<pin>` / `<state>` (parsed as
  HTML tags). Replaced with safe placeholders and `{state}` is now passed
  as a literal description placeholder.

### Changed
- Switched to date-based version numbers (`YYMMDD`).

## [Unreleased]

### Added
- **Options flow for the GPIO pin map**: edit pin assignments via
  *Settings → Devices & Services → ESPEasy P2P → Configure*. One row per
  discovered switch task; clearing a row removes the override.
- **Per-task command template**: a second optional field next to each
  pin lets you send arbitrary commands like `event,door={state}` for
  Output Helper / Domoticz Helper tasks that don't drive a GPIO directly.
  `{state}` is replaced with `0` or `1`. When set, the template
  overrides the default `gpio,…` / `<taskname>,…` dispatch.

### Fixed
- Options flow returned 500 Internal Server Error because the schema
  used `vol.Any(...)` which HA's JSON encoder cannot serialize. Replaced
  with `NumberSelector`.

## 2026-05-05

### Added
- **`espeasy_p2p.set_gpio_pin` service**: persists a
  `(unit, task_name) → BCM pin` mapping in the config entry options.
  Required for RPiEasy `Output - Output Helper` tasks, whose pin is not
  exposed in `/json`. Survives restarts.
- Sensor entities now expose **device class and unit** (temperature °C,
  humidity %, pressure hPa) based on the value name and plugin `Type`.

### Changed
- Switch toggling now uses **strict success detection**: HTTP 200 with
  body `False` / `0` / empty / `Unknown command` is treated as failure.
  No optimistic state update on failure — the HA toggle reflects what
  the node actually did.
- When a toggle fails because no GPIO pin is known, the INFO log line is
  followed by a warning that names the exact `set_gpio_pin` call needed
  to fix it permanently.

### Fixed
- Pumps on RPiEasy `Output Helper` no longer appear to toggle in HA
  while the relay stays put — root cause was RPiEasy silently rejecting
  `<taskname>,<state>` with body `False`.

## 2026-05-03

### Added
- `espeasy_p2p.send_command` service to send arbitrary commands to a node
  via P2P + HTTP `/control`, with the response logged at INFO.
- `espeasy_p2p.refetch_metadata` service to re-pull `/json` from every
  known node without restarting HA.
- Bilingual (EN/DE) README with honest feature status table.
- GPIO pin extraction from `/json` (`TaskDeviceGPIO1`, `GPIO1`, …);
  switches use `gpio,<pin>,<state>` first when known.

### Changed
- Reduced per-packet log spam; switch attempts log at INFO with
  command/status/body so failures can be diagnosed without enabling debug.

## 2026-05-02

### Added
- Switch platform: tasks whose value is named `State`, `Output`, `Relay`
  or `Switch` are exposed as toggleable switches. Toggles are sent both
  as a C013 Type-0 P2P packet (RPiEasy) and as HTTP `/control?cmd=…`.
- C013 Type-3 / Type-5 / Type-6 sensor data decoding, including handling
  of the broadcast `src_unit=255` case.
- Active discovery: HA broadcasts itself as a peer at startup and every
  30 s, plus an `espeasy_p2p.scan` service.
- Per-task value sensor entities (up to 4 per task) with 3-decimal
  display precision; phantom unit-0 entities suppressed.
- Fallback: pull task and value names from the node's `/json` HTTP
  endpoint so entities still appear if a node never sends Type-3.

### Changed
- HA announces itself with C013 node type `33` (ESP Easy32) for maximum
  RPiEasy compatibility — earlier types were silently dropped.

## 2026-05-01

### Added
- Initial release: UDP listener on port 8266, node auto-discovery,
  HACS-installable custom integration.
