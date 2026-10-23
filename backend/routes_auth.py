"""Public challenges and protected security endpoints share the domain authentication engine."""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from backend.auth import Credentials, Verification
from backend.authorization import COOKIE_NAME, authenticated, peer, recent, services, session
from backend.enrollment import Enrollment
from backend.requests import (
    FactorRequest,
    LoginRequest,
    OtpRequest,
    PasswordRequest,
    SetupRequest,
    TotpRequest,
)
from backend.security import Security
from shared.errors import ServiceError

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
    values = {"token": body.token, "password": body.password, "code": body.code}
    codes = await application.hashes.run(lambda: enrollment.confirm(values, peer(request)))
    return {"recovery_codes": codes, "initialized": True}


@public.post("/login")
async def login(body: LoginRequest, request: Request) -> dict[str, Any]:
    application = services(request)
    challenge = await application.hashes.run(
        lambda: application.authentication.password_challenge(
            Credentials(body.email, body.password, peer(request))
        )
    )
    available = await application.io.run(application.relay.otp_ready)
    return {
        "challenge": challenge,
        "email_otp_available": bool(application.settings.email_otp_enabled and available),
    }


@public.post("/2fa/verify")
async def verify_login(
    body: FactorRequest, request: Request, response: Response
) -> dict[str, bool]:
    application = services(request)
    token = await application.hashes.run(
        lambda: application.authentication.verify(
            Verification(body.challenge, body.factor, body.code, peer(request)), "login"
        )
    )
    if token is None:
        raise ServiceError("authentication_failed", 401)
    response.set_cookie(
        COOKIE_NAME, token, max_age=43200, secure=True, httponly=True, samesite="strict", path="/"
    )
    return {"authenticated": True}


@public.post("/2fa/send-otp")
async def send_otp(body: OtpRequest, request: Request) -> dict[str, str]:
    application = services(request)
    if not application.settings.email_otp_enabled:
        raise ServiceError("email_otp_unavailable", 403)
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


@protected.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, bool]:
    application = services(request)
    await application.io.run(lambda: application.authentication.logout(session(request)))
    clear_cookie(response)
    return {"revoked": True}


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", secure=True, httponly=True, samesite="strict")


@protected.post("/security/reauth")
async def reauth(body: LoginRequest, request: Request) -> dict[str, str]:
    application = services(request)
    challenge = await application.hashes.run(
        lambda: application.authentication.password_challenge(
            Credentials(body.email, body.password, peer(request)), session(request)
        )
    )
    return {"challenge": challenge}


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
