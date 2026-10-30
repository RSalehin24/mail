"""Private DNS overrides confined to explicitly marked synthetic simulation tools."""

import ipaddress
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


@contextmanager
def private_dns(configuration: dict[str, Any]) -> Iterator[None]:
    mapping = configuration.get("simulation_dns", {})
    if not mapping:
        yield
        return
    if (
        configuration.get("simulation") is not True
        or configuration.get("scope") != "isolated-two-host-mail-qualification"
        or not isinstance(mapping, dict)
        or not all(
            isinstance(name, str)
            and name.endswith(".test")
            and ipaddress.ip_address(value).is_private
            for name, value in mapping.items()
        )
    ):
        raise ValueError("Private .test DNS overrides require an isolated simulation")
    original = socket.getaddrinfo

    def resolve(host: Any, *args: Any, **kwargs: Any) -> Any:
        name = host.decode() if isinstance(host, bytes) else host
        return original(mapping.get(name, host), *args, **kwargs)

    socket.getaddrinfo = resolve
    try:
        yield
    finally:
        socket.getaddrinfo = original
