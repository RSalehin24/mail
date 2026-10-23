import os
import uuid

from helper.journal import Journal
from helper.process import CommandResult
from helper.protocol import HelperRequest
from helper.service import HelperDependencies, HostHelper
from host.locks import LockPolicy
from shared.errors import ServiceError
from tests.fixture_values import MAILBOX_PASSWORD
from tests.support import StateFixture


class FakeMaddy:
    def __init__(self):
        self.credentials = set()
        self.storage = set()
        self.calls = []
        self.fail = None
        self.warning = False
        self.unknown = False

    def validate(self):
        pass

    def inventories(self):
        return {"credentials": sorted(self.credentials), "storage": sorted(self.storage)}

    def change(self, arguments, password=""):
        self.calls.append((arguments, password))
        if self.unknown:
            raise ServiceError("command_completion_unknown", 503)
        store = self.credentials if arguments[0] == "creds" else self.storage
        if arguments[1] == "create":
            store.add(arguments[-1])
        if arguments[1] == "remove":
            store.discard(arguments[-1])
        if self.fail == arguments[:2]:
            return CommandResult(1, "fixture failed after mutation")
        return CommandResult(0, "warning: fixture folders" if self.warning else "")


class DeletePolicy:
    def __init__(self):
        self.enabled = False

    def capability(self):
        if not self.enabled:
            raise ServiceError("permanent_deletion_disabled", 403)
        return {}

    def check(self, request, journal):
        self.capability()


class HelperFixture(StateFixture):
    def __init__(self):
        super().__init__()
        self.maddy = FakeMaddy()
        self.journal = Journal.provision(self.root / "journal.db")
        self.lock = self.root / "maddy.lock"
        self.lock.touch(mode=0o600)
        self.deletion = DeletePolicy()
        self.helper = HostHelper(
            HelperDependencies(
                self.maddy,
                self.journal,
                self.deletion,
                self.lock,
                LockPolicy(0.1, os.getuid()),
                lambda: self.now,
                lambda: {},
            )
        )

    def request(self, operation, email="fixture@rsalehin24.me"):
        return HelperRequest(
            operation,
            str(uuid.uuid4()),
            email,
            MAILBOX_PASSWORD if operation in {"create", "password"} else "",
        )
