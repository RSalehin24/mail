"""Reuse actual HTTPS enrollment and factor login assertions across runtime fixtures."""

import time

import pyotp
from argon2 import PasswordHasher

from backend.auth import AuthDependencies, Authentication
from backend.enrollment import Enrollment
from tests.auth_support import PASSWORD


class RuntimeAuthentication:
    def __init__(self, assertions, fixture):
        self.assertions, self.fixture = assertions, fixture

    def enroll(self):
        auth = Authentication(
            AuthDependencies(
                self.fixture.database,
                self.fixture.keys,
                PasswordHasher(time_cost=1, memory_cost=8192),
                time.time,
            )
        )
        token = Enrollment(auth).issue_bootstrap()
        status, headers, material = self.fixture.api("POST", "/api/setup", {"token": token})
        self.assertions.assertEqual(status, 200)
        self.assertions.assertEqual(headers["cache-control"], "no-store")
        status, _, result = self.fixture.api(
            "POST",
            "/api/setup",
            {"token": token, "password": PASSWORD, "code": pyotp.TOTP(material["secret"]).now()},
        )
        self.assertions.assertEqual(status, 200)
        self.codes = result["recovery_codes"]
        self.assertions.assertEqual(len(self.codes), 5)

    def login(self):
        status, _, result = self.fixture.api(
            "POST", "/api/login", {"email": "rsalehin24@gmail.com", "password": PASSWORD}
        )
        self.assertions.assertEqual(status, 200)
        status, headers, _ = self.fixture.api(
            "POST",
            "/api/2fa/verify",
            {"challenge": result["challenge"], "factor": "recovery", "code": self.codes.pop()},
        )
        self.assertions.assertEqual(status, 200)
        cookie = headers["set-cookie"]
        for attribute in ("Secure", "HttpOnly", "SameSite=strict"):
            self.assertions.assertIn(attribute, cookie)
        self.assertions.assertNotIn("Domain=", cookie)
        self.fixture.cookie = cookie.split(";", 1)[0]

    def reauthenticate(self):
        status, _, result = self.fixture.api(
            "POST",
            "/api/security/reauth",
            {"email": "rsalehin24@gmail.com", "password": PASSWORD},
        )
        self.assertions.assertEqual(status, 200)
        status, headers, grant = self.fixture.api(
            "POST",
            "/api/security/reauth/verify",
            {"challenge": result["challenge"], "factor": "recovery", "code": self.codes.pop()},
        )
        self.assertions.assertEqual(status, 200)
        self.assertions.assertEqual(grant["grant_seconds"], 300)
        self.assertions.assertNotIn("set-cookie", headers)
