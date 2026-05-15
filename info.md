# ESPEasy P2P (C013) for Home Assistant

Local-push integration of ESPEasy and RPiEasy nodes into Home Assistant —
no MQTT, no cloud, no polling. Pure UDP via the native C013 peer-to-peer
protocol.

## Features

- Auto-discovery of nodes, tasks and value names
- Sensor entities with configurable decimal precision (0–6)
- Switch entities for tasks named `State`, `Output`, `Relay` or `Switch`
- Per-task GPIO pin and command-template overrides via the options flow
- Online/offline tracking via heartbeat aging

See the [README](https://github.com/chance-konstruktion/ha-espeasy-p2p) for
full configuration details and firmware-specific notes.
