"""Authenticated management of root-owned forward-copy rules."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from backend.authorization import authenticated, recent, services
from backend.requests import RequestModel

router = APIRouter(prefix="/api/forwarding", dependencies=[Depends(authenticated)])


class ForwardingSave(RequestModel):
    email: str = Field(max_length=256)
    destination: str = Field(max_length=254)
    enabled: bool
    revision: int


class ForwardingRemove(RequestModel):
    email: str = Field(max_length=256)
    revision: int


class ForwardingRetry(RequestModel):
    email: str = Field(max_length=256)
    acknowledged: bool


async def call(request: Request, operation: str, values: dict[str, Any]) -> dict[str, Any]:
    application = services(request)
    return await application.io.run(
        lambda: application.mailboxes.helper.call(
            {"operation": operation, "request_id": str(uuid.uuid4()), **values}
        )
    )


@router.get("")
async def listing(request: Request) -> dict[str, Any]:
    return await call(request, "forwarding_list", {})


@router.put("", dependencies=[Depends(recent)])
async def save(body: ForwardingSave, request: Request) -> dict[str, Any]:
    return await call(request, "forwarding_save", body.model_dump())


@router.post("/remove", dependencies=[Depends(recent)])
async def remove(body: ForwardingRemove, request: Request) -> dict[str, Any]:
    return await call(request, "forwarding_remove", body.model_dump())


@router.post("/retry", dependencies=[Depends(recent)])
async def retry(body: ForwardingRetry, request: Request) -> dict[str, Any]:
    return await call(request, "forwarding_retry", body.model_dump())


class ForwardingVerification(RequestModel):
    email: str = Field(max_length=256)


@router.post("/verify", dependencies=[Depends(recent)])
async def verify(body: ForwardingVerification, request: Request) -> dict[str, Any]:
    return await call(request, "forwarding_verify", body.model_dump())


@router.post("/verify-local", dependencies=[Depends(recent)])
async def verify_local(body: ForwardingVerification, request: Request) -> dict[str, Any]:
    return await call(request, "forwarding_verify_local", body.model_dump())
