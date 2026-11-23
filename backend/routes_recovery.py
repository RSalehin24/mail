"""Session-protected recovery controls: deletion is direct, restoring is an owner command."""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from backend.authorization import authenticated, confirmed, recent, services
from backend.requests import RequestModel
from shared.errors import ServiceError
from shared.validation import address, request_id

router = APIRouter(prefix="/api/recovery", dependencies=[Depends(authenticated)])


class RecoveryDelete(RequestModel):
    request_id: str = Field(max_length=36)
    email: str = Field(max_length=256)
    confirmation: str = Field(max_length=256)
    acknowledged: bool
    retention: Literal["7_days", "30_days", "forever", "none"]
    policy_revision: int
    permanent: bool = False


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


@router.post("/delete", dependencies=[Depends(confirmed)])
async def delete(body: RecoveryDelete, request: Request) -> dict[str, Any]:
    application = services(request)
    values = body.model_dump()
    if body.retention == "none":
        values["permanent"] = True
    try:
        mailbox = await application.io.run(
            lambda: application.mailboxes.details(address(body.email)))
    except ServiceError as error:
        if error.code != "account_not_found":
            raise
        # Already deleted (a repeated request) or absent: the helper decides, from its records.
        mailbox = {"display_name": "", "notes": "", "created": 1}
    values["metadata"] = {key: mailbox[key] for key in ("display_name", "notes", "created")}
    identifier = values.pop("request_id")
    return await call(request, "recovery_delete", values, identifier)
