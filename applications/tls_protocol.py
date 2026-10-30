"""Bind peer identity to the TLS transport, never to caller controlled HTTP headers."""

import asyncio
import ssl

from uvicorn.protocols.http.h11_impl import H11Protocol

from applications.certificates import fingerprint


class MutualTLSProtocol(H11Protocol):
    def connection_made(self, transport: asyncio.Transport) -> None:  # type: ignore[override]
        super().connection_made(transport)
        tls = transport.get_extra_info("ssl_object")
        if not isinstance(tls, (ssl.SSLObject, ssl.SSLSocket)):
            transport.close()
            return
        certificate = tls.getpeercert(binary_form=True)
        if not certificate:
            transport.close()
            return
        # H11Protocol copies this connection-local state into every request scope.
        self.app_state = {**self.app_state, "client_certificate": fingerprint(certificate)}
