"""In-process relay actions: verify with the local Maddy CLI, restart through a root worker."""

import time
import uuid
from pathlib import Path

from helper.maddy import MaddyAccess, cli_succeeded
from helper.submission import RelayActions
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json

REQUEST_NAME = "maddy-restart.request.json"
RESULT_NAME = "maddy-restart.result.json"
RESTART_TIMEOUT_SECONDS = 15.0
POLL_INTERVAL_SECONDS = 0.1


class LocalVerifier:
    def __init__(self, maddy: MaddyAccess) -> None:
        self.maddy = maddy

    def verify(self) -> bool:
        return cli_succeeded(self.maddy.change(("verify-config",)))


class FileRestarter:
    """Asks the root worker (started by a path unit) to restart Maddy and waits for its answer."""

    def __init__(self, state: Path, owner: int, timeout: float = RESTART_TIMEOUT_SECONDS,
                 ) -> None:
        self.request, self.result = state / REQUEST_NAME, state / RESULT_NAME
        self.owner, self.timeout = owner, timeout

    def restart(self) -> None:
        request_id = str(uuid.uuid4())
        # A leftover answer must not be mistaken for this request's.
        self.result.unlink(missing_ok=True)
        atomic_json(self.request, {"id": request_id, "requested_at": time.time()})
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self._answer(request_id):
                return
            time.sleep(POLL_INTERVAL_SECONDS)
        raise ServiceError("submission_restart_failed", 503)

    def _answer(self, request_id: str) -> bool:
        try:
            record = protected_json(self.result, self.owner)
        except (OSError, ServiceError):
            # Missing, or not yet handed over by the worker.
            return False
        if record.get("id") != request_id:
            return False
        if record.get("ok") is not True:
            raise ServiceError("submission_restart_failed", 503)
        return True


def local_relay_actions(maddy: MaddyAccess, state: Path, owner: int) -> RelayActions:
    return RelayActions(LocalVerifier(maddy), FileRestarter(state, owner))
