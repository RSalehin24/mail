"""Mailbox-only routes. Addresses and user IDs are never accepted as ownership inputs."""

import asyncio
import json
import uuid
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from backend.authorization import peer, services, session
from backend.webmail_models import (
    Bulk,
    Compose,
    FactorChange,
    FolderChange,
    LabelChange,
    MessageRef,
    PasswordChange,
    ProfileChange,
    Search,
    SendRequest,
)
from backend.webmail_services import MailServices
from shared.errors import ServiceError
from shared.validation import mailbox_password


def mail_services(request: Request) -> MailServices:
    context = services(request)
    enabled = context.settings.webmail_enabled
    if context.settings.application_kind == "webmail":
        enabled = bool(webmail_configuration(request).get("webmail_enabled"))
    if not enabled or context.webmail is None:
        raise ServiceError("webmail_disabled", 503)
    return context.webmail


def webmail_configuration(request: Request) -> dict[str, Any]:
    cached = getattr(request.state, "webmail_configuration", None)
    if isinstance(cached, dict):
        return cached
    with services(request).authentication.database.transaction() as connection:
        value = json.loads(
            connection.execute("SELECT settings FROM application_state").fetchone()[0]
        )
    request.state.webmail_configuration = value
    return dict(value)


def enabled_mailboxes(request: Request) -> tuple[str, ...]:
    context = services(request)
    if context.settings.application_kind != "webmail":
        return (
            context.settings.webmail_allowed_mailboxes if context.settings.webmail_enabled else ()
        )
    values = webmail_configuration(request)
    return tuple(values.get("allowlist", [])) if values.get("webmail_enabled") else ()


async def mailbox_authenticated(request: Request) -> None:
    application = mail_services(request)
    if not session(request).startswith("wm."):
        # Existing administrator sessions cannot enter mailbox routes.
        if session(request):
            raise ServiceError("mailbox_role_required", 403)
        raise ServiceError("authentication_required", 401)
    user = await asyncio.to_thread(application.auth.require, session(request))
    if user.email not in enabled_mailboxes(request):
        raise ServiceError("webmail_not_enabled_for_mailbox", 403)


router = APIRouter(prefix="/api/webmail", dependencies=[Depends(mailbox_authenticated)])
profile_router = APIRouter(prefix="/api/profile", dependencies=[Depends(mailbox_authenticated)])


@router.get("/folders")
async def folders(request: Request) -> list[dict[str, Any]]:
    app = mail_services(request)
    return await app.pool.run(lambda: app.mail.folders(session(request)))


@router.post("/folders")
async def change_folder(body: FolderChange, request: Request) -> dict[str, bool]:
    app = mail_services(request)
    return await app.pool.run(lambda: app.mail.change_folder(session(request), body))


@router.post("/messages/search")
async def search(body: Search, request: Request) -> dict[str, Any]:
    app = mail_services(request)
    try:
        return await asyncio.wait_for(
            app.pool.run(lambda: app.mail.messages(session(request), body)), 9.5
        )
    except TimeoutError as error:
        raise ServiceError("search_timeout", 504) from error
    except ServiceError as error:
        if error.code in {"webmail_busy", "mail_timeout"}:
            # The search deadline includes waiting for an admitted mailbox worker
            # or its session-owned connection, as well as native SEARCH execution.
            raise ServiceError("search_timeout", 504) from error
        raise


@router.post("/messages/open")
async def message(body: MessageRef, request: Request, remote: bool = False) -> dict[str, Any]:
    app = mail_services(request)
    return await app.pool.run(lambda: app.mail.message(session(request), body, remote))


@router.post("/messages/bulk")
async def bulk(body: Bulk, request: Request) -> dict[str, Any]:
    app = mail_services(request)
    return await app.pool.run(lambda: app.mail.bulk(session(request), body))


@router.get("/labels")
async def labels(request: Request) -> list[dict[str, str]]:
    app = mail_services(request)
    return await app.pool.run(lambda: app.mail.labels(session(request)))


@router.post("/labels")
async def create_label(body: LabelChange, request: Request) -> dict[str, str]:
    app = mail_services(request)
    return await app.pool.run(lambda: app.mail.save_label(session(request), body))


@router.put("/labels/{identifier}")
async def update_label(identifier: str, body: LabelChange, request: Request) -> dict[str, str]:
    app = mail_services(request)
    return await app.pool.run(lambda: app.mail.save_label(session(request), body, identifier))


