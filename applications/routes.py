"""Application setup and pairing controls, separated from mailbox authorization."""

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from applications import VERSION
from applications.setup import ConfigureRequest, configure
from backend.authorization import authenticated, peer, recent, services
from backend.requests import RequestModel
from shared.errors import ServiceError
from shared.installation import site
from shared.validation import address

router = APIRouter(prefix="/api")


@router.get("/installation")
def installation(request: Request) -> dict[str, Any]:
    context = services(request)
    with context.authentication.database.transaction() as connection:
        configured = connection.execute("SELECT configured FROM application_state").fetchone()[0]
        initialized = connection.execute("SELECT initialized FROM admin_auth").fetchone()[0]
    return {
        "kind": context.settings.application_kind,
        "version": VERSION,
        "configured": bool(configured),
        "initialized": bool(initialized),
        **site().public(),
    }


@router.post("/installation/configure")
async def setup(body: ConfigureRequest, request: Request) -> dict[str, Any]:
    context = services(request)
    return await context.io.run(
        lambda: configure(context, request.app.state.root, body, peer(request))
    )


@router.get("/installation/diagnostics", dependencies=[Depends(authenticated)])
async def diagnostics(request: Request) -> dict[str, Any]:
    context = services(request)
    with context.authentication.database.transaction() as connection:
        values = json.loads(
            connection.execute("SELECT settings FROM application_state").fetchone()[0]
        )
    if context.settings.application_kind == "webmail":
        if context.webmail is None:
            raise ServiceError("webmail_installation_required", 403)
        try:
            return await context.io.run(context.webmail.auth.authority.status)
        except ServiceError as error:
            return {"connected": False, "reason": error.code, "version": VERSION}
    from applications.dns import required

    records = await context.io.run(lambda: required(values))
    return {
        "records": records,
        "delivery_mode": values.get("delivery_mode", "direct"),
        "prerequisites": [
            "Publish the listed records through your DNS provider.",
            "Ask the server provider to set reverse DNS (PTR) to the mail hostname.",
            "Allow inbound TCP 25, 587, 993 and HTTPS; allow integration TCP 8444 from Webmail.",
            "Allow outbound authenticated relay TCP 587. Verify the sender domain with the relay.",
            "Configure remote or externally replicated storage at the selected backup destination.",
        ],
    }


class TokenRequest(RequestModel):
    token: str = Field(max_length=24000)


class CredentialRequest(RequestModel):
    credential_id: str = Field(pattern=r"^[0-9a-f]{32}$")


@router.get("/pairing", dependencies=[Depends(authenticated)])
async def pairing_status(request: Request) -> dict[str, Any]:
    context = services(request)
    if context.settings.application_kind == "management":
        return {
            "credentials": await context.io.run(request.app.state.pairing.status),
            "api_version": 1,
            "version": VERSION,
        }
    if context.webmail is None:
        raise ServiceError("webmail_installation_required", 403)
    try:
        return await context.io.run(context.webmail.auth.authority.status)
    except ServiceError as error:
        return {"connected": False, "reason": error.code, "api_version": 1, "version": VERSION}


@router.post("/pairing/enrollment", dependencies=[Depends(authenticated), Depends(recent)])
async def enrollment(request: Request) -> dict[str, Any]:
    context = services(request)
    if context.settings.application_kind != "management":
        raise ServiceError("management_role_required", 403)
    return await context.io.run(
        lambda: request.app.state.pairing.enrollment("https://" + site().hostname + ":8444")
    )


@router.post("/pairing/enroll", dependencies=[Depends(authenticated), Depends(recent)])
async def enroll(body: TokenRequest, request: Request) -> dict[str, Any]:
    context = services(request)
    if context.settings.application_kind != "webmail" or context.webmail is None:
        raise ServiceError("webmail_installation_required", 403)
    with context.authentication.database.transaction() as connection:
        values = json.loads(
            connection.execute("SELECT settings FROM application_state").fetchone()[0]
        )
        if values.get("webmail_enabled"):
            raise ServiceError("disable_mailbox_access_before_pairing", 409)
        connection.execute("DELETE FROM mailbox_sessions")
    remote = context.webmail.auth.authority
    return await context.io.run(lambda: remote.enroll(body.token))


@router.post("/pairing/rotate", dependencies=[Depends(authenticated), Depends(recent)])
async def rotate(request: Request) -> dict[str, Any]:
    context = services(request)
    if context.webmail is None:
        raise ServiceError("rotate_from_paired_webmail", 409)
    return await context.io.run(context.webmail.auth.authority.rotate)


