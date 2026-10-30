"""ASGI limits cover streamed bodies and errors before routing or work admission."""

import asyncio
import json
import logging
import uuid
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.config import Settings
from shared.errors import ServiceError
from shared.validation import BODY_LIMIT

SEARCH_DEADLINE_SECONDS = 9.5


class HttpBoundary:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.application, self.settings = app, settings
        self.capacity = asyncio.Semaphore(settings.http_concurrency)
        self.mail_capacity = asyncio.Semaphore(12)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.application(scope, receive, send)
            return
        is_mail = scope["path"].startswith(("/api/webmail", "/api/profile"))
        capacity = self.mail_capacity if is_mail else self.capacity
        if capacity.locked():
            scope.setdefault("state", {})["request_id"] = str(uuid.uuid4())
            await self._error(scope, send, (503, "work_overloaded"))
            return
        async with capacity:
            await self._admitted(scope, receive, send)

    async def _admitted(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.application(scope, receive, send)
            return
        scope.setdefault("state", {})["request_id"] = str(uuid.uuid4())
        headers = {key.lower(): value for key, value in scope["headers"]}
        upload = scope["path"] == "/api/webmail/uploads" and scope["method"] == "PUT"
        expected = urlsplit(self.settings.origin)
        host = headers.get(b"host", b"").decode("ascii", errors="replace")
        if host != expected.netloc or scope.get("scheme") != expected.scheme:
            await self._error(scope, send, (400, "invalid_host"))
            return
        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            if headers.get(b"origin", b"").decode(errors="replace") != self.settings.origin:
                await self._error(scope, send, (403, "invalid_origin"))
                return
            required_type = b"application/octet-stream" if upload else b"application/json"
            if headers.get(b"content-type", b"").split(b";")[0].lower() != required_type:
                await self._error(scope, send, (415, "json_required"))
                return
        try:
            async with asyncio.timeout(self.settings.operation_timeout):
                limit = 1024 * 1024 if scope["path"].startswith("/api/webmail/") else BODY_LIMIT
                body = b"" if upload else await self._body(receive, limit)
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

        streamed = 0

        async def upload_receive() -> Message:
            nonlocal streamed
            message = await receive()
            streamed += len(message.get("body", b""))
            if streamed > 10 * 1024 * 1024:
                raise ServiceError("attachment_limit_exceeded", 413)
            return message

        response_started = False

        async def guarded(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                message["headers"] = list(message.get("headers", [])) + self._headers(scope)
            await send(message)

        try:
            if upload:
                async with asyncio.timeout(30):
                    await self.application(scope, upload_receive, guarded)
            elif scope["path"] == "/api/webmail/messages/search":
                async with asyncio.timeout(SEARCH_DEADLINE_SECONDS):
                    await self.application(scope, replay, guarded)
            else:
                await self.application(scope, replay, guarded)
        except TimeoutError:
            if not response_started:
                error_response = (
                    (504, "search_timeout")
                    if scope["path"] == ("/api/webmail/messages/search")
                    else (408, "request_timeout")
                )
                await self._error(scope, send, error_response)
        except Exception as error:
            logging.getLogger(__name__).error(
                "request_failed request_id=%s type=%s",
                scope["state"]["request_id"],
                type(error).__name__,
            )
            if response_started:
                raise
            await self._error(scope, send, (500, "internal_error"))

    async def _body(self, receive: Receive, limit: int = BODY_LIMIT) -> bytes | None:
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return None
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > limit:
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
                b"default-src 'self'; img-src 'self' data: https: http:; "
                b"style-src 'self' 'unsafe-inline'; "
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
                "headers": [(b"content-type", b"application/json")]
                + self._headers(scope)
                + ([(b"retry-after", b"1")] if status == 503 else []),
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
