# ESPEasy P2P (C013) for Home Assistant

<p align="center">
  <img src="logo.png" alt="ESPEasy P2P" width="240">
</p>

[Deutsche Version weiter unten / German version below](#espeasy-p2p-c013-für-home-assistant) · [Changelog](CHANGELOG.md)

> **Version `v.260506`** — sensor readings and switching are reliable now on
> both stock ESPEasy mega and RPiEasy. From this release on the project uses
> **date-based version numbers** (`v.YYMMDD`); they sort chronologically and
> avoid the "what does 0.3.1 even mean" semver guesswork.

**Local-push integration of ESPEasy and RPiEasy nodes into Home Assistant —
no MQTT, no cloud, no polling. Pure UDP via the native C013 peer-to-peer
protocol.**

## Status

| Feature                                | State                                  |
|----------------------------------------|----------------------------------------|
| Auto-discovery of nodes                | ✅ working                             |
| Auto-discovery of tasks / value names  | ✅ working                             |
| Sensor values (push from node → HA)    | ✅ working                             |
| Switch entities (HA → node)            | ✅ working (RPiEasy needs one-time pin map) |

**Sensor values work very reliably. Switching works well with the provided
tools** — GPIO pin mapping for RPiEasy `Output Helper` and per-task
command templates (e.g. `event,<name>` rules) for stock ESPEasy mega.
Switch entities are exposed for tasks whose value is named `State`,
`Output`, `Relay` or `Switch`; see
[Switching limitations](#switching-limitations) below for the workarounds
per firmware.

## How the data flow works

C013 is a transport — all sensor reading and task configuration happens on
the ESP/RPi itself. There are three packet types:

| Type | Purpose                              | When sent                                |
|------|--------------------------------------|------------------------------------------|
| 1    | Node info / "hello, I exist"         | every 30 s + on demand                   |
| 3    | Task definition (name, value names)  | when a peer is newly discovered          |
| 5    | Sensor values (up to 4 floats)       | whenever the task fires (interval/event) |

Home Assistant joins the mesh as a virtual peer (configurable unit number,
default `250`). Entities appear automatically as soon as a node sends its
task config.

## Features

- Pure local push, no cloud, no polling, no MQTT broker
- Auto-discovers nodes and tasks as they announce themselves
- **Active scan**: HA broadcasts itself as a peer at startup and every 30 s,
  and unicasts a hello back to each newly discovered node. Also exposed as
  the manual `espeasy_p2p.scan` service.
- One sensor entity per task value (up to four per task), with up to three
  decimal places of display precision
- Each ESPEasy unit appears as a single HA device with a link to its web UI
- **Node aging** (since `v.260506`): entities go `unavailable` after 120 s
  of silence and recover automatically when traffic resumes — no more
  stale "last value forever" after a node powers off
- **`espeasy_p2p.remove_node`** service to permanently forget a unit
  (removes its HA device and entities) after retiring it or changing its
  unit number
- **Two-step options flow**: pick a switch task from a list and edit just
  its GPIO pin and command-template fields, instead of one giant N×2 form

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
   the box for the C013 controller.

That's it. Within seconds of saving, the task should show up in HA and
start publishing values.

## Switching limitations

Sending commands from HA to ESPEasy via C013 is **not officially supported
by stock ESPEasy mega**. The integration tries two paths in order:

1. **C013 Type-0 P2P packet** — only RPiEasy currently has a receiver for
   this packet type. Stock ESPEasy ignores it.
2. **HTTP `GET /control?cmd=…`** — works on every ESPEasy build, but the
   command has to be one the node actually understands. The integration
   tries `gpio,<pin>,<state>` first (if the GPIO pin was discovered via
   `/json`) and falls back to `<taskname>,<state>`.

### RPiEasy: register the GPIO pin once

RPiEasy's `Output - Output Helper` plugin does **not** expose its GPIO pin
in `/json`. Without that pin the integration falls back to
`<taskname>,<state>`, which RPiEasy answers with HTTP 200 body `False` —
the relay never moves. Since version 2026-05-05 the integration detects
this case and logs:

```
Switch 'pump' on unit 9 has no known GPIO pin and the node
rejected the task-name command. Call service espeasy_p2p.set_gpio_pin
with unit=9 task_name='pump' pin=<bcm-pin> to fix this
permanently.
```

Fix it once via the integration's UI: *Settings → Devices & Services →
ESPEasy P2P → Configure*. You'll see one row per discovered switch task —
fill in the BCM pin and save. Or, equivalently, call the service:

```yaml
service: espeasy_p2p.set_gpio_pin
data:
  unit: 9
  task_name: pump
  pin: 16
```

The mapping is stored in the config entry options and survives restarts.
After that, toggles use `gpio,16,<state>` directly and succeed.

### ESPEasy mega: trigger a relay via a rule (proven working)

Stock ESPEasy mega doesn't accept arbitrary `gpio,…` from outside reliably,
but it *does* react to events. The combo that works in practice:

1. In *Settings → Devices & Services → ESPEasy P2P → Configure*, find the
   row for the relay task (e.g. `Relay1`, value `cmd`) and put a command
   template into the right-hand field:
   ```
   event,key
   ```
2. On the ESPEasy node, under **Tools → Rules**, add:
   ```
   On key do
     GPIO,5,10
     Publish,2,1
     timerSet,2,4
   endon
   ```

Toggling the switch in HA now sends `event,key` over HTTP, the
node's rule fires and pulses GPIO 5. The same pattern works for any
ESPEasy command you want to wrap behind an event.

### Other failure modes

- The node has HTTP authentication enabled (not yet supported).
- The plugin has no write command (e.g. a pure DS18b20 temperature task).
- A `Switch input` plugin reads a GPIO but doesn't write it — same fix:
  set the pin map and the integration will write via `gpio,…`.

If toggling does nothing, the INFO log line shows exactly why:

```
Switch unit=9 task='pump' pin=12 state=1 -> success=False last_cmd='gpio,12,1' http=400 body='Unknown command'
```

The `body=…` part is the node's verbatim response.

You can also test commands manually with the
**`espeasy_p2p.send_command`** service:

```yaml
service: espeasy_p2p.send_command
data:
  unit: 9
  command: gpio,12,1
```

After upgrading, run **`espeasy_p2p.refetch_metadata`** to re-pull
`/json` so newly extracted fields take effect without restarting HA.

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

```yaml
logger:
  default: warning
  logs:
    custom_components.espeasy_p2p: debug
```

### Manually trigger a scan

*Developer Tools → Services → `espeasy_p2p.scan`* sends an immediate
broadcast hello. Reachable nodes will respond within a second or two.

## Limitations

- Only one listener instance (it owns the UDP port).
- Switching works for any plugin that responds to `gpio,<pin>,<state>` or
  to its task name; for RPiEasy `Output Helper` you have to register the
  pin once via `espeasy_p2p.set_gpio_pin` —
  see [Switching limitations](#switching-limitations).
- Node aging: entities go `unavailable` after 120 s of silence and
  recover automatically when traffic resumes. Use the
  `espeasy_p2p.remove_node` service to permanently forget a unit.

---

# ESPEasy P2P (C013) für Home Assistant

**Lokale Push-Integration von ESPEasy- und RPiEasy-Nodes in Home Assistant —
ohne MQTT, ohne Cloud, ohne Polling. Reines UDP über das native
C013-Peer-to-Peer-Protokoll.**

## Status

| Funktion                                  | Stand                                    |
|-------------------------------------------|------------------------------------------|
| Auto-Erkennung von Nodes                  | ✅ funktioniert                          |
| Auto-Erkennung von Tasks / Wertenamen     | ✅ funktioniert                          |
| Sensorwerte (Push vom Node → HA)          | ✅ funktioniert                          |
| Switch-Entities (HA → Node)               | ✅ funktioniert (RPiEasy: Pin einmal mappen) |

Tasks, deren Wert `State`, `Output`, `Relay` oder `Switch` heißt, werden
automatisch als Schalter in HA angelegt. Bei RPiEasy-`Output Helper`
muss der GPIO-Pin einmalig per Service hinterlegt werden, weil RPiEasy
den Pin nicht im `/json` ausliefert (siehe
[Schalt-Einschränkungen](#schalt-einschränkungen)).

## Datenfluss

C013 ist nur das Transport-Protokoll — Sensoren werden auf dem ESP/RPi
selbst ausgelesen, HA hört nur zu. Es gibt drei Pakettypen:

| Typ | Inhalt                                  | Wann gesendet                                |
|-----|-----------------------------------------|----------------------------------------------|
| 1   | Node-Info / "hello, ich bin da"          | alle 30 s + auf Anfrage                      |
| 3   | Task-Definition (Task-Name, Wertenamen) | wenn ein neuer Peer entdeckt wird            |
| 5   | Sensorwerte (bis zu 4 Floats)           | sobald der Task feuert (Intervall / Event)   |

Home Assistant nimmt als virtueller Peer am Mesh teil (frei wählbare
Unit-Nummer, Default `250`). Sobald ein Node seine Task-Konfiguration
sendet, erscheinen die Entities automatisch.

## Features

- Reiner Local Push, kein Cloud-Dienst, kein Polling, kein MQTT-Broker
- Erkennt Nodes und Tasks automatisch
- **Aktiver Scan**: HA broadcastet sich beim Start und alle 30 s als Peer
  und schickt jedem neu entdeckten Node ein Unicast-Hello. Auch manuell
  über den Service `espeasy_p2p.scan` auslösbar.
- Eine Sensor-Entity je Task-Wert (bis zu vier pro Task), Anzeige mit bis
  zu drei Nachkommastellen
- Jede ESPEasy-Unit erscheint als eigenes HA-Gerät mit Link auf die Web-UI
- **Node-Aging** (seit `v.260506`): Entities werden nach 120 s Funkstille
  `unavailable` und kommen automatisch zurück, sobald wieder Pakete
  eintreffen — keine "ewig letzten Werte" mehr nach Stromausfall am Node
- **`espeasy_p2p.remove_node`**-Service, um eine Unit dauerhaft zu
  entfernen (HA-Gerät und Entities verschwinden) — praktisch nach dem
  Stilllegen oder Umnummerieren eines Nodes
- **Zweistufiger Options-Flow**: Switch-Task aus einer Liste wählen und
  gezielt nur dessen GPIO-Pin und Command-Template bearbeiten, statt einer
  riesigen N×2-Maske

## Installation via HACS

1. In HACS → *Integrationen* → ⋮ → *Benutzerdefinierte Repositories*
   dieses Repo als Kategorie **Integration** hinzufügen.
2. **ESPEasy P2P** installieren, Home Assistant neustarten.
3. *Einstellungen → Geräte & Dienste → Integration hinzufügen → ESPEasy
   P2P*.
4. UDP-Port bestätigen (Default `8266`), eine freie Unit-Nummer für HA
   wählen (Default `250`) und einen Peer-Namen vergeben.

## Konfiguration auf dem ESP / RPi

Für jeden Node, den du auslesen willst:

1. **Controllers → Add → C013 ESPEasy P2P Networking** → aktivieren. Port
   auf `8266` setzen (muss zu HA passen).
2. **Tools → Advanced → Unit Number**: jeder Node bekommt eine *eindeutige*
   Nummer zwischen 1 und 254 (HA nutzt 250 — eine andere wählen).
3. Bei jedem Task, dessen Werte in HA landen sollen:
   **Devices → Task editieren → Data Acquisition → Send to Controller** →
   den C013-Controller anhaken.

Innerhalb weniger Sekunden taucht der Task in HA auf und sendet Werte.

## Schalt-Einschränkungen

Befehle von HA an ESPEasy über C013 zu senden ist **vom Standard-ESPEasy
mega offiziell nicht unterstützt**. Die Integration probiert zwei Wege
nacheinander:

1. **C013 Type-0 P2P-Paket** — nur RPiEasy hat aktuell einen Empfänger
   dafür. Stock ESPEasy ignoriert es.
2. **HTTP `GET /control?cmd=…`** — funktioniert auf jeder
   ESPEasy-Firmware, der Befehl muss aber einer sein, den der Node auch
   versteht. Die Integration versucht zuerst `gpio,<pin>,<state>` (sofern
   der GPIO-Pin über `/json` ermittelt wurde) und fällt dann auf
   `<taskname>,<state>` zurück.

### RPiEasy: GPIO-Pin einmalig registrieren

Das RPiEasy-Plugin `Output - Output Helper` legt seinen GPIO-Pin **nicht**
im `/json` offen. Ohne diesen Pin fällt die Integration auf
`<taskname>,<state>` zurück — RPiEasy antwortet darauf mit HTTP 200 und
Body `False` und schaltet das Relais nicht. Seit Version 2026-05-05
erkennt die Integration das und loggt:

```
Switch 'pump' on unit 9 has no known GPIO pin and the node
rejected the task-name command. Call service espeasy_p2p.set_gpio_pin
with unit=9 task_name='pump' pin=<bcm-pin> to fix this
permanently.
```

Einmalig fixen über die UI: *Einstellungen → Geräte & Dienste → ESPEasy
P2P → Konfigurieren*. Es erscheint eine Zeile pro erkanntem Switch-Task —
BCM-Pin eintragen und speichern. Alternativ über den Service:

```yaml
service: espeasy_p2p.set_gpio_pin
data:
  unit: 9
  task_name: pump
  pin: 16
```

Das Mapping wird in den Optionen des Config Entries gespeichert und
übersteht Neustarts. Danach werden Toggles direkt als `gpio,16,<state>`
gesendet und funktionieren.

### ESPEasy mega: Relais über eine Rule schalten (erprobt)

Stock ESPEasy mega nimmt `gpio,…` von außen nicht zuverlässig an, reagiert
aber auf Events. Die in der Praxis funktionierende Kombi:

1. In *Einstellungen → Geräte & Dienste → ESPEasy P2P → Konfigurieren* in
   der Zeile des Relay-Tasks (z. B. `Relay1`, Wert `cmd`) im rechten Feld
   ein Command-Template eintragen:
   ```
   event,key
   ```
2. Auf dem ESPEasy-Node unter **Tools → Rules**:
   ```
   On schluessel do
     GPIO,5,10
     Publish,2,1
     timerSet,2,4
   endon
   ```

Beim Schalten in HA wird `event,key` per HTTP an den Node geschickt,
die Rule feuert und pulst GPIO 5. Das Schema funktioniert für jeden
ESPEasy-Befehl, den man hinter einem Event verstecken will.

### Weitere Fehlerquellen

- Der Node hat HTTP-Auth aktiv (noch nicht unterstützt).
- Das Plugin hat keinen Schreib-Befehl (z. B. reiner DS18b20-Task).
- Ein `Switch input`-Plugin *liest* einen GPIO, schreibt aber nicht — gleiche
  Lösung: Pin-Map setzen, dann schreibt die Integration via `gpio,…`.

Wenn ein Toggle nichts bewirkt, zeigt die INFO-Log-Zeile genau warum
(Debug muss dafür *nicht* an sein):

```
Switch unit=9 task='pump' pin=12 state=1 -> success=False last_cmd='gpio,12,1' http=400 body='Unknown command'
```

Das `body=…` zeigt dir genau, warum der Node abweist.

Befehle kannst du auch manuell mit dem Service
**`espeasy_p2p.send_command`** testen:

```yaml
service: espeasy_p2p.send_command
data:
  unit: 9
  command: gpio,12,1
```

Nach einem Update der Integration einmal **`espeasy_p2p.refetch_metadata`**
aufrufen, dann werden die `/json`-Daten neu eingelesen (z. B. um die
GPIO-Pin-Erkennung zu aktivieren), ohne HA neu zu starten.

## Troubleshooting

### Home Assistant läuft in Docker und sieht nichts

UDP-Broadcasts (`255.255.255.255`) gehen **nicht** über Docker-Bridge-
Netzwerke. Den Container mit `network_mode: host` starten, HAOS /
Supervised verwenden oder ein UDP-Relay (z. B.
`udp-broadcast-relay-redux`) zwischen Container-Netz und LAN setzen.

### Der Node zeigt "HomeAssistant" als Peer, HA zeigt aber keine Entities

Heißt: Die Announce kommt beim Node an, Typ-3/Typ-5-Pakete kommen aber
nicht zurück. Häufigste Ursachen:

- Der Task hat *Send to Controller → C013* nicht angehakt.
- Zwei Nodes haben dieselbe Unit-Nummer → sie überschreiben sich
  gegenseitig in der Peer-Tabelle.
- Ein managed Switch / VLAN blockiert das Broadcast-Subnetz.

### Debug-Logging aktivieren

```yaml
logger:
  default: warning
  logs:
    custom_components.espeasy_p2p: debug
```

### Scan manuell auslösen

*Entwicklerwerkzeuge → Dienste → `espeasy_p2p.scan`* schickt sofort einen
Broadcast-Hello. Erreichbare Nodes antworten in 1–2 Sekunden.

## Einschränkungen

- Nur eine Listener-Instanz (sie hält den UDP-Port).
- Schalten funktioniert für jedes Plugin, das `gpio,<pin>,<state>` oder
  seinen Task-Namen versteht; bei RPiEasy `Output Helper` muss der Pin
  einmalig via `espeasy_p2p.set_gpio_pin` registriert werden — siehe
  [Schalt-Einschränkungen](#schalt-einschränkungen).
- Node-Aging: Entities werden nach 120 s ohne Pakete `unavailable` und
  kommen automatisch wieder, sobald wieder Traffic eintrifft. Mit dem
  Service `espeasy_p2p.remove_node` kann eine Unit dauerhaft entfernt
  werden.
