"""Bounded Unix socket listener with Linux peer credentials and stable parent directory."""

import json
import logging
import os
import socket
import socketserver
import stat
import struct
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from helper.protocol import HelperRequest
from helper.service import HostHelper
from shared.errors import ServiceError
from shared.validation import HELPER_LIMIT


@dataclass(frozen=True)
class SocketPolicy:
    service_uid: int
    service_gid: int
    timeout: float
    concurrency: int = 4


class HelperHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server = cast(HelperServer, self.server)
        self.connection.settimeout(server.policy.timeout)
        try:
            if not server.authorized(self.connection):
                raise ServiceError("helper_access_denied", 403)
            payload = self.rfile.readline(HELPER_LIMIT + 1)
            if len(payload) > HELPER_LIMIT or not payload.endswith(b"\n"):
                raise ServiceError("helper_request_limit", 413)
            record = json.loads(payload)
            if not isinstance(record, dict):
                raise ServiceError("invalid_helper_request", 400)
            response = {"ok": True, **server.helper.handle(HelperRequest.parse(record))}
        except ServiceError as error:
            response = {"ok": False, "error": error.code, "status": error.status}
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            response = {"ok": False, "error": "helper_unavailable", "status": 503}
        try:
            self.wfile.write(json.dumps(response).encode() + b"\n")
        except OSError:
            # Completion is already durable; a disconnected caller cannot undo or replay it.
            logging.getLogger(__name__).warning("helper_response_lost")


class HelperServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 4

    def __init__(self, path: Path, dependencies: tuple[HostHelper, SocketPolicy]) -> None:
        self.helper, self.policy = dependencies
        self.capacity = threading.BoundedSemaphore(self.policy.concurrency)
        metadata = path.parent.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != self.policy.service_gid
            or stat.S_IMODE(metadata.st_mode) != 0o750
        ):
            raise ServiceError("unsafe_socket_directory", 503)
        if path.exists():
            previous = path.lstat()
            if not stat.S_ISSOCK(previous.st_mode) or previous.st_uid != 0:
                raise ServiceError("unsafe_socket_path", 503)
            path.unlink()
        super().__init__(str(path), HelperHandler)
        os.chown(path, 0, self.policy.service_gid)
        os.chmod(path, 0o660)

    def authorized(self, connection: socket.socket) -> bool:
        credential_option = getattr(socket, "SO_PEERCRED", None)
        if credential_option is None:
            raise ServiceError("linux_peer_credentials_required", 503)
        credentials = connection.getsockopt(
            socket.SOL_SOCKET, credential_option, struct.calcsize("3i")
        )
        _, uid, _ = struct.unpack("3i", credentials)
        return uid in {0, self.policy.service_uid}

    def process_request(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: Any
    ) -> None:
        if not self.capacity.acquire(blocking=False):
            if isinstance(request, socket.socket):
                request.sendall(b'{"ok":false,"error":"helper_overloaded","status":503}\n')
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: Any
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.capacity.release()