@router.post("/pairing/activate", dependencies=[Depends(authenticated), Depends(recent)])
async def activate(request: Request) -> dict[str, Any]:
    context = services(request)
    if context.webmail is None:
        raise ServiceError("webmail_installation_required", 403)
    return await context.io.run(context.webmail.auth.authority.activate)


@router.post("/pairing/revoke", dependencies=[Depends(authenticated), Depends(recent)])
async def revoke(body: CredentialRequest, request: Request) -> dict[str, bool]:
    context = services(request)
    if context.settings.application_kind != "management":
        raise ServiceError("management_role_required", 403)
    await context.io.run(lambda: request.app.state.pairing.revoke(body.credential_id))
    return {"revoked": True}


class RolloutRequest(RequestModel):
    enabled: bool
    allowlist: list[str] = Field(max_length=10)


@router.post("/installation/rollout", dependencies=[Depends(authenticated), Depends(recent)])
async def rollout(body: RolloutRequest, request: Request) -> dict[str, bool]:
    context = services(request)
    if context.settings.application_kind != "webmail":
        raise ServiceError("webmail_installation_required", 403)
    allowlist = [address(value) for value in body.allowlist]
    if body.enabled:
        from applications.qualification import require_qualified

        if context.webmail is None:
            raise ServiceError("webmail_installation_required", 403)
        paired = await context.io.run(context.webmail.auth.authority.status)
        require_qualified(
            request.app.state.root / "qualification.json",
            context.authentication.database,
            paired["management_id"],
        )
        if not allowlist or len(allowlist) != len(set(allowlist)):
            raise ServiceError("pilot_mailbox_required", 400)
    with context.authentication.database.transaction() as connection:
        row = connection.execute("SELECT settings FROM application_state").fetchone()
        values = json.loads(row[0])
        if body.enabled and not values.get("pilot_verified") and len(allowlist) != 1:
            raise ServiceError("verify_one_pilot_mailbox_first", 409)
        values.update(webmail_enabled=body.enabled, allowlist=allowlist)
        connection.execute("UPDATE application_state SET settings=?", (json.dumps(values),))
        if not body.enabled:
            connection.execute("DELETE FROM mailbox_sessions")
    (request.app.state.root / "restart-requested").touch(mode=0o600)
    return {"restart_required": True}


class InstallationSettings(RequestModel):
    backup_destination: str = Field(max_length=512)
    permanent_deletion_enabled: bool = False


@router.get("/installation/settings", dependencies=[Depends(authenticated)])
def settings(request: Request) -> dict[str, Any]:
    context = services(request)
    with context.authentication.database.transaction() as connection:
        values = json.loads(
            connection.execute("SELECT settings FROM application_state").fetchone()[0]
        )
    return {
        "backup_destination": values.get("backup_destination", ""),
        "permanent_deletion_enabled": values.get("permanent_deletion_enabled", False),
        "webmail_enabled": values.get("webmail_enabled", False),
        "allowlist": values.get("allowlist", []),
        "backup": json.loads((request.app.state.root / "last-backup.json").read_text())
        if (request.app.state.root / "last-backup.json").is_file()
        else {"state": "not_run"},
    }


@router.put("/installation/settings", dependencies=[Depends(authenticated), Depends(recent)])
def save_settings(body: InstallationSettings, request: Request) -> dict[str, bool]:
    context = services(request)
    destination = Path(body.backup_destination)
    allowed = Path("/var/backups/mail-" + context.settings.application_kind)
    if not destination.is_relative_to(allowed) or ".." in destination.parts:
        raise ServiceError("unsafe_backup_destination", 400)
    with context.authentication.database.transaction() as connection:
        values = json.loads(
            connection.execute("SELECT settings FROM application_state").fetchone()[0]
        )
        values["backup_destination"] = str(destination)
        values["permanent_deletion_enabled"] = body.permanent_deletion_enabled
        connection.execute("UPDATE application_state SET settings=?", (json.dumps(values),))
    return {"saved": True}


class TLSRequest(RequestModel):
    fullchain: str = Field(max_length=16000)
    private_key: str = Field(max_length=16000)


@router.put("/installation/tls", dependencies=[Depends(authenticated), Depends(recent)])
async def renew_tls(body: TLSRequest, request: Request) -> dict[str, bool]:
    from applications.setup import validate_tls

    context = services(request)
    names = [site().hostname]
    if context.settings.application_kind == "management":
        names.append(site().mail_hostname)
    await context.io.run(
        lambda: validate_tls(request.app.state.root, body.fullchain, body.private_key, names)
    )
    (request.app.state.root / "restart-requested").touch(mode=0o600)
    return {"restart_required": True}
