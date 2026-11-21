"""Read the TLS certificate a service presents; Maddy issues and renews the certificates."""

import hashlib
import socket
import ssl
import time
from pathlib import Path
from typing import Any

from shared.errors import ServiceError
from shared.validation import MAIL_HOST


def served_certificate(
    endpoint: tuple[str, int], trust: Path | None = None
) -> dict[str, Any]:
    context = ssl.create_default_context(cafile=str(trust) if trust else None)
    with socket.create_connection(endpoint, timeout=5) as connection:
        with context.wrap_socket(connection, server_hostname=MAIL_HOST) as secured:
            certificate = secured.getpeercert(binary_form=True)
            if certificate is None:
                raise ServiceError("served_certificate_missing", 503)
            parsed = secured.getpeercert()
            expiry = parsed.get("notAfter") if parsed else None
            if not isinstance(expiry, str):
                raise ServiceError("served_certificate_expiry_missing", 503)
            return {
                "served_sha256": hashlib.sha256(certificate).hexdigest(),
                "expiry": ssl.cert_time_to_seconds(expiry),
            }


def served_fingerprint(endpoint: tuple[str, int], trust: Path | None = None) -> str:
    return str(served_certificate(endpoint, trust)["served_sha256"])


def verify_served(endpoint: tuple[str, int], expected: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if served_fingerprint(endpoint) == expected:
                return
        except (OSError, ServiceError) as error:
            last_error = error
        time.sleep(0.1)
    raise ServiceError("served_generation_mismatch", 503) from last_error
