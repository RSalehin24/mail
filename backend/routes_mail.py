"""Mailbox routes enforce the capability gate before invoking their helper adapter."""

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request

from backend.authorization import authenticated, recent, services
from backend.requests import CreateMailbox, DeleteMailbox, ResetMailbox
from shared.errors import ServiceError

router = APIRouter(prefix="/api", dependencies=[Depends(authenticated)])


@router.get("/accounts")
async def accounts(request: Request) -> list[dict[str, Any]]:
    application = services(request)
    return await application.io.run(application.mailboxes.inventory)


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


@router.delete("/accounts/{email}", dependencies=[Depends(recent)])
async def delete(email: str, body: DeleteMailbox, request: Request) -> dict[str, Any]:
    application = services(request)
    if not application.settings.permanent_deletion_enabled:
        raise ServiceError("permanent_deletion_disabled", 403)
    request.state.operation_id = body.request_id
    return await application.io.run(lambda: application.mailboxes.delete(email, body.model_dump()))


@router.get("/diagnostics")
async def diagnostics(request: Request) -> dict[str, Any]:
    application = services(request)
    observed, dns, relay = await asyncio.gather(
        _host_observation(request),
        application.io.run(application.dns),
        application.io.run(application.relay.masked),
    )
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
            "permanent_deletion": application.settings.permanent_deletion_enabled,
            "email_otp": application.settings.email_otp_enabled,
        },
    }


async def _host_observation(request: Request) -> dict[str, Any]:
    application = services(request)
    try:
        return await application.io.run(
            lambda: application.mailboxes.helper.call(
                {"operation": "status", "request_id": str(uuid.uuid4())}
            )
        )
    except ServiceError as error:
        return {"state": "unknown", "reason": error.code}