@router.delete("/labels/{identifier}")
async def delete_label(identifier: str, request: Request) -> dict[str, bool]:
    app = mail_services(request)
    return await app.pool.run(lambda: app.mail.delete_label(session(request), identifier))


@router.put("/uploads")
async def upload(request: Request, filename: str = Query(max_length=256)) -> dict[str, Any]:
    return await mail_services(request).files.upload(session(request), filename, request.stream())


@router.delete("/uploads/{identifier}")
async def delete_upload(identifier: str, request: Request) -> dict[str, bool]:
    app = mail_services(request)
    return await app.pool.run(lambda: app.files.delete(session(request), identifier))


@router.get("/attachments/{part}")
async def attachment(
    part: str,
    request: Request,
    folder: str = Query(max_length=256),
    uidvalidity: int = Query(gt=0),
    uid: int = Query(gt=0),
) -> StreamingResponse:
    app = mail_services(request)
    ref = MessageRef(folder=folder, uidvalidity=uidvalidity, uid=uid)
    path, filename = await app.transfers.run(
        lambda: app.files.download(session(request), ref, part)
    )
    return StreamingResponse(
        app.files.stream(path),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe=""),
            "Content-Length": str(path.stat().st_size),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/drafts")
async def save_draft(body: Compose, request: Request) -> dict[str, Any]:
    app = mail_services(request)
    return await app.compose(lambda: app.sending.save_draft(session(request), body))


@router.post("/send")
async def send(body: SendRequest, request: Request) -> dict[str, Any]:
    request.state.operation_id = body.operation_id
    app = mail_services(request)
    return await app.compose(lambda: app.sending.send(session(request), body))


@router.get("/sending")
async def sending(request: Request) -> list[dict[str, Any]]:
    app = mail_services(request)
    return await app.pool.run(lambda: app.sending.operations(session(request)))


@router.get("/sending/{identifier}")
async def sending_operation(identifier: str, request: Request) -> dict[str, Any]:
    app = mail_services(request)
    return await app.pool.run(lambda: app.sending.operation(session(request), identifier))


@router.post("/sending/{identifier}/save-sent")
async def retry_sent(identifier: str, request: Request) -> dict[str, Any]:
    request.state.operation_id = identifier
    app = mail_services(request)
    return await app.compose(lambda: app.sending.retry_sent(session(request), identifier))


@profile_router.get("")
async def profile(request: Request) -> dict[str, Any]:
    app = mail_services(request)
    return await app.pool.run(lambda: app.auth.profile(session(request)))


@profile_router.put("")
async def save_profile(body: ProfileChange, request: Request) -> dict[str, Any]:
    app = mail_services(request)
    return await app.pool.run(lambda: app.auth.save_profile(session(request), body))


@profile_router.post("/authenticator")
async def authenticator(body: FactorChange, request: Request) -> dict[str, Any]:
    app = mail_services(request)
    return await app.pool.run(lambda: app.auth.factor_change(session(request), body, peer(request)))


@profile_router.post("/password")
async def change_password(body: PasswordChange, request: Request) -> dict[str, Any]:
    app = mail_services(request)
    new_password = mailbox_password(body.new_password)

    def change() -> dict[str, Any]:
        identity = app.auth.prove_password(session(request), body, peer(request))
        with app.auth.database.transaction() as connection:
            app.auth.consume_proof(connection, identity, body)
            # Invalidate before crossing the helper boundary. Even an uncertain
            # password reset must not leave reusable web sessions behind.
            connection.execute(
                "UPDATE mailbox_users SET revision=revision+1 WHERE id=?", (identity.id,)
            )
            connection.execute("DELETE FROM mailbox_sessions WHERE user_id=?", (identity.id,))
        request.state.webmail_revoked = True
        app.auth.cleanup(force=True)
        if app.auth.authority is not None:
            result = app.auth.authority.change_password(identity, body.password, new_password)
            return {"revoked": True, "state": result["state"]}
        result = services(request).mailboxes.reset(
            identity.email,
            {"request_id": str(uuid.uuid4()), "password": new_password},
            expected_inbox_validity=identity.validity,
        )
        return {"revoked": True, "state": result["state"]}

    return await app.pool.run(change)
