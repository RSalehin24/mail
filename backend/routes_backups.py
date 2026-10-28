"""Authenticated backup catalog and asynchronous root-controlled recovery jobs."""

import base64
import os
from typing import Any, Literal

from argon2.low_level import Type, hash_secret_raw
from fastapi import APIRouter, Depends, Request
from pydantic import Field

from backend.authorization import authenticated, recent
from backend.requests import RequestModel
from backend.routes_recovery import call
from shared.validation import address, mailbox_password

router = APIRouter(prefix="/api/backups", dependencies=[Depends(authenticated)])


class BackupSubmit(RequestModel):
    request_id: str = Field(max_length=36)
    kind: Literal["refresh", "backup", "preflight", "restore", "reconcile"]
    snapshot: str = Field(default="", max_length=64)
    previous: str = Field(default="", max_length=36)
    confirmation: str = Field(default="", max_length=64)
    acknowledged: bool
    passwords: dict[str, str] = Field(default_factory=dict, max_length=32)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    value = hash_secret_raw(
        mailbox_password(password).encode(),
        salt,
        time_cost=3,
        memory_cost=19456,
        parallelism=1,
        hash_len=64,
        type=Type.ID,
    )
    return (
        "argon2:3:19456:1:"
        + base64.b64encode(salt).decode()
        + ":"
        + base64.b64encode(value).decode()
    )


@router.get("")
async def listing(
    request: Request,
    page: int = 1,
    page_size: int = 10,
    kind: Literal["snapshots", "operations"] = "snapshots",
) -> dict[str, Any]:
    return await call(request, "backup_list", {"page": page, "page_size": page_size, "kind": kind})


@router.get("/operations/{identifier}")
async def outcome(identifier: str, request: Request) -> dict[str, Any]:
    return await call(request, "backup_outcome", {"job_id": identifier})


@router.post("/operations", dependencies=[Depends(recent)])
async def submit(body: BackupSubmit, request: Request) -> dict[str, Any]:
    from backend.authorization import services

    application = services(request)
    # Response-loss retries reuse the original operation without writing fresh hashes.
    from shared.errors import ServiceError

    try:
        existing = await call(request, "backup_outcome", {"job_id": body.request_id})
        if (
            existing["kind"] != body.kind
            or existing["snapshot"] != body.snapshot
            or not body.acknowledged
            or (body.kind in {"preflight", "restore"} and body.confirmation != body.snapshot)
        ):
            raise ServiceError("request_id_conflict", 409)
        return existing
    except ServiceError as error:
        if error.code != "backup_operation_not_found":
            raise
    values = body.model_dump(exclude={"request_id", "passwords"})
    values["password_hashes"] = await application.io.run(
        lambda: {
            address(email): hash_password(password) for email, password in body.passwords.items()
        }
    )
    return await call(request, "backup_submit", values, body.request_id)
