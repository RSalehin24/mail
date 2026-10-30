"""Application factory with explicit state, protected routers and a final SPA fallback."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.boundary import HttpBoundary
from backend.retention import cleanup
from backend.routes_auth import protected, public
from backend.routes_backups import router as backups_router
from backend.routes_forwarding import router as forwarding_router
from backend.routes_mail import router
from backend.routes_recovery import router as recovery_router
from backend.routes_relay import router as relay_router
from backend.routes_webmail import profile_router
from backend.routes_webmail import router as webmail_router
from backend.services import Services
from shared.errors import ServiceError


async def housekeeping(application: FastAPI) -> None:
    context: Services = application.state.services
    while True:
        await asyncio.sleep(60)
        try:
            await context.io.run(
                lambda: cleanup(
                    context.authentication.database, context.authentication.dependencies.clock()
                )
            )
            application.state.retention_status = "ok"
            if context.webmail is not None:
                await context.webmail.pool.run(context.webmail.cleanup)
        except ServiceError:
            application.state.retention_status = "degraded"


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    context: Services = application.state.services
    await context.io.run(
        lambda: cleanup(
            context.authentication.database, context.authentication.dependencies.clock()
        )
    )
    task = asyncio.create_task(housekeeping(application))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        context.close()


async def service_error(request: Request, error: Exception) -> JSONResponse:
    error = cast(ServiceError, error)
    payload: dict[str, Any] = {"error": error.code, "request_id": request.state.request_id}
    if hasattr(request.state, "operation_id"):
        payload["operation_id"] = request.state.operation_id
    if getattr(request.state, "webmail_revoked", False):
        payload["revoked"] = True
    headers = {"Retry-After": str(error.retry_after)} if error.retry_after is not None else {}
    return JSONResponse(payload, status_code=error.status, headers=headers)


async def invalid_request(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(
        {"error": "invalid_request", "request_id": request.state.request_id}, status_code=422
    )


def create_app(context: Services | None = None) -> FastAPI:
    context = context or Services.production()
    application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    application.state.services = context
    application.add_middleware(HttpBoundary, settings=context.settings)
    application.add_exception_handler(ServiceError, service_error)
    application.add_exception_handler(RequestValidationError, invalid_request)
    for routes in (
        public,
        protected,
        router,
        relay_router,
        recovery_router,
        backups_router,
        forwarding_router,
        webmail_router,
        profile_router,
    ):
        application.include_router(routes)
    assets = context.settings.static_directory / "assets"
    if assets.is_dir():
        application.mount("/assets", StaticFiles(directory=assets), name="assets")
    _fallback(application, context)
    return application


def _fallback(application: FastAPI, context: Services) -> None:
    @application.api_route("/api/{path:path}", methods=["GET", "POST", "DELETE", "PUT", "PATCH"])
    async def api_missing(path: str) -> None:
        raise ServiceError("api_not_found", 404)

    @application.get("/{path:path}")
    async def frontend(path: str) -> FileResponse:
        if path in {"favicon.svg", "icon.svg", "manifest.webmanifest"}:
            asset = context.settings.static_directory / path
            if not asset.is_file():
                raise ServiceError("asset_not_found", 404)
            return FileResponse(asset, headers={"Cache-Control": "public, max-age=3600"})
        if "." in path.rsplit("/", 1)[-1] and not path.startswith("mailboxes/"):
            raise ServiceError("asset_not_found", 404)
        index = context.settings.static_directory / "index.html"
        if not index.is_file():
            raise ServiceError("frontend_unavailable", 503)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
