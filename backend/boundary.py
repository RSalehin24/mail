"""ASGI limits cover streamed bodies and errors before routing or work admission."""

import asyncio
import json
import logging
import uuid
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.config import Settings
from shared.validation import BODY_LIMIT


class HttpBoundary:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.application, self.settings = app, settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.application(scope, receive, send)
            return
        scope.setdefault("state", {})["request_id"] = str(uuid.uuid4())
        headers = {key.lower(): value for key, value in scope["headers"]}
        expected = urlsplit(self.settings.origin)
        host = headers.get(b"host", b"").decode("ascii", errors="replace")
        if host != expected.netloc or scope.get("scheme") != expected.scheme:
            await self._error(scope, send, (400, "invalid_host"))
            return
        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            if headers.get(b"origin", b"").decode(errors="replace") != self.settings.origin:
                await self._error(scope, send, (403, "invalid_origin"))
                return
            if headers.get(b"content-type", b"").split(b";")[0].lower() != b"application/json":
                await self._error(scope, send, (415, "json_required"))
                return
        try:
            async with asyncio.timeout(self.settings.operation_timeout):
                body = await self._body(receive)
        except TimeoutError:
            await self._error(scope, send, (408, "request_timeout"))
            return
        if body is None:
            await self._error(scope, send, (413, "body_limit_exceeded"))
            return
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        response_started = False

        async def guarded(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                message["headers"] = list(message.get("headers", [])) + self._headers(scope)
            await send(message)

        try:
            await self.application(scope, replay, guarded)
        except Exception as error:
            logging.getLogger(__name__).error(
                "request_failed request_id=%s type=%s",
                scope["state"]["request_id"],
                type(error).__name__,
            )
            if response_started:
                raise
            await self._error(scope, send, (500, "internal_error"))

    async def _body(self, receive: Receive) -> bytes | None:
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return None
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > BODY_LIMIT:
                return None
            body.extend(chunk)
            if not message.get("more_body", False):
                return bytes(body)

    def _headers(self, scope: Scope) -> list[tuple[bytes, bytes]]:
        headers = [
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
            (b"x-request-id", scope["state"]["request_id"].encode()),
            (
                b"content-security-policy",
                b"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                b"script-src 'self'; object-src 'none'; base-uri 'none'; "
                b"frame-ancestors 'none'; form-action 'self'",
            ),
        ]
        if scope["path"].startswith("/api"):
            headers.append((b"cache-control", b"no-store"))
        return headers

    async def _error(self, scope: Scope, send: Send, error: tuple[int, str]) -> None:
        status, code = error
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")] + self._headers(scope),
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": json.dumps(
                    {"error": code, "request_id": scope["state"]["request_id"]}
                ).encode(),
            }
        )
