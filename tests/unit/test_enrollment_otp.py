import concurrent.futures
import dataclasses
import unittest
from unittest.mock import patch

import pyotp

from backend.auth import OTP_GENERATION_ATTEMPTS, AuthDependencies, Authentication, Verification
from backend.enrollment import Enrollment
from shared.errors import ServiceError
from tests.auth_support import PASSWORD, AuthFixture


class EnrollmentOtpTests(unittest.TestCase):
    def setUp(self):
        self.fixture = AuthFixture()
        self.addCleanup(self.fixture.close)

    def ready_relay(self):
        self.fixture.ready_relay()

    def test_initialized_setup_never_reopens(self):
        with self.assertRaises(ServiceError):
            self.fixture.enrollment.issue_bootstrap()
        with self.assertRaises(ServiceError):
            self.fixture.enrollment.begin("wrong", "setup")

    def test_bootstrap_expires_and_is_checked_before_hashing(self):
        with self.fixture.database.transaction() as connection:
            connection.execute("UPDATE admin_auth SET initialized=0")
        token = self.fixture.enrollment.issue_bootstrap()
        self.fixture.now += 1800
        with self.assertRaises(ServiceError):
            self.fixture.enrollment.begin(token, "expired")
        with patch.object(type(self.fixture.hasher), "hash") as hashing:
            with self.assertRaises(ServiceError):
                self.fixture.enrollment.confirm(
                    {"token": "wrong", "password": PASSWORD, "code": "123456"}, "invalid"
                )
        hashing.assert_not_called()

    def test_enrollment_expiring_during_hash_cannot_commit(self):
        with self.fixture.database.transaction() as connection:
            connection.execute("UPDATE admin_auth SET initialized=0")
        token = self.fixture.enrollment.issue_bootstrap()
        material = self.fixture.enrollment.begin(token, "expiry")
        original = self.fixture.hasher

        class DelayedHasher:
            def hash(inner, value):
                self.fixture.now += 301
                return original.hash(value)

        auth = Authentication(
            dataclasses.replace(self.fixture.auth.dependencies, hasher=DelayedHasher())
        )
        payload = {
            "token": token,
            "password": PASSWORD,
            "code": pyotp.TOTP(material["secret"]).at(self.fixture.now),
        }
        with self.assertRaises(ServiceError) as caught:
            Enrollment(auth).confirm(payload, "expiry")
        self.assertEqual(caught.exception.code, "setup_denied")
        with self.fixture.database.transaction() as connection:
            self.assertEqual(
                connection.execute("SELECT initialized FROM admin_auth").fetchone()[0], 0
            )

    def test_bootstrap_wrong_expired_invalid_factor_and_competing_confirmations(self):
        with self.fixture.database.transaction() as connection:
            connection.execute("UPDATE admin_auth SET initialized=0,bootstrap_hash=NULL")
        enrollment = Enrollment(self.fixture.auth)
        token = enrollment.issue_bootstrap()
        for value in ("", "wrong"):
            with self.assertRaises(ServiceError):
                enrollment.begin(value, "setup2")
        secret = enrollment.begin(token, "setup2")["secret"]
        with self.assertRaises(ServiceError):
            enrollment.confirm({"token": token, "password": PASSWORD, "code": "invalid"}, "setup2")
        payload = {
            "token": token,
            "password": PASSWORD,
            "code": pyotp.TOTP(secret).at(self.fixture.now),
        }

        def confirm(_):
            try:
                enrollment.confirm(payload, "setup2")
                return True
            except ServiceError:
                return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sum(executor.map(confirm, range(2))), 1)
        with self.assertRaises(ServiceError):
            enrollment.begin(token, "setup2")

    def test_otp_replay_resend_and_challenge_binding(self):
        self.ready_relay()
        challenge = self.fixture.challenge()
        values = []
        attempt = Verification(challenge, "email", "", "send")
        self.fixture.auth.send_otp(attempt, values.append)
        with self.assertRaises(ServiceError):
            self.fixture.auth.send_otp(attempt, values.append)
        other = self.fixture.challenge()
        with self.assertRaises(ServiceError):
            self.fixture.auth.verify(Verification(other, "email", values[0], "factor"), "login")
        self.fixture.now += 60
        self.fixture.auth.send_otp(attempt, values.append)
        with self.assertRaises(ServiceError):
            self.fixture.auth.verify(Verification(challenge, "email", values[0], "factor"), "login")
        self.fixture.auth.verify(Verification(challenge, "email", values[-1], "factor"), "login")
        with self.assertRaises(ServiceError):
            self.fixture.auth.verify(
                Verification(challenge, "email", values[-1], "factor"), "login"
            )

    def test_incorrect_cross_challenge_and_expired_email_values_cannot_issue_a_session(self):
        self.ready_relay()
        challenge = self.fixture.challenge()
        sent = []
        self.fixture.auth.send_otp(Verification(challenge, "email", "", "send"), sent.append)
        wrong = "000000" if sent[0] != "000000" else "111111"
        with self.assertRaises(ServiceError):
            self.fixture.auth.verify(Verification(challenge, "email", wrong, "factor"), "login")
        other = self.fixture.challenge()
        with self.assertRaises(ServiceError):
            self.fixture.auth.verify(Verification(other, "email", sent[0], "factor"), "login")
        self.fixture.now += 300
        with self.assertRaises(ServiceError):
            self.fixture.auth.verify(Verification(challenge, "email", sent[0], "factor"), "login")
        with self.fixture.database.transaction() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)

    def test_disabled_email_capability_denies_send_and_verification(self):
        self.ready_relay()
        disabled = Authentication(
            AuthDependencies(
                self.fixture.database,
                self.fixture.keys,
                self.fixture.hasher,
                lambda: self.fixture.now,
                False,
            )
        )
        challenge = self.fixture.challenge()
        sent = []
        with self.assertRaises(ServiceError):
            disabled.send_otp(Verification(challenge, "email", "", "send"), sent.append)
        with self.assertRaises(ServiceError):
            disabled.verify(Verification(challenge, "email", "123456", "factor"), "login")
        self.assertEqual(sent, [])

    def test_resend_resamples_a_colliding_value_and_only_the_new_code_works(self):
        self.ready_relay()
        challenge = self.fixture.challenge()
        attempt = Verification(challenge, "email", "", "send")
        sent = []
        with patch("backend.auth.secrets.randbelow", side_effect=[123456, 123456, 654321]) as rng:
            self.fixture.auth.send_otp(attempt, sent.append)
            self.fixture.now += 60
            self.fixture.auth.send_otp(attempt, sent.append)
        self.assertEqual(rng.call_count, 3)
        self.assertEqual(sent, ["123456", "654321"])
        with self.assertRaises(ServiceError):
            self.fixture.auth.verify(Verification(challenge, "email", sent[0], "factor"), "login")
        token = self.fixture.auth.verify(
            Verification(challenge, "email", sent[1], "factor"), "login"
        )
        self.fixture.auth.require_session(token)

    def test_exhausted_collision_sampling_is_bounded_and_sends_no_replacement(self):
        self.ready_relay()
        challenge = self.fixture.challenge()
        attempt = Verification(challenge, "email", "", "send")
        sent = []
        with patch("backend.auth.secrets.randbelow", return_value=123456):
            self.fixture.auth.send_otp(attempt, sent.append)
        self.fixture.now += 60
        with patch("backend.auth.secrets.randbelow", return_value=123456) as rng:
            with self.assertRaisesRegex(ServiceError, "otp_generation_failed"):
                self.fixture.auth.send_otp(attempt, sent.append)
        self.assertEqual(rng.call_count, OTP_GENERATION_ATTEMPTS)
        self.assertEqual(sent, ["123456"])
        token = self.fixture.auth.verify(
            Verification(challenge, "email", sent[0], "factor"), "login"
        )
        self.fixture.auth.require_session(token)

    def test_five_attempt_limit_is_persistent(self):
        self.ready_relay()
        challenge = self.fixture.challenge()
        sent = []
        self.fixture.auth.send_otp(Verification(challenge, "email", "", "send"), sent.append)
        wrong = "000000" if sent[0] != "000000" else "111111"
        for _ in range(5):
            with self.assertRaises(ServiceError):
                self.fixture.auth.verify(Verification(challenge, "email", wrong, "factor"), "login")
        with self.assertRaises(ServiceError):
            self.fixture.auth.verify(Verification(challenge, "email", sent[0], "factor"), "login")
