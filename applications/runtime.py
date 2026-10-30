"""Composition and HTTPS entry points for independently installed applications."""

import argparse
import asyncio
import json
import os
import ssl
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import uvicorn
from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles

from applications.client import ManagementClient
from applications.identity import Identities
from applications.pairing import PairingAuthority
from applications.tls_protocol import MutualTLSProtocol
from applications.trash import TrashOutbox
from backend.auth import AuthDependencies, Authentication
from backend.boundary import HttpBoundary
from backend.config import Settings
from backend.database import Database
from backend.helper_client import HelperClient
from backend.mailboxes import Mailboxes
from backend.main import _fallback, invalid_request, service_error
from backend.relay import Relay, RelayDependencies, SmtpTransport
from backend.retention import cleanup
from backend.routes_auth import protected, public
from backend.routes_mail import router as management_mail
from backend.routes_relay import router as relay_routes
from backend.routes_webmail import profile_router
from backend.routes_webmail import router as mail_routes
from backend.secrets import SecretKeys
from backend.services import Services
from backend.webmail_services import MailServices
from backend.work import WorkPool
from shared.errors import ServiceError
from shared.installation import site


class UnavailableHelper:
    def call(self, request: dict[str, Any]) -> dict[str, Any]:
        raise ServiceError("management_operation_not_available_on_webmail", 403)


def load(root: Path, kind: str, *, mailbox_runtime: bool = True) -> Services:
    os.environ["MAIL_CONFIG"] = str(root / "site.json")
    record = json.loads((root / "installation.json").read_text())
    resources = record["resources"]
    database = Database(root / (kind + ".db"), record["installation_id"], busy_timeout=1)
    # Keys are private to this application and its independent backup.
    keys = SecretKeys(
        (root / "secrets/encryption.key").read_bytes(), (root / "secrets/verifier.key").read_bytes()
    )
    auth = Authentication(
        AuthDependencies(
            database,
            keys,
            PasswordHasher(memory_cost=resources["hash_memory_kib"], parallelism=1, time_cost=3),
            time.time,
        )
    )
    config = site()
    with database.transaction() as connection:
        state = connection.execute("SELECT * FROM application_state WHERE id=1").fetchone()
        if state is None or state["kind"] != kind or state["version"] != 1:
            raise ServiceError("application_upgrade_required", 503)
        values = json.loads(state["settings"])
    enabled = bool(values.get("webmail_enabled", False)) and kind == "webmail"
    settings = Settings(
        "https://" + config.hostname,
        Path(__file__).resolve().parents[1] / "static",
        resources["hash_memory_kib"],
        resources["hash_parallelism"],
        resources["http_concurrency"],
        resources["operation_timeout"],
        webmail_enabled=enabled,
        webmail_allowed_mailboxes=tuple(values.get("allowlist", [])),
        application_kind=kind,
    )
    helper: Any = (
        HelperClient(Path("/run/mail-management-helper/control.sock"), settings.operation_timeout)
        if kind == "management"
        else UnavailableHelper()
    )
    relay = Relay(
        RelayDependencies(database, keys, SmtpTransport(settings.operation_timeout), time.time)
    )
    services = Services(
        settings,
        auth,
        Mailboxes((database, helper, time.time)),
        WorkPool(settings.hash_parallelism, settings.operation_timeout),
        WorkPool(settings.http_concurrency, settings.operation_timeout),
        relay,
    )
    if kind == "management":
        services.mailboxes.identities = Identities(database, helper)
        services.mailboxes.ownership = services.mailboxes.identities.guard
    elif mailbox_runtime:
        remote = ManagementClient(database, keys)
        # Even a disabled/unpaired installation starts and exposes administrative diagnostics.
        services.webmail = MailServices(
            auth,
            authority=remote,
            attachment_capacity_mib=resources.get("attachment_spool_mib", 128),
            independent_io=True,
        )
        services.webmail.auth.trash = TrashOutbox(database, remote)
    return services


def authority(root: Path, context: Services) -> PairingAuthority:
    return PairingAuthority(
        context.authentication.database,
        context.authentication.keys,
        (root / "secrets/integration-ca.key").read_text(),
        (root / "integration-ca.pem").read_text(),
    )


def create_app(context: Services, root: Path, pairing: PairingAuthority | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        async def housekeeping() -> None:
            while True:
                await asyncio.sleep(30)
                try:
                    await context.io.run(
                        lambda: cleanup(context.authentication.database, time.time())
                    )
                    if context.webmail is not None:
                        await context.webmail.pool.run(context.webmail.cleanup)
                        await context.webmail.pool.run(context.webmail.auth.trash.flush)
                except ServiceError:
                    app.state.connection_status = "degraded"

        task = asyncio.create_task(housekeeping())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            context.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.services = context
    app.state.root = root
    app.state.pairing = pairing
    app.add_middleware(HttpBoundary, settings=context.settings)
    app.add_exception_handler(ServiceError, service_error)
    app.add_exception_handler(RequestValidationError, invalid_request)
    from applications.routes import router

    app.include_router(router)
    app.include_router(public)
    app.include_router(protected)
    if context.settings.application_kind == "management":
        app.include_router(management_mail)
        app.include_router(relay_routes)
    else:
        app.include_router(mail_routes)
        app.include_router(profile_router)
    assets = context.settings.static_directory / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets))
    _fallback(app, context)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("management", "webmail"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--integration", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    context = load(args.root, args.kind)
    if args.integration:
        if args.kind != "management":
            raise ServiceError("management_listener_required", 400)
        from applications.integration_api import create_integration_app

        app = create_integration_app(
            authority(args.root, context), context.mailboxes.identities, context.mailboxes
        )
        uvicorn.run(
            app,
            host="0.0.0.0",  # noqa: S104 -- explicit public HTTPS listener.
            port=8444,
            workers=1,
            http=MutualTLSProtocol,
            ssl_cert_reqs=ssl.CERT_REQUIRED,
            ssl_ca_certs=str(args.root / "integration-ca.pem"),
            ssl_certfile=str(args.root / "integration.pem"),
            ssl_keyfile=str(args.root / "secrets/integration.key"),
            proxy_headers=False,
            access_log=False,
            server_header=False,
            limit_concurrency=24,
            timeout_keep_alive=5,
        )
    else:
        app = create_app(
            context, args.root, authority(args.root, context) if args.kind == "management" else None
        )
        uvicorn.run(
            app,
            host="0.0.0.0",  # noqa: S104 -- explicit public HTTPS listener.
            port=8443,
            workers=1,
            ssl_certfile=str(args.root / "tls/active/fullchain.pem"),
            ssl_keyfile=str(args.root / "tls/active/privkey.pem"),
            proxy_headers=False,
            access_log=False,
            server_header=False,
            limit_concurrency=40,
            timeout_keep_alive=5,
        )


if __name__ == "__main__":
    main()
