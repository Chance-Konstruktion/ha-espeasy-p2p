"""ESPEasy P2P (C013) UDP protocol decoder.

Packet layouts are derived from the rpieasy reference implementation:
https://github.com/enesbcs/rpieasy/blob/master/_C013_ESPEasyP2P.py
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
from dataclasses import dataclass, field
from typing import Callable

from .const import (
    NODE_TYPE_NAMES,
    PACKET_HEADER,
    PACKET_TYPE_INFO,
    PACKET_TYPE_SENSOR_CONFIG,
    PACKET_TYPE_SENSOR_DATA,
)

_LOGGER = logging.getLogger(__name__)

# Type 1 - Node info: header,type,mac(6),ip(4),unit,build(uint16),name(25s),node_type,web_port(uint16)
INFO_STRUCT = struct.Struct("<BB6B4BBH25sBH")

# Type 5 - Sensor data: header,type,src_unit,dst_unit,src_task,dst_task,2 reserved bytes, 4 floats
SENSOR_DATA_STRUCT = struct.Struct("<BBBBBBBB4f")


def _decode_string(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()


@dataclass
class NodeInfo:
    unit: int
    name: str
    ip: str
    mac: str
    build: int
    node_type: int
    web_port: int

    @property
    def node_type_name(self) -> str:
        return NODE_TYPE_NAMES.get(self.node_type, f"Type {self.node_type}")


@dataclass
class TaskConfig:
    src_unit: int
    task_index: int
    device_number: int
    task_name: str
    value_names: list[str]


@dataclass
class TaskValues:
    src_unit: int
    task_index: int
    values: list[float]


@dataclass
class _State:
    nodes: dict[int, NodeInfo] = field(default_factory=dict)
    tasks: dict[tuple[int, int], TaskConfig] = field(default_factory=dict)


class ESPEasyP2PProtocol(asyncio.DatagramProtocol):
    """Asyncio protocol that decodes incoming ESPEasy P2P UDP packets."""

    def __init__(
        self,
        on_node: Callable[[NodeInfo], None],
        on_task: Callable[[TaskConfig], None],
        on_values: Callable[[TaskValues], None],
    ) -> None:
        self._on_node = on_node
        self._on_task = on_task
        self._on_values = on_values
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:  # type: ignore[override]
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) < 2 or data[0] != PACKET_HEADER:
            _LOGGER.debug(
                "Ignoring non-C013 UDP packet from %s (len=%d, first=%r)",
                addr, len(data), data[:4],
            )
            return
        ptype = data[1]
        _LOGGER.debug(
            "RX C013 type=%d from %s len=%d", ptype, addr[0], len(data)
        )
        try:
            if ptype == PACKET_TYPE_INFO:
                self._handle_info(data, addr[0])
            elif ptype == PACKET_TYPE_SENSOR_CONFIG:
                self._handle_sensor_config(data)
            elif ptype == PACKET_TYPE_SENSOR_DATA:
                self._handle_sensor_data(data)
            else:
                _LOGGER.debug("Unhandled C013 packet type %d from %s", ptype, addr)
        except (struct.error, ValueError) as err:
            _LOGGER.debug("Bad ESPEasy P2P packet from %s: %s", addr, err)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("UDP error: %s", exc)

    def _handle_info(self, data: bytes, src_ip: str) -> None:
        if len(data) < INFO_STRUCT.size:
            return
        fields = INFO_STRUCT.unpack_from(data)
        mac = ":".join(f"{b:02x}" for b in fields[2:8])
        ip = ".".join(str(b) for b in fields[8:12])
        unit = fields[12]
        build = fields[13]
        name = _decode_string(fields[14])
        node_type = fields[15]
        web_port = fields[16]
        if not ip or ip == "0.0.0.0":
            ip = src_ip
        self._on_node(
            NodeInfo(
                unit=unit,
                name=name or f"unit-{unit}",
                ip=ip,
                mac=mac,
                build=build,
                node_type=node_type,
                web_port=web_port,
            )
        )

    def _handle_sensor_config(self, data: bytes) -> None:
        # Layout: 2 header bytes + 5 routing bytes (src_unit, dst_unit, src_task,
        # dst_task, device_number) followed by null-padded ASCII fields. The
        # reference implementation uses 25- or 26-byte string slots for the
        # task name and 4 value names. To be tolerant of both, we slice the
        # remaining payload into equal chunks rather than relying on a fixed
        # struct size.
        header_len = 7
        if len(data) <= header_len:
            return
        src_unit = data[2]
        task_index = data[4]
        device_number = data[6]
        payload = data[header_len:]
        # Expect 5 strings (taskname + 4 value names). Compute slot width.
        slot_count = 5
        slot = max(1, len(payload) // slot_count)
        strings = [
            _decode_string(payload[i * slot : (i + 1) * slot])
            for i in range(slot_count)
        ]
        task_name = strings[0]
        value_names = strings[1:5]
        self._on_task(
            TaskConfig(
                src_unit=src_unit,
                task_index=task_index,
                device_number=device_number,
                task_name=task_name,
                value_names=value_names,
            )
        )

    def _handle_sensor_data(self, data: bytes) -> None:
        if len(data) < SENSOR_DATA_STRUCT.size:
            return
        fields = SENSOR_DATA_STRUCT.unpack_from(data)
        src_unit = fields[2]
        task_index = fields[4]
        values = list(fields[8:12])
        self._on_values(
            TaskValues(src_unit=src_unit, task_index=task_index, values=values)
        )


def build_info_packet(
    unit: int, name: str, ip: str, web_port: int, build: int = 1, node_type: int = 5
) -> bytes:
    """Build a Type-1 (Node Info) packet announcing HA as a virtual peer.

    Sending this as a broadcast triggers ESPEasy nodes to immediately
    announce themselves and their tasks back, instead of waiting for the
    next periodic broadcast (~30 s).
    """
    try:
        ip_bytes = bytes(int(p) for p in ip.split("."))
        if len(ip_bytes) != 4:
            ip_bytes = b"\x00\x00\x00\x00"
    except ValueError:
        ip_bytes = b"\x00\x00\x00\x00"
    name_bytes = name.encode("utf-8", errors="replace")[:25].ljust(25, b"\x00")
    return INFO_STRUCT.pack(
        PACKET_HEADER,
        PACKET_TYPE_INFO,
        0, 0, 0, 0, 0, 0,  # MAC unknown - zeroed
        ip_bytes[0], ip_bytes[1], ip_bytes[2], ip_bytes[3],
        unit & 0xFF,
        build & 0xFFFF,
        name_bytes,
        node_type & 0xFF,
        web_port & 0xFFFF,
    )


def detect_local_ip() -> str:
    """Best-effort detection of the local LAN IP that broadcasts will originate from."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # The address does not need to be reachable; this just forces the
        # kernel to pick the outbound interface and tells us its address.
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return "0.0.0.0"
    finally:
        sock.close()


async def create_listener(
    loop: asyncio.AbstractEventLoop,
    port: int,
    protocol_factory: Callable[[], ESPEasyP2PProtocol],
) -> tuple[asyncio.DatagramTransport, ESPEasyP2PProtocol]:
    """Create a UDP socket bound to the broadcast port and return transport+protocol."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    except OSError:
        pass
    sock.bind(("0.0.0.0", port))
    sock.setblocking(False)
    transport, protocol = await loop.create_datagram_endpoint(
        protocol_factory, sock=sock
    )
    return transport, protocol  # type: ignore[return-value]
