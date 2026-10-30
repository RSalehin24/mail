"""Bounded DNS comparisons for customer managed direct delivery."""

import ipaddress
import secrets
import socket
import struct
from typing import Any

from backend.dns import _resolver, dns_name
from shared.installation import hostname, site

KINDS = {"A": 1, "MX": 15, "TXT": 16, "PTR": 12}


def query(name: str, kind: str) -> list[str]:
    hostname(name.replace("_", "a"))
    identifier = secrets.randbelow(65536)
    question = b"".join(bytes((len(label),)) + label.encode() for label in name.split("."))
    packet = struct.pack("!6H", identifier, 0x0100, 1, 0, 0, 0)
    packet += question + b"\0" + struct.pack("!HH", KINDS[kind], 1)
    resolver = _resolver()
    with socket.socket(
        socket.AF_INET6 if ":" in resolver else socket.AF_INET, socket.SOCK_DGRAM
    ) as connection:
        connection.settimeout(1)
        connection.connect((resolver, 53))
        connection.send(packet)
        response = connection.recv(8192)
    actual, flags, questions, answers, _, _ = struct.unpack_from("!6H", response)
    if actual != identifier or flags & 0xFA0F != 0x8000 or questions != 1 or answers > 100:
        raise ValueError("Unusable DNS response")
    echoed, position = dns_name(response, 12)
    if echoed != name.lower() or struct.unpack_from("!HH", response, position) != (KINDS[kind], 1):
        raise ValueError("DNS question mismatch")
    position += 4
    result = []
    for _ in range(answers):
        owner, position = dns_name(response, position)
        record_type, category, _, length = struct.unpack_from("!HHIH", response, position)
        position += 10
        data = response[position : position + length]
        if len(data) != length:
            raise ValueError("Truncated DNS response")
        if record_type == KINDS[kind] and category == 1 and owner == name.lower():
            if kind == "A":
                result.append(str(ipaddress.IPv4Address(data)))
            elif kind == "MX":
                result.append(
                    str(struct.unpack_from("!H", data)[0])
                    + " "
                    + dns_name(response, position + 2)[0]
                )
            elif kind == "PTR":
                result.append(dns_name(response, position)[0])
            else:
                offset, text = 0, b""
                while offset < length:
                    size = data[offset]
                    offset += 1
                    if offset + size > length:
                        raise ValueError("Truncated TXT response")
                    text += data[offset : offset + size]
                    offset += size
                result.append(text.decode("ascii"))
        position += length
    return result


def required(values: dict[str, Any]) -> list[dict[str, Any]]:
    config = site()
    records = [
        {"name": config.mail_hostname, "type": "A", "expected": values.get("public_ipv4", "")},
        {"name": config.domain, "type": "MX", "expected": "10 " + config.mail_hostname},
        {"name": config.domain, "type": "TXT", "expected": values.get("spf_value", "")},
        {"name": "_dmarc." + config.domain, "type": "TXT", "expected": "v=DMARC1; p=none"},
        {
            "name": values.get("dkim_name", ""),
            "type": "TXT",
            "expected": values.get("dkim_value", ""),
        },
    ]
    if values.get("public_ipv4"):
        records.append(
            {
                "name": ipaddress.ip_address(values["public_ipv4"]).reverse_pointer,
                "type": "PTR",
                "expected": config.mail_hostname,
            }
        )
    for record in records:
        if not record["name"] or not record["expected"]:
            record.update(state="input_required", published=[])
            continue
        try:
            published = query(record["name"], record["type"])
            record.update(
                published=published,
                state="verified" if record["expected"] in published else "mismatch",
            )
        except (OSError, ValueError, IndexError, struct.error):
            record.update(state="unknown", published=[])
    return records
