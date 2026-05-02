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

PACKET_HEADER = 255
PACKET_TYPE_INFO = 1
PACKET_TYPE_SENSOR_CONFIG = 3
PACKET_TYPE_SENSOR_DATA = 5
PACKET_TYPE_COMMAND = 0

NODE_TIMEOUT_CYCLES = 10
ANNOUNCE_INTERVAL = 30

SIGNAL_NODE_DISCOVERED = f"{DOMAIN}_node_discovered"
SIGNAL_TASK_DISCOVERED = f"{DOMAIN}_task_discovered"
SIGNAL_VALUE_UPDATED = f"{DOMAIN}_value_updated"

NODE_TYPE_NAMES = {
    1: "ESP Easy",
    5: "RPiEasy",
    17: "ESP Easy-M",
    33: "ESP Easy32",
    65: "Arduino Easy",
    81: "Nano Easy",
    97: "ATmega LoRa",
}
