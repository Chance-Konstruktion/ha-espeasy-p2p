"""Constants for the ESPEasy P2P integration."""

from __future__ import annotations

DOMAIN = "espeasy_p2p"

CONF_PORT = "port"
CONF_UNIT = "unit"
CONF_NAME = "name"

DEFAULT_PORT = 8266
DEFAULT_UNIT = 250
DEFAULT_NAME = "Home Assistant"

SERVICE_SCAN = "scan"
SERVICE_SEND_COMMAND = "send_command"
SERVICE_REFETCH_METADATA = "refetch_metadata"
SERVICE_SET_GPIO_PIN = "set_gpio_pin"

CONF_GPIO_PIN_MAP = "gpio_pin_map"
CONF_COMMAND_MAP = "command_map"

PACKET_HEADER = 255
PACKET_TYPE_INFO = 1
PACKET_TYPE_SENSOR_CONFIG = 3
PACKET_TYPE_SENSOR_DATA = 5
# Newer ESPEasy mega builds use type 6 for sensor data (extended payload).
# Decoder treats it the same as type 5 — first 4 floats after the 8-byte
# routing header. Trailing bytes (per-value sensor type, etc.) are ignored.
PACKET_TYPE_SENSOR_DATA_EXT = 6
PACKET_TYPE_COMMAND = 0

# Source unit value used by some firmware versions when the packet is meant
# as a broadcast rather than addressed from a specific unit. We resolve the
# real sender via the UDP source IP in that case.
BROADCAST_UNIT = 255

NODE_TIMEOUT_CYCLES = 10
ANNOUNCE_INTERVAL = 30

SIGNAL_NODE_DISCOVERED = f"{DOMAIN}_node_discovered"
SIGNAL_TASK_DISCOVERED = f"{DOMAIN}_task_discovered"
SIGNAL_VALUE_UPDATED = f"{DOMAIN}_value_updated"

NODE_TYPE_NAMES = {
    1: "ESP Easy",
    5: "RPiEasy",
    17: "ESPEasy Mega (ESP8266)",
    33: "ESPEasy Mega (ESP32)",
    65: "Arduino Easy",
    81: "Nano Easy",
    97: "ATmega LoRa",
}

SWITCH_VALUE_NAMES = frozenset({"state", "output", "relay", "switch"})

# Node-type byte we use when announcing HA on the C013 mesh.
#
# C013 has no official "Home Assistant" type, and RPiEasy strictly rejects
# any type byte not in its hard-coded accept list (1, 5, 17, 33, 65, 81, 97).
# An earlier attempt with type 66 worked on ESPEasy firmware but was silently
# dropped by RPiEasy. Type 33 (ESP Easy32) is accepted by every known firmware
# and is the most neutral label — peer UIs will display "ESP Easy32" instead
# of "RPi Easy".
HA_NODE_TYPE = 33

# Build number HA reports on the wire. The C013 build field is 16 bits
# (max 65535), so we encode it as YYMMDD without the century prefix:
# 2026-05-02 -> 26502.
HA_BUILD = 26502
# Human-readable version shown in HA's own device info / log lines.
HA_VERSION = "20260502"
