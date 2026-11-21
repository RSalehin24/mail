"""Single-worker HTTPS entry point; missing TLS material fails before listening."""

import os
from pathlib import Path

from backend.binds import require_runtime_sources
from backend.config import Settings
from backend.database import require_existing_database
from shared.runtime_policy import RuntimePolicy


def main() -> None:
    os.umask(0o077)
    settings = Settings.from_environment()
    require_existing_database(Path("/app/data/admin_data.db"))
    RuntimePolicy().require_running()
    require_runtime_sources(certificates=(settings.tls_certificate, settings.tls_key))
    # Reject a lost data mount before loading the HTTP and cryptographic dependency graph.
    import uvicorn

    from backend.certificate_watch import restart_when_replaced

    restart_when_replaced(settings.tls_certificate)
    uvicorn.run(
        "backend.main:create_app",
        factory=True,
        host=os.environ.get("BIND_HOST", "127.0.0.1"),
        port=8443,
        workers=1,
        ssl_certfile=str(settings.tls_certificate),
        ssl_keyfile=str(settings.tls_key),
        access_log=False,
        proxy_headers=False,
        server_header=False,
        headers=[("Cache-Control", "no-store")],
        ws="none",
        # ASGI admission bounds active requests. Allow bounded idle browser sockets
        # separately: Uvicorn counts keep-alive connections against this limit.
        limit_concurrency=2 * settings.http_concurrency,
        backlog=2 * settings.http_concurrency,
        timeout_keep_alive=5,
    )


if __name__ == "__main__":
    main()
