"""Public challenges and protected security endpoints share the domain authentication engine."""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from backend.auth import Credentials, Verification
from backend.authorization import COOKIE_NAME, authenticated, peer, recent, services, session
from backend.email_auth import EmailAuthentication
from backend.enrollment import Enrollment
from backend.requests import (
    FactorRequest,
    LoginRequest,
    OtpRequest,
    PasswordRequest,
    RequestModel,
    SetupRequest,
    TotpRequest,
)
from backend.security import Security
from shared.errors import ServiceError
from shared.installation import site

public = APIRouter(prefix="/api")
protected = APIRouter(prefix="/api", dependencies=[Depends(authenticated)])


@public.get("/status")
def status() -> dict[str, str]:
    return {"status": "ok"}


@public.post("/setup")
async def setup(body: SetupRequest, request: Request) -> dict[str, Any]:
    application = services(request)
    enrollment = Enrollment(application.authentication)
    if body.password is None and body.code is None:
        return await application.hashes.run(lambda: enrollment.begin(body.token, peer(request)))
    if body.password is None or body.code is None:
        raise ServiceError("invalid_setup_request", 400)
    if application.settings.application_kind != "legacy":
        with application.authentication.database.transaction() as connection:
            if not connection.execute("SELECT configured FROM application_state").fetchone()[0]:
                raise ServiceError("installation_wizard_required", 409)
    values = {"token": body.token, "password": body.password, "code": body.code}
    codes = await application.hashes.run(lambda: enrollment.confirm(values, peer(request)))
    return {"recovery_codes": codes, "initialized": True}


@public.post("/login")
async def login(body: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    application = services(request)
    if body.email != site().admin_email or request.url.path == "/api/mailbox/login":
        from backend.routes_webmail import enabled_mailboxes

        if application.webmail is None or body.email.lower() not in enabled_mailboxes(request):
            await application.io.run(lambda: application.authentication.reserve(peer(request)))
            raise ServiceError("authentication_failed", 401)
        from backend.routes_webmail import mail_services

        mail = mail_services(request)
        result, token = await mail.pool.run(
            lambda: mail.auth.login(body.email, body.password, peer(request))
        )
        if token is not None:
            set_cookie(response, token)
        return result
    challenge = await application.hashes.run(
        lambda: application.authentication.password_challenge(
            Credentials(body.email, body.password, peer(request))
        )
    )
    available = await application.io.run(application.authentication.email_available)
    return {
        "challenge": challenge,
        "email_otp_available": available,
    }


@public.post("/2fa/verify")
async def verify_login(body: FactorRequest, request: Request, response: Response) -> dict[str, Any]:
    application = services(request)
    if body.challenge.startswith("wm."):
        from backend.routes_webmail import mail_services

        mail = mail_services(request)
        result, mailbox_token = await mail.pool.run(
            lambda: mail.auth.verify(body.challenge, body.factor, body.code, peer(request))
        )
        set_cookie(response, mailbox_token)
        return result
    token = await application.hashes.run(
        lambda: application.authentication.verify(
            Verification(body.challenge, body.factor, body.code, peer(request)), "login"
        )
    )
    if token is None:
        raise ServiceError("authentication_failed", 401)
    set_cookie(response, token)
    return {"authenticated": True}


def set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token, max_age=43200, secure=True, httponly=True, samesite="strict", path="/"
    )


@public.post("/2fa/send-otp")
async def send_otp(body: OtpRequest, request: Request) -> dict[str, str]:
    application = services(request)
    binding = session(request) if body.purpose == "reauth" else None
    if body.purpose == "reauth" and not binding:
        raise ServiceError("authentication_required", 401)
    await application.io.run(
        lambda: application.authentication.send_otp(
            Verification(body.challenge, "email", "", peer(request), binding),
            application.relay.send_otp,
        )
    )
    return {"state": "sent", "expires_in": "300", "resend_after": "60"}


