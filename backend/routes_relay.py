"""All relay edits, sends, confirmations and reveals require recent authentication."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from backend.authorization import authenticated, peer, recent, services
from backend.requests import RequestModel
from shared.errors import ServiceError
from shared.validation import MAIL_HOST, OTP_SENDER, address

router = APIRouter(prefix="/api", dependencies=[Depends(authenticated)])


class RelaySettingsRequest(RequestModel):
    host: Literal["smtp-relay.brevo.com"]
    port: Literal[587, 465]
    tls_mode: Literal["starttls", "tls"]
    username: str = Field(max_length=256)
    credential: str | None = Field(default=None, max_length=4096)
    api_key: str | None = Field(default=None, max_length=4096)


class RelayTestRequest(RequestModel):
    mode: Literal["connection", "delivery"]


@router.get("/settings/relay")
async def masked(request: Request) -> dict[str, Any]:
    application = services(request)
    return await application.io.run(application.relay.masked)


@router.post("/settings/relay", dependencies=[Depends(recent)])
async def save(body: RelaySettingsRequest, request: Request) -> dict[str, Any]:
    application = services(request)
    return await application.io.run(lambda: application.relay.save(body.model_dump()))


@router.get("/settings/relay/quota")
async def quota(request: Request) -> dict[str, Any]:
    application = services(request)
    return await application.io.run(application.relay.quota)


@router.post("/settings/relay/test", dependencies=[Depends(recent)])
async def test(body: RelayTestRequest, request: Request) -> dict[str, Any]:
    application = services(request)
    return await application.io.run(
        lambda: application.relay.test(OTP_SENDER, (body.mode, peer(request)))
    )


@router.post("/relay-tests/{test_id}/confirm", dependencies=[Depends(recent)])
async def confirm(test_id: str, request: Request) -> dict[str, bool]:
    application = services(request)
    return await application.io.run(lambda: application.relay.confirm(test_id))


@router.post("/settings/relay/reveal", dependencies=[Depends(recent)])
async def reveal(request: Request) -> dict[str, str]:
    application = services(request)
    credential = await application.io.run(application.relay.reveal)
    return {
        "credential": credential,
        "scope": "Shared Brevo key; may authorize other sender identities.",
    }


@router.post("/accounts/{email}/test-sender", dependencies=[Depends(recent)])
async def sender(email: str, request: Request) -> dict[str, Any]:
    application = services(request)
    normalized = await application.io.run(lambda: application.mailboxes.existing(email))
    return await application.io.run(
        lambda: application.relay.test(normalized, ("delivery", peer(request)))
    )


@router.get("/accounts/{email}/config")
async def client_configuration(email: str, request: Request) -> dict[str, Any]:
    application = services(request)
    normalized = await application.io.run(lambda: application.mailboxes.existing(email))
    return {
        "incoming": {
            "host": MAIL_HOST,
            "port": 993,
            "security": "TLS",
            "username": normalized,
            "password": "Use your known account password or reset it; hashes cannot be recovered.",
        },
        "outgoing": {
            "host": MAIL_HOST,
            "port": 587,
            "security": "STARTTLS",
            "username": normalized,
            "password": "Use the same mailbox password as IMAP.",
        },
    }


@router.get("/relay-tests")
async def history(
    request: Request, sender: str, page: int = 1, page_size: int = 10
) -> dict[str, Any]:
    normalized = address(sender)
    if page < 1 or page_size not in {5, 10, 20, 50}:
        raise ServiceError("invalid_pagination", 400)
    application = services(request)

    def read() -> dict[str, Any]:
        with application.relay.database.transaction() as connection:
            total = connection.execute(
                "SELECT count(*) FROM relay_tests WHERE sender=?", (normalized,)
            ).fetchone()[0]
            current = min(page, max(1, (total + page_size - 1) // page_size))
            rows = connection.execute(
                "SELECT id,revision,sender,connection_ok,accepted,confirmed,created "
                "FROM relay_tests WHERE sender=? ORDER BY created DESC,id DESC LIMIT ? OFFSET ?",
                (normalized, page_size, (current - 1) * page_size),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total, "page": current}

    return await application.io.run(read)
