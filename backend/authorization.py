"""Shared FastAPI route dependencies; all protected routers use authenticated()."""

from typing import cast

from fastapi import Request

from backend.services import Services
from shared.errors import ServiceError

COOKIE_NAME = "__Host-mail_admin"


def services(request: Request) -> Services:
    return cast(Services, request.app.state.services)


def peer(request: Request) -> str:
    if request.client is None:
        raise ServiceError("peer_unavailable", 400)
    return request.client.host


def session(request: Request) -> str:
    return request.cookies.get(COOKIE_NAME, "")


async def authenticated(request: Request) -> None:
    application = services(request)
    await application.io.run(lambda: application.authentication.require_session(session(request)))


async def recent(request: Request) -> None:
    application = services(request)
    await application.io.run(
        lambda: application.authentication.require_session(session(request), True)
    )
