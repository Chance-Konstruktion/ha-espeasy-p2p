# ESPEasy P2P (C013) for Home Assistant

A custom integration that lets Home Assistant act as a peer in the
[ESPEasy P2P (C013) controller](https://github.com/enesbcs/rpieasy/blob/master/_C013_ESPEasyP2P.py)
mesh. Nodes that have C013 enabled broadcast their presence and sensor values
on UDP port `8266` — this integration listens for those broadcasts and exposes
each task value as a Home Assistant sensor.

## Features

- Pure local push, no cloud, no polling
- Auto-discovers nodes and tasks as they announce themselves
- **Active scan**: HA broadcasts itself as a peer at startup and every 30 s,
  which triggers all reachable ESPEasy nodes to announce themselves
  immediately instead of waiting up to 30 s. Also exposed as a manual
  `espeasy_p2p.scan` service.
- Creates one sensor entity per task value (up to four per task)
- Groups entities by ESPEasy unit as a Home Assistant device

## Installation via HACS

1. In HACS → *Integrations* → ⋮ → *Custom repositories*, add this repo as
   category **Integration**.
2. Install **ESPEasy P2P** and restart Home Assistant.
3. *Settings → Devices & Services → Add Integration → ESPEasy P2P*.
4. Confirm the UDP port (default `8266`), pick a free unit number for HA
   (default `250`) and a peer name. The integration will broadcast a scan
   immediately; entities appear as nodes respond.

## Notes

- The host running Home Assistant must be on the same broadcast domain as
  the ESPEasy nodes.
- Only one listener instance can be configured (it owns the UDP port).
- The integration currently only consumes data; sending commands back to
  nodes is not implemented yet.

## Protocol reference

| Type | Purpose                | Direction |
|------|------------------------|-----------|
| 1    | Node info / heartbeat  | broadcast |
| 3    | Sensor task config     | broadcast |
| 5    | Sensor values          | broadcast |
