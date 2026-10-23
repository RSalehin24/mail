import concurrent.futures
import unittest

from backend.budgets import BudgetReservation, Budgets
from backend.relay import Relay, RelayDependencies
from shared.errors import ServiceError
from tests.api_support import FakeRelayTransport
from tests.auth_support import AuthFixture
from tests.support import StateFixture


class PersistentBudgetTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)

    def reserve(self, reservation):
        try:
            Budgets(self.fixture.database).reserve(reservation)
            return True
        except ServiceError as error:
            self.assertEqual(error.code, "rate_limited")
            self.assertEqual(error.status, 429)
            self.assertEqual(error.retry_after, 900)
            return False

    def test_parallel_authentication_reservations_admit_exactly_thirty_admin_or_fifteen_peer(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            attempts = [BudgetReservation("auth", "peer-" + str(index), self.fixture.now)
                        for index in range(40)]
            self.assertEqual(sum(pool.map(self.reserve, attempts)), 30)
        self.fixture.now += 900
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            attempts = [BudgetReservation("auth", "same-peer", self.fixture.now)] * 20
            self.assertEqual(sum(pool.map(self.reserve, attempts)), 15)

    def test_parallel_send_reservations_admit_exactly_five_admin_or_three_peer(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            attempts = [BudgetReservation("send", "peer-" + str(index), self.fixture.now)
                        for index in range(10)]
            self.assertEqual(sum(pool.map(self.reserve, attempts)), 5)
        self.fixture.now += 900
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            attempts = [BudgetReservation("send", "same-peer", self.fixture.now)] * 10
            self.assertEqual(sum(pool.map(self.reserve, attempts)), 3)


class SharedSendBudgetTests(unittest.TestCase):
    def setUp(self):
        self.fixture = AuthFixture()
        self.addCleanup(self.fixture.close)
        self.transport = FakeRelayTransport()
        self.relay = Relay(RelayDependencies(
            self.fixture.database, self.fixture.keys, self.transport, lambda: self.fixture.now,
        ))
        self.relay.save({"host": "smtp-relay.brevo.com", "port": 587, "tls_mode": "starttls",
                         "username": "fixture", "credential": "synthetic-relay-secret"})

    def otp(self, peer):
        from backend.auth import Verification

        challenge = self.fixture.challenge("new-login")
        self.fixture.auth.send_otp(Verification(challenge, "email", "", peer), self.relay.send_otp)

    def assert_send_blocked(self, peer):
        with self.assertRaises(ServiceError) as caught:
            self.otp(peer)
        self.assertEqual((caught.exception.status, caught.exception.code), (429, "rate_limited"))
        self.assertEqual(caught.exception.retry_after, 900)

    def test_email_otp_and_explicit_delivery_share_three_peer_and_five_admin_slots(self):
        first = self.relay.test("mail@rsalehin24.me", ("delivery", "shared-peer"))
        self.relay.confirm(first["test_id"])
        self.otp("shared-peer")
        self.relay.test("mail@rsalehin24.me", ("delivery", "shared-peer"))
        self.assert_send_blocked("shared-peer")
        self.relay.test("mail@rsalehin24.me", ("delivery", "another-peer"))
        self.otp("another-peer")
        self.assert_send_blocked("third-peer")
        with self.assertRaises(ServiceError):
            self.relay.test("mail@rsalehin24.me", ("delivery", "fourth-peer"))
        self.assertEqual(len(self.transport.messages), 5)
        self.fixture.now += 900
        self.relay.test("mail@rsalehin24.me", ("delivery", "shared-peer"))
        self.assertEqual(len(self.transport.messages), 6)
