# ESPEasy P2P (C013) for Home Assistant

A custom integration that lets Home Assistant act as a peer in the
[ESPEasy P2P (C013) controller](https://github.com/enesbcs/rpieasy/blob/master/_C013_ESPEasyP2P.py)
mesh. Nodes that have C013 enabled broadcast their presence and sensor values
on UDP port `8266` — this integration listens for those broadcasts and exposes
each task value as a Home Assistant sensor.

## Features

- Pure local push, no cloud, no polling
- Auto-discovers nodes and tasks as they announce themselves
- Creates one sensor entity per task value (up to four per task)
- Groups entities by ESPEasy unit as a Home Assistant device

## Installation via HACS

1. In HACS → *Integrations* → ⋮ → *Custom repositories*, add this repo as
   category **Integration**.
2. Install **ESPEasy P2P** and restart Home Assistant.
3. *Settings → Devices & Services → Add Integration → ESPEasy P2P*.
4. Confirm the UDP port (default `8266`). The integration will start listening
   immediately; entities appear as nodes broadcast their tasks.

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
