# ESPEasy & RPiEasy P2P (C013) for Home Assistant

**Push-based, local-first bridge between ESPEasy / RPiEasy nodes and Home
Assistant — no MQTT, no cloud, no polling.** Pure UDP via the native C013
peer-to-peer protocol.

Set your nodes to the **C013 (ESPEasy/RPiEasy P2P)** controller and they show
up in Home Assistant on their own. Every value is pushed the moment it changes;
relays can be switched back from Lovelace.

## Features

- **Auto-discovery** — new nodes appear the moment they send their first packet
- **All sensor types** — temperature, humidity, pressure, lux, switches, analog, counters
- **Two-way switching** — toggle relays, GPIOs and PWM from Lovelace
- **Unit-aware** — HA picks the right device class and unit from the plugin ID
- **Persistent state** — last values survive HA restarts
- **Health watchdog** — per-node `last_seen` plus an `available` sensor
- **Bilingual UI** — config flow, options and repairs fully translated DE + EN

## Quick setup

1. **Install** this integration from HACS, then restart Home Assistant.
2. **Add it**: Settings → Devices & Services → **+ Add Integration** → "ESPEasy P2P".
   Pick the UDP port (default `8266`) and confirm.
3. **Point your nodes at HA** — on each ESPEasy / RPiEasy unit, under
   Controllers → Add → **ESPEasy P2P (C013)**:

   | Setting | Value |
   | :--- | :--- |
   | Controller IP | your Home Assistant IP |
   | Controller Port | 8266 |
   | Unit Number | unique 1–255 per node |
   | Enabled | yes |

Within seconds the device shows up under ESPEasy P2P.

## Configuration options

| Option | Default | Description |
| :--- | :--- | :--- |
| UDP port | 8266 | Must match the controller settings on every node |
| HA unit number | 250 | Unit ID Home Assistant uses in its announce packets |
| HA peer name | Home Assistant | Name HA advertises to the C013 mesh |
| Decimal precision | 3 | Decimal places shown for sensor values (0–6) |

> **Networking:** the integration receives UDP **broadcasts** on port 8266.
> This only works when Home Assistant can see them on the LAN — HA OS,
> Supervised, or a container started with **host networking**
> (`--network=host`). In a bridged Docker network broadcasts never arrive.

---

See the full [README on GitHub](https://github.com/chance-konstruktion/ha-espeasy-p2p)
for the data-flow diagram, services reference, switching details and
troubleshooting.
