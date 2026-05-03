# ESPEasy P2P (C013) for Home Assistant

<p align="center">
  <img src="logo.png" alt="ESPEasy P2P" width="240">
</p>

**Seamless integration of ESPEasy and RPiEasy nodes into Home Assistant —
without MQTT, without cloud, purely local push over the native C013
peer-to-peer protocol.**

A custom HACS integration that lets Home Assistant act as a peer in the
[ESPEasy P2P (C013) controller](https://github.com/enesbcs/rpieasy/blob/master/_C013_ESPEasyP2P.py)
mesh. Nodes that have C013 enabled broadcast their presence, task config and
sensor values on UDP port `8266` — this integration listens, registers each
node as a HA device, and creates one sensor entity per task value.

## How the data flow works

All sensor reading and task configuration happens **on the ESP/RPi itself**.
C013 is purely a transport. There are three packet types:

| Type | Purpose                              | When sent                              |
|------|--------------------------------------|----------------------------------------|
| 1    | Node info / "hello, I exist"         | every 30 s + on demand                 |
| 3    | Task definition (name, value names)  | when a peer is newly discovered        |
| 5    | Sensor values (up to 4 floats)       | whenever the task fires (interval/event) |

Home Assistant joins the mesh as a virtual peer (configurable unit number,
default `250`). It does not assign anything to your nodes — entities appear
automatically as soon as a node sends its task config.

## Features

- Pure local push, no cloud, no polling, no MQTT broker
- Auto-discovers nodes and tasks as they announce themselves
- **Active scan**: HA broadcasts itself as a peer at startup and every 30 s,
  and unicasts a hello back to each newly discovered node so it re-sends its
  task configuration immediately. Also exposed as the manual
  `espeasy_p2p.scan` service.
- One sensor entity per task value (up to four per task), shown with up to
  three decimal places
- Tasks whose value is named `State`, `Output`, `Relay` or `Switch` are
  exposed as toggleable **switch entities** that send
  `GET /control?cmd=<taskname>,<0|1>` to the node
- Each ESPEasy unit appears as a single HA device with a link to its web UI

## Installation via HACS

1. In HACS → *Integrations* → ⋮ → *Custom repositories*, add this repo as
   category **Integration**.
2. Install **ESPEasy P2P** and restart Home Assistant.
3. *Settings → Devices & Services → Add Integration → ESPEasy P2P*.
4. Confirm the UDP port (default `8266`), pick a free unit number for HA
   (default `250`) and a peer name.

## Configuration on the ESP / RPi side

For each node you want to read from:

1. **Controllers → Add → C013 ESPEasy P2P Networking** → enable it. Set the
   port to `8266` (must match HA).
2. **Tools → Advanced → Unit Number**: give every node a *unique* number
   between 1 and 254 (HA defaults to 250 — pick another).
3. For every task whose values you want in HA:
   **Devices → edit task → Data Acquisition → Send to Controller** → tick
   the box for the C013 controller you just added.

That's it. Within seconds of saving, the task should show up in HA and start
publishing values.

## Troubleshooting

### Home Assistant runs in Docker and sees nothing

UDP broadcasts (`255.255.255.255`) do **not** cross Docker bridge networks.
Run the container with `network_mode: host`, or run HAOS / Supervised, or
add a UDP relay (e.g. `udp-broadcast-relay-redux`) between your container
network and your LAN.

### The node shows "HomeAssistant" as a peer but HA shows no entities

That means the announce reaches the node but Type-3 / Type-5 packets are
not coming back to HA. Most common causes:

- The task on the ESP does not have *Send to Controller → C013* ticked.
- Two different nodes share the same unit number → they overwrite each
  other in C013's peer table.
- A managed switch / VLAN is blocking the broadcast subnet.

### Enable debug logging

Add this to `configuration.yaml` and restart:

```yaml
logger:
  default: warning
  logs:
    custom_components.espeasy_p2p: debug
```

You will see lines like:

```
RX C013 type=1 from 192.168.1.42 len=43
Discovered ESPEasy node unit=12 name=kitchen-esp ip=192.168.1.42 ...
Discovered task 0 on unit 12: BME280 (values=['Temperature','Humidity','Pressure',''])
```

If you see only `type=1` and never `type=3`, the node is not configured to
forward its task data — see the configuration section above.

If you see no `RX C013` lines at all, the broadcasts are not reaching HA —
see the Docker note above.

### Manually trigger a scan

*Developer Tools → Services → `espeasy_p2p.scan`* sends an immediate
broadcast hello. Reachable nodes will respond within a second or two.

## Limitations

- Only one listener instance (it owns the UDP port).
- Switch commands go out via the node's HTTP `/control` endpoint, so the
  node must be reachable on its web port from HA.
- No node-timeout / aging logic — entities stay `available` once values
  have been seen.
