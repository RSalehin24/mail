"""Version 1 integration listener. It exposes no mailbox content or general host access."""

import asyncio
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import Field
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from applications import CAPABILITIES, INTEGRATION_VERSION, VERSION
from applications.identity import Identities
from applications.pairing import PairingAuthority
from backend.mailboxes import Mailboxes
from backend.requests import RequestModel
from backend.webmail_imap import ImapTransport
from shared.errors import ServiceError
from shared.installation import site
from shared.validation import address, mailbox_password, request_id


class IntegrationBoundary:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.capacity = asyncio.Semaphore(12)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if self.capacity.locked():
            await JSONResponse({"error": "integration_busy"}, 503)(scope, receive, send)
            return
        async with self.capacity:
            if scope.get("scheme") != "https" or not scope.get("state", {}).get(
                "client_certificate"
            ):
                await JSONResponse({"error": "mutual_tls_required"}, 403)(scope, receive, send)
                return
            data = bytearray()
            try:
                async with asyncio.timeout(3):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        data.extend(message.get("body", b""))
                        if len(data) > 65536:
                            await JSONResponse({"error": "integration_body_limit"}, 413)(
                                scope, receive, send
                            )
                            return
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                await JSONResponse({"error": "integration_request_timeout"}, 408)(
                    scope, receive, send
                )
                return
            delivered, started = False, False

            async def replay() -> Message:
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(data), "more_body": False}
                return await receive()

            async def guarded(message: Message) -> None:
                nonlocal started
                if message["type"] == "http.response.start":
                    started = True
                    message["headers"] = list(message.get("headers", [])) + [
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                    ]
                await send(message)

            try:
                async with asyncio.timeout(15):
                    await self.app(scope, replay, guarded)
            except TimeoutError:
                if not started:
                    await JSONResponse({"error": "integration_request_timeout"}, 504)(
                        scope, receive, send
                    )


class EnrollmentRequest(RequestModel):
    api_version: int
    capabilities: list[str] = Field(max_length=20)
    installation_id: str = Field(max_length=36)
    token: str = Field(max_length=128)
    csr: str = Field(max_length=8192)


class IdentityRequest(RequestModel):
    email: str = Field(max_length=254)


class PasswordRequest(IdentityRequest):
    mailbox_id: str = Field(max_length=32)
    revision: int = Field(ge=0)
    request_id: str = Field(max_length=36)
    password: str = Field(max_length=1024)
    new_password: str = Field(max_length=72)


class RotationRequest(RequestModel):
    csr: str = Field(max_length=8192)


def create_integration_app(
    authority: PairingAuthority,
    identities: Identities,
    mailboxes: Mailboxes,
    imap: ImapTransport | None = None,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(IntegrationBoundary)

    @app.exception_handler(ServiceError)
    async def error(request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse({"error": exc.code}, exc.status, headers={"Cache-Control": "no-store"})

    def certificate(request: Request) -> str:
        return str(request.state.client_certificate)

    def credential(request: Request) -> dict[str, Any]:
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise ServiceError("integration_authentication_required", 401)
        return authority.authenticate(header[7:], certificate(request))

    def active(request: Request) -> dict[str, Any]:
        value = credential(request)
        if not value["activated"]:
            raise ServiceError("activate_rotated_credential", 409)
        if request.headers.get("x-mail-integration-version") != str(INTEGRATION_VERSION):
            raise ServiceError("integration_upgrade_required", 409)
        return value

    @app.post("/integration/v1/enroll")
    def enroll(body: EnrollmentRequest, request: Request) -> dict[str, Any]:
        return authority.enroll(body.model_dump(), certificate(request))

    @app.get("/integration/v1/status")
    def status(pair: Annotated[dict[str, Any], Depends(active)]) -> dict[str, Any]:
        return {
            "api_version": INTEGRATION_VERSION,
            "capabilities": list(CAPABILITIES),
            "version": VERSION,
            "domain": site().domain,
            "management_id": authority.database.identity,
            "credential_id": pair["id"],
        }

    @app.post("/integration/v1/identity")
    def identity(
        body: IdentityRequest, pair: Annotated[dict[str, Any], Depends(active)]
    ) -> dict[str, Any]:
        return identities.lookup(address(body.email))

    @app.post("/integration/v1/password")
    def password(
        body: PasswordRequest, pair: Annotated[dict[str, Any], Depends(active)]
    ) -> dict[str, Any]:
        normalized = address(body.email)
        with identities.guard(normalized):
            return change_password(body, normalized)

    def change_password(body: PasswordRequest, normalized: str) -> dict[str, Any]:
        before = identities.lookup(normalized)
        if before["id"] != body.mailbox_id or before["revision"] != body.revision:
            raise ServiceError("mailbox_security_revision_changed", 409)
        # Service credentials alone never authorize password replacement.
        with (imap or ImapTransport()).connect(normalized, body.password) as mail:
            mail.select("INBOX", before["inbox_validity"])
        after = identities.lookup(normalized)
        if after != before:
            raise ServiceError("mailbox_security_revision_changed", 409)
        return mailboxes.reset(
            normalized,
            {
                "request_id": request_id(body.request_id),
                "password": mailbox_password(body.new_password),
            },
            expected_inbox_validity=before["inbox_validity"],
            expected_security_revision=(before["id"], before["revision"]),
        )

    @app.post("/integration/v1/credentials/rotate")
    def rotate(
        body: RotationRequest, pair: Annotated[dict[str, Any], Depends(active)]
    ) -> dict[str, Any]:
        return authority.rotate(pair, body.csr)

    @app.post("/integration/v1/credentials/activate")
    def activate(pair: Annotated[dict[str, Any], Depends(credential)]) -> dict[str, bool]:
        authority.activate(pair)
        return {"activated": True}

    from applications.trash import TrashAuthority, TrashBatch

    trash = TrashAuthority(authority.database, identities)

    @app.post("/integration/v1/trash")
    def events(
        body: TrashBatch, pair: Annotated[dict[str, Any], Depends(active)]
    ) -> dict[str, Any]:
        return trash.receive(pair["installation_id"], body)

    return app
