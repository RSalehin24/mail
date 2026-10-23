"""Bounded read-only MX observations using the container's configured resolver (RFC 1035)."""

import ipaddress
import secrets
import socket
import struct
import time
from pathlib import Path
from typing import Any

from shared.validation import DOMAIN


def dns_name(packet: bytes, start: int) -> tuple[str, int]:
    labels: list[str] = []
    position, end = start, None
    visited: set[int] = set()
    while position not in visited and len(visited) < 128:
        visited.add(position)
        size = packet[position]
        if size & 0xC0 == 0xC0:
            end = end or position + 2
            position = struct.unpack_from("!H", packet, position)[0] & 0x3FFF
            continue
        if size > 63:
            raise ValueError("Invalid DNS label")
        position += 1
        if size == 0:
            return ".".join(labels).lower(), end or position
        label = packet[position : position + size].decode("ascii")
        if len(label) != size or not all(
            character.isalnum() or character in "-_" for character in label
        ):
            raise ValueError("Invalid DNS hostname")
        labels.append(label)
        position += size
    raise ValueError("Cyclic or excessive DNS compression")


def mx_records(packet: bytes, identifier: int) -> list[str]:
    actual, flags, questions, answers, _, _ = struct.unpack_from("!6H", packet)
    if actual != identifier or flags & 0xFA0F != 0x8000 or questions != 1 or answers > 128:
        raise ValueError("Unusable DNS response")
    name, position = dns_name(packet, 12)
    if name != DOMAIN or struct.unpack_from("!HH", packet, position) != (15, 1):
        raise ValueError("DNS question mismatch")
    position += 4
    records = []
    for _ in range(min(answers, 128)):
        _, position = dns_name(packet, position)
        kind, category, _, length = struct.unpack_from("!HHIH", packet, position)
        position += 10
        if position + length > len(packet):
            raise ValueError("Truncated DNS record")
        if kind == 15 and category == 1 and length >= 3:
            preference = struct.unpack_from("!H", packet, position)[0]
            exchange, _ = dns_name(packet, position + 2)
            records.append((preference, exchange or "."))
        position += length
    return [f"{preference} {exchange}" for preference, exchange in sorted(records)]


def _resolver() -> str:
    for line in Path("/etc/resolv.conf").read_text().splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == "nameserver":
            return str(ipaddress.ip_address(fields[1]))
    raise ValueError("No configured nameserver")


def observe_mx() -> dict[str, Any]:
    observed = {"observed_at": time.time(), "configured_mode": "cloudflare"}
    try:
        resolver, identifier = _resolver(), secrets.randbelow(65536)
        question = b"".join(bytes((len(label),)) + label.encode() for label in DOMAIN.split("."))
        packet = struct.pack("!6H", identifier, 0x0100, 1, 0, 0, 0) + question + b"\0\0\x0f\0\x01"
        family = socket.AF_INET6 if ":" in resolver else socket.AF_INET
        with socket.socket(family, socket.SOCK_DGRAM) as connection:
            connection.settimeout(2)
            connection.connect((resolver, 53))
            connection.send(packet)
            records = mx_records(connection.recv(4096), identifier)
        return {**observed, "state": "verified", "records": records}
    except (OSError, ValueError, IndexError, struct.error):
        return {**observed, "state": "unknown", "reason": "dns_observation_unavailable"}