@public.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, bool]:
    application = services(request)
    if session(request).startswith("wm."):
        if application.webmail is not None:
            mail = application.webmail
            await mail.pool.run(lambda: mail.auth.logout(session(request)))
    else:
        await application.io.run(lambda: application.authentication.logout(session(request)))
    clear_cookie(response)
    return {"revoked": True}


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", secure=True, httponly=True, samesite="strict")


@protected.post("/security/reauth")
async def reauth(body: LoginRequest, request: Request) -> dict[str, Any]:
    application = services(request)
    challenge = await application.hashes.run(
        lambda: application.authentication.password_challenge(
            Credentials(body.email, body.password, peer(request)), session(request)
        )
    )
    available = await application.io.run(application.authentication.email_available)
    return {
        "challenge": challenge,
        "email_otp_available": available,
    }


@protected.post("/security/reauth/verify")
async def verify_reauth(body: FactorRequest, request: Request) -> dict[str, int]:
    application = services(request)
    await application.hashes.run(
        lambda: application.authentication.verify(
            Verification(body.challenge, body.factor, body.code, peer(request), session(request)),
            "reauth",
        )
    )
    return {"grant_seconds": 300}


@protected.post("/security/password", dependencies=[Depends(recent)])
async def change_password(
    body: PasswordRequest, request: Request, response: Response
) -> dict[str, bool]:
    application = services(request)
    await application.hashes.run(
        lambda: Security(application.authentication).change_password(
            session(request), body.password
        )
    )
    clear_cookie(response)
    return {"revoked": True}


@protected.post("/security/recovery-codes", dependencies=[Depends(recent)])
async def recovery_codes(request: Request, response: Response) -> dict[str, Any]:
    application = services(request)
    codes = await application.hashes.run(
        lambda: Security(application.authentication).recovery_codes(session(request))
    )
    clear_cookie(response)
    return {"recovery_codes": codes, "revoked": True}


@protected.post("/security/totp", dependencies=[Depends(recent)])
async def replace_totp(body: TotpRequest, request: Request, response: Response) -> dict[str, Any]:
    application = services(request)
    security = Security(application.authentication)
    if body.code is None:
        return await application.hashes.run(lambda: security.begin_totp(session(request)))
    codes = await application.hashes.run(
        lambda: security.confirm_totp(session(request), (body.code or "", peer(request)))
    )
    clear_cookie(response)
    return {"recovery_codes": codes, "revoked": True}


@public.get("/session")
async def inspect_session(request: Request) -> dict[str, Any]:
    if session(request).startswith("wm."):
        from backend.routes_webmail import mail_services, mailbox_authenticated

        await mailbox_authenticated(request)
        user = await mail_services(request).pool.run(
            lambda: mail_services(request).auth.require(session(request))
        )
        return {
            "authenticated": True,
            "role": "mailbox",
            "user": {"id": user.id, "email": user.email},
        }
    await authenticated(request)
    return {
        "authenticated": True,
        "role": "administrator",
        "user": {"id": "admin", "email": site().admin_email},
    }


class EmailAuthenticationRequest(RequestModel):
    enabled: bool


@protected.get("/security/email-authentication")
async def email_authentication_status(request: Request) -> dict[str, Any]:
    application = services(request)
    return await application.io.run(EmailAuthentication(application.authentication).status)


@protected.put("/security/email-authentication", dependencies=[Depends(recent)])
async def save_email_authentication(
    body: EmailAuthenticationRequest, request: Request
) -> dict[str, Any]:
    application = services(request)
    return await application.io.run(
        lambda: EmailAuthentication(application.authentication).save(session(request), body.enabled)
    )


public.add_api_route("/mailbox/login", login, methods=["POST"])


@public.get("/installation")
def installation() -> dict[str, Any]:
    return {
        "kind": "legacy",
        "configured": True,
        "initialized": True,
        "version": "0.1.0",
        **site().public(),
    }
