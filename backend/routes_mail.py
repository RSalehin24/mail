"""Mailbox routes enforce the capability gate before invoking their helper adapter."""

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request

from backend.authorization import authenticated, recent, services
from backend.requests import CreateMailbox, DeleteMailbox, MailboxMetadata, ResetMailbox
from backend.services import Services
from shared.errors import ServiceError

router = APIRouter(prefix="/api", dependencies=[Depends(authenticated)])


def deletion_enabled(application: Services) -> bool:
    if application.settings.application_kind == "management":
        with application.authentication.database.transaction() as connection:
            return bool(
                json.loads(
                    connection.execute("SELECT settings FROM application_state").fetchone()[0]
                ).get("permanent_deletion_enabled")
            )
    return application.settings.permanent_deletion_enabled


@router.get("/accounts")
async def accounts(request: Request) -> list[dict[str, Any]]:
    application = services(request)
    return await application.io.run(application.mailboxes.inventory)


@router.get("/accounts/{email}")
async def account(email: str, request: Request) -> dict[str, Any]:
    application = services(request)
    return await application.io.run(lambda: application.mailboxes.details(email))


@router.post("/accounts/create")
async def create(body: CreateMailbox, request: Request) -> dict[str, Any]:
    application = services(request)
    request.state.operation_id = body.request_id
    return await application.io.run(lambda: application.mailboxes.create(body.model_dump()))


@router.post("/accounts/{email}/password", dependencies=[Depends(recent)])
async def reset(email: str, body: ResetMailbox, request: Request) -> dict[str, Any]:
    application = services(request)
    request.state.operation_id = body.request_id
    return await application.io.run(lambda: application.mailboxes.reset(email, body.model_dump()))


@router.patch("/accounts/{email}/metadata", dependencies=[Depends(recent)])
async def metadata(email: str, body: MailboxMetadata, request: Request) -> dict[str, str]:
    application = services(request)
    return await application.io.run(
        lambda: application.mailboxes.update_metadata(email, body.model_dump()),
    )


@router.delete("/accounts/{email}", dependencies=[Depends(recent)])
async def delete(email: str, body: DeleteMailbox, request: Request) -> dict[str, Any]:
    application = services(request)
    enabled = await application.io.run(lambda: deletion_enabled(application))
    if not enabled:
        raise ServiceError("permanent_deletion_disabled", 403)
    request.state.operation_id = body.request_id
    return await application.io.run(lambda: application.mailboxes.delete(email, body.model_dump()))


@router.get("/diagnostics")
async def diagnostics(request: Request) -> dict[str, Any]:
    application = services(request)

    def observe() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        return (_read_host(request, "status"), application.dns(), application.relay.masked())

    observed, dns, relay = await application.io.run(observe)
    return {
        "host": observed,
        "dns_mx": dns,
        "relay": {
            "state": "delivery_confirmed"
            if relay["otp_ready"]
            else "configured_unverified"
            if relay["configured"]
            else "not_configured"
        },
        "capabilities": {
            "permanent_deletion": await application.io.run(lambda: deletion_enabled(application)),
            "email_otp": await application.io.run(application.authentication.email_available),
        },
    }


@router.get("/telemetry")
async def telemetry(request: Request) -> dict[str, Any]:
    return await _host_observation(request, "telemetry")


async def _host_observation(request: Request, operation: str = "status") -> dict[str, Any]:
    application = services(request)
    return await application.io.run(lambda: _read_host(request, operation))


def _read_host(request: Request, operation: str) -> dict[str, Any]:
    try:
        return services(request).mailboxes.helper.call(
            {"operation": operation, "request_id": str(uuid.uuid4())}
        )
    except ServiceError as error:
        return {"state": "unknown", "reason": error.code}
