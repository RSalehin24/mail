"""Verified HTTPS, bounded streaming and one deadline; credentials never enter errors."""

import http.client
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import BinaryIO

from host.s3_signing import S3Request, SigV4
from shared.errors import ServiceError

CHUNK_BYTES = 64 * 1024
REQUEST_SECONDS = 300.0


class S3Response:
    def __init__(
        self, response: http.client.HTTPResponse,
        connection: http.client.HTTPSConnection, deadline: float,
    ) -> None:
        self.response, self.connection, self.deadline = response, connection, deadline

    def require_status(self, expected: int) -> None:
        if self.response.status == 412:
            raise ServiceError("off_host_archive_already_exists", 409)
        if self.response.status != expected:
            raise ServiceError("off_host_http_" + str(self.response.status), 503)

    def copy_to(self, writer: BinaryIO, limit: int) -> None:
        total = 0
        while True:
            bound_socket(self.connection, self.deadline)
            chunk = self.response.read1(CHUNK_BYTES)
            if not chunk:
                return
            total += len(chunk)
            if total > limit:
                raise ServiceError("off_host_response_too_large", 503)
            writer.write(chunk)


def bound_socket(connection: http.client.HTTPSConnection, deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ServiceError("off_host_transfer_timeout", 503)
    if connection.sock is not None:
        connection.sock.settimeout(min(10.0, remaining))


class S3Http:
    def __init__(
        self, signer: SigV4, connect: Callable[[float], http.client.HTTPSConnection],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.signer, self.connect, self.clock = signer, connect, clock

    @contextmanager
    def open(self, request: S3Request, source: BinaryIO | None = None) -> Iterator[S3Response]:
        deadline = time.monotonic() + REQUEST_SECONDS
        connection = self.connect(10.0)
        try:
            self._headers(connection, request, deadline)
            self._send(connection, source, deadline)
            bound_socket(connection, deadline)
            response = connection.getresponse()
            yield S3Response(response, connection, deadline)
        except (OSError, http.client.HTTPException) as error:
            raise ServiceError("off_host_transfer_failed", 503) from error
        finally:
            connection.close()

    def _headers(
        self, connection: http.client.HTTPSConnection, request: S3Request, deadline: float,
    ) -> None:
        stamp = datetime.fromtimestamp(self.clock(), UTC).strftime("%Y%m%dT%H%M%SZ")
        headers = {
            **request.headers, "x-amz-date": stamp, "x-amz-content-sha256": request.payload_sha256,
        }
        headers["authorization"] = self.signer.authorization(replace(request, headers=headers))
        target = request.path + ("?" + request.query if request.query else "")
        connection.putrequest(request.method, target, skip_host=True, skip_accept_encoding=True)
        for name, value in headers.items():
            connection.putheader(name, value)
        connection.endheaders()
        bound_socket(connection, deadline)

    @staticmethod
    def _send(
        connection: http.client.HTTPSConnection, source: BinaryIO | None, deadline: float,
    ) -> None:
        if source is None:
            return
        for chunk in iter(lambda: source.read(CHUNK_BYTES), b""):
            bound_socket(connection, deadline)
            connection.send(chunk)
