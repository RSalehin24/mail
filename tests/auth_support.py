import pyotp
from argon2 import PasswordHasher

from backend.auth import AuthDependencies, Authentication, Credentials, Verification
from backend.enrollment import Enrollment
from tests.fixture_values import PASSWORD
from tests.support import StateFixture


class AuthFixture(StateFixture):
    def __init__(self):
        super().__init__()
        self.hasher = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)
        self.auth = Authentication(
            AuthDependencies(self.database, self.keys, self.hasher, lambda: self.now, True)
        )
        self.enrollment = Enrollment(self.auth)
        token = self.enrollment.issue_bootstrap()
        material = self.enrollment.begin(token, "setup")
        self.seed = material["secret"]
        self.codes = self.enrollment.confirm(
            {"token": token, "password": PASSWORD, "code": self.code()}, "setup"
        )
        self.now += 30

    def code(self):
        return pyotp.TOTP(self.seed).at(self.now)

    def ready_relay(self):
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO relay_settings VALUES(1,?,?,?,?,?,1,'confirmed-fixture')",
                ("smtp-relay.brevo.com", 587, "fixture", "encrypted", "encrypted"),
            )
            connection.execute(
                "INSERT INTO relay_tests VALUES('confirmed-fixture',1,?,?,1,1,1,?)",
                ("mail@rsalehin24.me", "rsalehin24@gmail.com", self.now),
            )

    def challenge(self, peer="login"):
        return self.auth.password_challenge(Credentials("rsalehin24@gmail.com", PASSWORD, peer))

    def login(self):
        challenge = self.challenge()
        return self.auth.verify(Verification(challenge, "totp", self.code(), "login"), "login")

    def reauth(self, session):
        challenge = self.auth.password_challenge(
            Credentials("rsalehin24@gmail.com", PASSWORD, "reauth"), session
        )
        return self.auth.verify(
            Verification(challenge, "recovery", self.codes.pop(), "reauth", session), "reauth"
        )
