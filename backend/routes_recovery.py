"""Session-protected recovery controls; all mutations require recent authentication."""

import base64
import os
import uuid
from typing import Any, Literal

from argon2.low_level import Type, hash_secret_raw
from fastapi import APIRouter, Depends, Request
from pydantic import Field

from backend.authorization import authenticated, recent, services
from backend.requests import RequestModel
from shared.errors import ServiceError
from shared.validation import address, mailbox_password, request_id

router = APIRouter(prefix="/api/recovery", dependencies=[Depends(authenticated)])


class RecoveryDelete(RequestModel):
    request_id: str = Field(max_length=36)
    email: str = Field(max_length=256)
    confirmation: str = Field(max_length=256)
    acknowledged: bool
    retention: Literal["7_days", "30_days", "forever", "none"]
    policy_revision: int
    permanent: bool = False


class RecoveryRestore(RequestModel):
    request_id: str = Field(max_length=36)
    confirmation: str = Field(max_length=256)
    acknowledged: bool
    password: str = Field(max_length=72)


class RecoveryRemove(RequestModel):
    request_id: str = Field(max_length=36)
    confirmation: str = Field(max_length=256)
    acknowledged: bool


class PolicyValue(RequestModel):
    schema_version: Literal[1]
    revision: int
    enabled: bool
    permanent_deletion_enabled: bool
    default_retention: Literal["7_days", "30_days", "forever", "none"]
    protected: dict[str, str]


class RecoverySave(RequestModel):
    policy: PolicyValue
    acknowledged: bool


async def call(
    request: Request, operation: str, values: dict[str, Any], identifier: str | None = None
) -> dict[str, Any]:
    application = services(request)
    identifier = request_id(identifier) if identifier else str(uuid.uuid4())
    request.state.operation_id = identifier
    return await application.io.run(
        lambda: application.mailboxes.helper.call(
            {
                "operation": operation,
                "request_id": identifier,
                **values,
            }
        )
    )


@router.get("/policy")
async def policy(request: Request) -> dict[str, Any]:
    return await call(request, "recovery_policy", {})


@router.put("/policy", dependencies=[Depends(recent)])
async def save_policy(body: RecoverySave, request: Request) -> dict[str, Any]:
    return await call(request, "recovery_save_policy", body.model_dump())


@router.get("/mailboxes")
async def listing(
    request: Request, cursor: str = "", page: int | None = None, page_size: int = 10
) -> dict[str, Any]:
    if page is not None:
        if page < 1 or page_size not in {5, 10, 20, 50}:
            raise ServiceError("invalid_pagination", 400)
        return await call(request, "recovery_pages", {"page": page, "page_size": page_size})
    return await call(request, "recovery_list", {"cursor": cursor})


@router.get("/operations/{identifier}")
async def outcome(identifier: str, request: Request) -> dict[str, Any]:
    return await call(request, "recovery_outcome", {"job_id": request_id(identifier)})


@router.post("/delete", dependencies=[Depends(recent)])
async def delete(body: RecoveryDelete, request: Request) -> dict[str, Any]:
    application = services(request)
    values = body.model_dump()
    if body.retention == "none":
        values["permanent"] = True
    try:
        existing = await call(request, "recovery_outcome", {"job_id": body.request_id})
        if (
            existing["operation"] != "recovery_delete"
            or existing["email"] != body.email
            or body.confirmation != body.email
            or not body.acknowledged
            or existing["selection"]
            != {key: values[key] for key in ("retention", "permanent", "policy_revision")}
        ):
            raise ServiceError("request_id_conflict", 409)
        return {key: existing[key] for key in ("protocol", "request_id", "state", "error")}
    except ServiceError as error:
        if error.code != "operation_not_found":
            raise
    mailbox = await application.io.run(lambda: application.mailboxes.details(address(body.email)))
    values["metadata"] = {key: mailbox[key] for key in ("display_name", "notes", "created")}
    identifier = values.pop("request_id")
    return await call(request, "recovery_delete", values, identifier)


@router.post("/mailboxes/{identifier}/restore", dependencies=[Depends(recent)])
async def restore(identifier: str, body: RecoveryRestore, request: Request) -> dict[str, Any]:
    password = mailbox_password(body.password)

    # A fresh Maddy-compatible Argon2id hash crosses the root boundary. The new
    # plaintext password is never written to a job, database, log or archive.
    def password_hash() -> str:
        salt = os.urandom(16)
        value = hash_secret_raw(
            password.encode(),
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
            + (base64.b64encode(value).decode())
        )

    application = services(request)
    hashed = await application.io.run(password_hash)
    return await call(
        request,
        "recovery_restore",
        {
            "record_id": request_id(identifier),
            "confirmation": body.confirmation,
            "acknowledged": body.acknowledged,
            "password_hash": hashed,
        },
        body.request_id,
    )


@router.post("/mailboxes/{identifier}/remove", dependencies=[Depends(recent)])
async def remove(identifier: str, body: RecoveryRemove, request: Request) -> dict[str, Any]:
    return await call(
        request,
        "recovery_remove",
        {
            "record_id": request_id(identifier),
            "confirmation": body.confirmation,
            "acknowledged": body.acknowledged,
        },
        body.request_id,
    )


@router.post("/mailboxes/{identifier}/reconcile", dependencies=[Depends(recent)])
async def reconcile(identifier: str, body: RecoveryRemove, request: Request) -> dict[str, Any]:
    return await call(
        request,
        "recovery_reconcile",
        {
            "record_id": request_id(identifier),
            "confirmation": body.confirmation,
            "acknowledged": body.acknowledged,
        },
        body.request_id,
    )
