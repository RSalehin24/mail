import unittest

import httpx
from argon2 import PasswordHasher

from backend.auth import AuthDependencies, Authentication
from backend.authorization import COOKIE_NAME
from backend.config import Settings
from backend.enrollment import Enrollment
from backend.mailboxes import Mailboxes
from backend.main import create_app
from backend.relay import Relay, RelayDependencies
from backend.services import Services
from backend.work import WorkPool
from helper.protocol import HelperRequest
from tests.auth_support import AuthFixture
from tests.helper_support import HelperFixture
from tests.support import StateFixture


class DirectHelper:
    def __init__(self, helper):
        self.helper = helper

    def call(self, request):
        return self.helper.handle(HelperRequest.parse({"protocol": 1, **request}))


class FakeRelayTransport:
    def __init__(self):
        self.messages = []
        self.failure = None

    def perform(self, configuration, message):
        if self.failure:
            raise self.failure
        self.messages.append((configuration, message))


class ApiFixture(AuthFixture):
    def __init__(self):
        super().__init__()
        self.services = api_services(self)

    def close(self):
        self.services.close()
        self.host.close()
        super().close()


class FreshApiFixture(StateFixture):
    def __init__(self):
        super().__init__()
        self.auth = Authentication(AuthDependencies(
            self.database, self.keys,
            PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1), lambda: self.now,
        ))
        self.enrollment = Enrollment(self.auth)
        self.services = api_services(self)

    def close(self):
        self.services.close()
        self.host.close()
        super().close()


def api_services(fixture):
    fixture.host, fixture.transport = HelperFixture(), FakeRelayTransport()
    static = fixture.root / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>Mail Admin fixture</title>")
    settings = Settings("https://mail.rsalehin24.me", static, 8192, 1, 4, 5)
    return Services(
        settings, fixture.auth,
        Mailboxes((fixture.database, DirectHelper(fixture.host.helper), lambda: fixture.now)),
        WorkPool(1, 5), WorkPool(4, 5),
        Relay(RelayDependencies(fixture.database, fixture.keys, fixture.transport,
                                lambda: fixture.now)),
        lambda: {"state": "unverified", "configured_mode": "cloudflare"},
    )


class ApiTestCase(unittest.IsolatedAsyncioTestCase):
    fixture_factory = ApiFixture

    def set_session(self, token):
        self.client.cookies.clear()
        if token:
            self.client.cookies.set(COOKIE_NAME, token, domain="mail.rsalehin24.me", path="/")

    async def asyncSetUp(self):
        self.fixture = self.fixture_factory()
        self.addCleanup(self.fixture.close)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(self.fixture.services)),
            base_url="https://mail.rsalehin24.me",
            headers={"origin": "https://mail.rsalehin24.me"},
        )
        self.addAsyncCleanup(self.client.aclose)
