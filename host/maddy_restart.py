"""Root worker: restart Maddy when the dashboard files a validated request."""

import logging
import os
import sys
import uuid
from pathlib import Path

from helper.maddy_restart import REQUEST_NAME, RESULT_NAME
from helper.submission import DockerRestarter
from shared.errors import ServiceError
from shared.files import atomic_owned_json, protected_json, sync_directory

STATE = Path("/var/lib/mail-admin-helper")
MAX_REQUEST_BYTES = 4096

log = logging.getLogger("mail-admin-maddy-restart")


def read_request(path: Path, owner: int) -> str:
    """Return the request id, or raise if the file is not exactly what the dashboard writes."""
    if os.lstat(path).st_size > MAX_REQUEST_BYTES:
        raise ServiceError("restart_request_too_large", 503)
    record = protected_json(path, owner)
    try:
        return str(uuid.UUID(str(record["id"])))
    except (KeyError, ValueError) as error:
        raise ServiceError("restart_request_invalid", 503) from error


def already_answered(result: Path, owner: int, request_id: str) -> bool:
    try:
        return protected_json(result, owner).get("id") == request_id
    except (OSError, ServiceError):
        return False


def restart_succeeded() -> bool:
    try:
        DockerRestarter().restart()
    except (OSError, ServiceError):
        log.exception("Maddy restart failed")
        return False
    return True


def process(state: Path) -> int:
    request, result = state / REQUEST_NAME, state / RESULT_NAME
    owner = state.stat().st_uid
    try:
        request_id = read_request(request, owner)
    except FileNotFoundError:
        return 0
    except (OSError, ServiceError):
        log.error("Rejected an untrusted restart request")
        request.unlink(missing_ok=True)
        return 1
    if not already_answered(result, owner, request_id):
        atomic_owned_json(result, {"id": request_id, "ok": restart_succeeded()}, owner)
    # Removing the request last means a crash before this point retries; a repeat is skipped.
    request.unlink(missing_ok=True)
    sync_directory(state)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    return process(STATE)


if __name__ == "__main__":
    sys.exit(main())
