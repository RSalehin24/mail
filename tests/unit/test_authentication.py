import concurrent.futures
import secrets
import threading
import unittest
from unittest.mock import patch

import pyotp

from backend.auth import AuthDependencies, Authentication, Credentials, Verification
from backend.factors import FactorAttempt, Factors, matching_step
from backend.offline import OfflineRecovery
from backend.security import Security
from shared.errors import ServiceError
from tests.auth_support import PASSWORD, AuthFixture


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = AuthFixture()
        self.addCleanup(self.fixture.close)

    def test_password_only_challenge_cannot_act_as_session(self):
        challenge = self.fixture.challenge()
        with self.assertRaises(ServiceError):
            self.fixture.auth.require_session(challenge)
        token = self.fixture.auth.verify(
            Verification(challenge, "totp", self.fixture.code(), "a"), "login"
        )
        self.fixture.auth.require_session(token)
        with self.assertRaises(ServiceError):
            self.fixture.auth.verify(
                Verification(challenge, "totp", self.fixture.code(), "a"), "login"
            )

    def test_totp_window_and_watermark_never_move_backward(self):
        for offset in (-60, 60):
            with self.assertRaises(ServiceError):
                self.fixture.auth.verify(
                    Verification(
                        self.fixture.challenge(),
                        "totp",
                        pyotp.TOTP(self.fixture.seed).at(self.fixture.now + offset),
                        "factor",
                    ),
                    "login",
                )
        future = pyotp.TOTP(self.fixture.seed).at(self.fixture.now + 30)
        self.fixture.auth.verify(
            Verification(self.fixture.challenge(), "totp", future, "factor"), "login"
        )
        with self.assertRaises(ServiceError):
            self.fixture.auth.verify(
                Verification(self.fixture.challenge(), "totp", self.fixture.code(), "factor"),
                "login",
            )

    def test_parallel_recovery_consumption_occurs_exactly_once(self):
        challenges = [self.fixture.challenge("password") for _ in range(4)]
        code = self.fixture.codes[0]

        def consume(challenge):
            try:
                self.fixture.auth.verify(
                    Verification(challenge, "recovery", code, "factor"), "login"
                )
                return True
            except ServiceError:
                return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            self.assertEqual(sum(executor.map(consume, challenges)), 1)

    def test_totp_and_email_verification_consume_each_value_once_under_concurrency(self):
        for factor in ("totp", "email"):
            self.consume_factor_once(factor)

    def test_recovery_regeneration_uses_128_bit_material_and_invalidates_the_old_set(self):
        old_codes = list(self.fixture.codes)
        session = self.fixture.login()
        self.fixture.reauth(session)
        pending = self.fixture.challenge()
        with patch("backend.auth_state.secrets.token_hex", wraps=secrets.token_hex) as random_code:
            new_codes = Security(self.fixture.auth).recovery_codes(session)
        self.assertEqual(len(new_codes), 5)
        self.assertEqual(len(set(new_codes)), 5)
        self.assertTrue(all(len(bytes.fromhex(value)) == 16 for value in new_codes))
        self.assertTrue(all(call.args == (16,) for call in random_code.call_args_list[:5]))
        with self.assertRaises(ServiceError):
            self.fixture.auth.require_session(session)
        with self.assertRaises(ServiceError):
            self.fixture.auth.verify(
                Verification(pending, "recovery", old_codes[0], "factor"), "login"
            )
        for old in old_codes:
            self.assert_old_recovery_denied(old)
        fresh = self.fixture.auth.verify(
            Verification(self.fixture.challenge(), "recovery", new_codes[0], "factor"), "login"
        )
        self.fixture.auth.require_session(fresh)

    def assert_old_recovery_denied(self, value):
        with self.assertRaises(ServiceError):
            self.fixture.auth.verify(
                Verification(self.fixture.challenge(), "recovery", value, "factor"), "login"
            )

    def consume_factor_once(self, factor):
        fixture = AuthFixture()
        self.addCleanup(fixture.close)
        challenges = [fixture.challenge("password") for _ in range(4)]
        code = fixture.code()
        if factor == "email":
            fixture.ready_relay()
            sent = []
            fixture.auth.send_otp(Verification(challenges[0], "email", "", "send"), sent.append)
            challenges, code = [challenges[0]] * 4, sent[0]

        def consume(challenge):
            try:
                return fixture.auth.verify(Verification(challenge, factor, code, "factor"), "login")
            except ServiceError:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            sessions = [value for value in executor.map(consume, challenges) if value is not None]
        self.assertEqual(len(sessions), 1, factor)
        fixture.auth.require_session(sessions[0])
        with fixture.database.transaction() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1)

    def test_exact_totp_digits_step_window_and_monotonic_consumption(self):
        secret, now = "JBSWY3DPEHPK3PXP", 1800000000
        current = now // 30
        for code in ("12345", "1234567", "１２３４５６", "+12345", "123 45"):
            self.assertIsNone(matching_step(secret, code, now))
        for offset in (-60, 60):
            self.assertIsNone(matching_step(secret, pyotp.TOTP(secret).at(now + offset), now))
        with self.fixture.database.transaction() as connection:
            connection.execute("UPDATE admin_auth SET totp_secret=?,last_step=?", (
                self.fixture.keys.seal(secret, "totp"), current - 2,
            ))
            factors = Factors(self.fixture.keys)
            for offset in (-30, 0, 30):
                code = pyotp.TOTP(secret).at(now + offset)
                self.assertEqual(len(code), 6)
                self.assertEqual(matching_step(secret, code, now), current + offset // 30)
                attempt = FactorAttempt("totp", code, "unused-for-totp")
                self.assertTrue(factors.consume(connection, (attempt, now)))
                self.assertFalse(factors.consume(connection, (attempt, now)))
            old = FactorAttempt("totp", pyotp.TOTP(secret).at(now - 30), "unused-for-totp")
            self.assertFalse(factors.consume(connection, (old, now)))
            self.assertEqual(connection.execute("SELECT last_step FROM admin_auth").fetchone()[0],
                             current + 1)

    def test_session_bound_reauth_and_expiration_preserve_absolute_expiry(self):
        session = self.fixture.login()
        with self.assertRaises(ServiceError):
            self.fixture.auth.require_session(session, recent=True)
        self.fixture.reauth(session)
        self.fixture.auth.require_session(session, recent=True)
        with self.fixture.database.transaction() as connection:
            before = connection.execute("SELECT absolute_expiry FROM sessions").fetchone()[0]
        self.fixture.now += 300
        with self.assertRaises(ServiceError):
            self.fixture.auth.require_session(session, recent=True)
        with self.fixture.database.transaction() as connection:
            self.assertEqual(
                connection.execute("SELECT absolute_expiry FROM sessions").fetchone()[0], before
            )
        self.fixture.now += 900
        with self.assertRaises(ServiceError):
            self.fixture.auth.require_session(session)

    def test_idle_expiry_uses_the_last_activity_and_expires_at_exactly_fifteen_minutes(self):
        session = self.fixture.login()
        self.fixture.now += 899
        self.fixture.auth.require_session(session)
        self.fixture.now += 899
        self.fixture.auth.require_session(session)
        self.fixture.now += 900
        with self.assertRaises(ServiceError) as caught:
            self.fixture.auth.require_session(session)
        self.assertEqual(caught.exception.code, "authentication_required")

    def test_twelve_hour_absolute_expiry_cannot_be_extended_by_activity(self):
        session = self.fixture.login()
        expires = self.fixture.now + 43200
        while self.fixture.now + 899 < expires:
            self.fixture.now += 899
            self.fixture.auth.require_session(session)
        self.fixture.now = expires - 1
        self.fixture.auth.require_session(session)
        self.fixture.now = expires
        with self.assertRaises(ServiceError) as caught:
            self.fixture.auth.require_session(session)
        self.assertEqual(caught.exception.code, "authentication_required")

    def test_wrong_purpose_and_session_binding_fail(self):
        session = self.fixture.login()
        challenge = self.fixture.auth.password_challenge(
            Credentials("rsalehin24@gmail.com", PASSWORD, "reauth"), session
        )
        for purpose, token in (("login", None), ("reauth", "other-session")):
            with self.assertRaises(ServiceError):
                self.fixture.auth.verify(
                    Verification(challenge, "recovery", self.fixture.codes[0], "factor", token),
                    purpose,
                )

    def test_password_change_revokes_old_challenges_and_preserves_consumed_recovery(self):
        session = self.fixture.login()
        self.fixture.reauth(session)
        pending = self.fixture.challenge()
        Security(self.fixture.auth).begin_totp(session)
        Security(self.fixture.auth).change_password(session, " replacement fixture password ")
        with self.assertRaises(ServiceError):
            self.fixture.auth.require_session(session)
        with self.assertRaises(ServiceError):
            self.fixture.auth.verify(
                Verification(pending, "recovery", self.fixture.codes[0], "factor"), "login"
            )
        with self.fixture.database.transaction() as connection:
            self.assertIsNone(
                connection.execute("SELECT pending_secret FROM admin_auth").fetchone()[0]
            )
            self.assertEqual(
                connection.execute("SELECT SUM(consumed) FROM recovery_codes").fetchone()[0], 1
            )

    def test_delayed_old_password_worker_cannot_create_new_revision_challenge(self):
        session = self.fixture.login()
        self.fixture.reauth(session)
        entered, release = threading.Event(), threading.Event()
        original = self.fixture.auth.dependencies.hasher

        class DelayedHasher:
            def verify(inner, encoded, value):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("test barrier expired")
                return original.verify(encoded, value)

        delayed = Authentication(
            AuthDependencies(
                self.fixture.database,
                self.fixture.keys,
                DelayedHasher(),
                lambda: self.fixture.now,
                True,
            )
        )
        with concurrent.futures.ThreadPoolExecutor() as executor:
            attempt = executor.submit(
                delayed.password_challenge, Credentials("rsalehin24@gmail.com", PASSWORD, "racing")
            )
            self.assertTrue(entered.wait(5))
            Security(self.fixture.auth).change_password(session, " replacement fixture password ")
            release.set()
            with self.assertRaises(ServiceError):
                attempt.result(5)

    def test_concurrent_old_factor_verification_cannot_issue_a_session_after_password_change(self):
        session = self.fixture.login()
        self.fixture.reauth(session)
        pending = self.fixture.challenge()
        entered, release = threading.Event(), threading.Event()
        original = self.fixture.auth.reserve

        def reserve(peer):
            now = original(peer)
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test barrier expired")
            return now

        with (patch.object(self.fixture.auth, "reserve", side_effect=reserve),
              concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor):
            attempt = executor.submit(self.fixture.auth.verify, Verification(
                pending, "recovery", self.fixture.codes[0], "racing-factor",
            ), "login")
            try:
                self.assertTrue(entered.wait(5))
                Security(self.fixture.auth).change_password(session, " replacement password ")
            finally:
                release.set()
            with self.assertRaises(ServiceError):
                attempt.result(5)
        with self.fixture.database.transaction() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)

    def test_replacement_requires_recent_factor_and_revokes_old_seed(self):
        session = self.fixture.login()
        security = Security(self.fixture.auth)
        with self.assertRaises(ServiceError):
            security.begin_totp(session)
        self.fixture.reauth(session)
        material = security.begin_totp(session)
        self.fixture.now += 30
        with self.assertRaises(ServiceError):
            security.confirm_totp(session, ("bad-code", "replacement"))
        new_codes = security.confirm_totp(
            session, (pyotp.TOTP(material["secret"]).at(self.fixture.now), "replacement")
        )
        self.assertEqual(len(new_codes), 5)
        with self.assertRaises(ServiceError):
            self.fixture.auth.require_session(session)
        with self.fixture.database.transaction() as connection:
            row = connection.execute("SELECT totp_secret FROM admin_auth").fetchone()
            self.assertEqual(self.fixture.keys.open(row[0], "totp"), material["secret"])

    def test_interrupted_offline_recovery_stays_closed(self):
        session = self.fixture.login()
        recovery = OfflineRecovery(self.fixture.auth)
        recovery.begin()
        with self.assertRaises(ServiceError):
            self.fixture.auth.require_session(session)
        with self.assertRaises(ServiceError):
            recovery.complete(PASSWORD, (self.fixture.seed, "incorrect"))
        self.fixture.now += 30
        recovery.complete(PASSWORD, (self.fixture.seed, self.fixture.code()))
        with self.assertRaises(ServiceError):
            self.fixture.auth.require_session(session)
