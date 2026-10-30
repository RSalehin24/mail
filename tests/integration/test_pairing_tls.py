"""Real TLS listener checks, including client certificate binding and credential rotation."""

import http.client
import socket
import ssl
import threading
import time
import unittest
from unittest.mock import patch

import uvicorn

from applications.certificates import ca, csr, issue, pem_key, private_key
from applications.client import ManagementClient
from applications.identity import Identities
from applications.integration_api import create_integration_app
from applications.pairing import PairingAuthority
from applications.schema import install
from applications.tls_protocol import MutualTLSProtocol
from shared.errors import ServiceError
from tests.api_support import ApiFixture
from tests.integration.test_independent_apps import NativeIdentity


class MutualTLSListenerTests(unittest.TestCase):
    def setUp(self):
        self.management, self.webmail = ApiFixture(), ApiFixture()
        self.addCleanup(self.management.close)
        self.addCleanup(self.webmail.close)
        install(self.management.database, "management")
        install(self.webmail.database, "webmail")
        identifier = "00000000-0000-4000-8000-000000000002"
        self.webmail.database.identity = identifier
        with self.webmail.database.transaction() as connection:
            connection.execute("UPDATE installation SET identity=?", (identifier,))
        ca_key, self.ca = ca("integration transport fixture")
        self.authority = PairingAuthority(
            self.management.database, self.management.keys, ca_key, self.ca
        )
        key = private_key()
        cert, _ = issue(ca_key, self.ca, csr(key, "server"), "mail.rsalehin24.me", 300, server=True)
        for name, content in (
            ("server.pem", cert),
            ("server.key", pem_key(key)),
            ("ca.pem", self.ca),
        ):
            (self.management.root / name).write_text(content)
        app = create_integration_app(
            self.authority,
            Identities(self.management.database, NativeIdentity()),
            self.management.services.mailboxes,
        )
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.server = uvicorn.Server(
            uvicorn.Config(
                app,
                http=MutualTLSProtocol,
                host="127.0.0.1",
                port=self.port,
                ssl_certfile=str(self.management.root / "server.pem"),
                ssl_keyfile=str(self.management.root / "server.key"),
                ssl_ca_certs=str(self.management.root / "ca.pem"),
                ssl_cert_reqs=ssl.CERT_REQUIRED,
                access_log=False,
                log_level="critical",
                timeout_keep_alive=1,
            )
        )
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [self.listener]}, daemon=True
        )
        self.thread.start()
        self.addCleanup(self.stop)
        deadline = time.monotonic() + 5
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.server.started)
        original = socket.getaddrinfo

        def resolve(host, *args, **kwargs):
            return original("127.0.0.1" if host == "mail.rsalehin24.me" else host, *args, **kwargs)

        self.resolver = patch("socket.getaddrinfo", side_effect=resolve)
        self.resolver.start()
        self.addCleanup(self.resolver.stop)
        self.client = ManagementClient(self.webmail.database, self.webmail.keys)
        self.addCleanup(self.client.close)

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)
        self.listener.close()

    def test_real_mutual_tls_enrollment_identity_and_rotation(self):
        token = self.authority.enrollment(f"https://mail.rsalehin24.me:{self.port}")[
            "enrollment_token"
        ]
        self.assertTrue(self.client.enroll(token)["connected"])
        borrowed = next(iter(self.client.connections.idle.values()))[0][0]
        self.assertTrue(self.client.status()["connected"])
        self.assertIs(next(iter(self.client.connections.idle.values()))[0][0], borrowed)
        identity = self.client.identify("first@rsalehin24.me")
        self.assertEqual(identity["inbox_validity"], 1234)
        with self.assertRaisesRegex(ServiceError, "account_not_found"):
            self.client.identify("missing@rsalehin24.me")
        old = self.client.load()
        self.assertTrue(self.client.rotate()["connected"])
        self.assertNotEqual(self.client.load()["credential_id"], old["credential_id"])
        self.client.save(old)
        with self.assertRaisesRegex(ServiceError, "integration_credential_revoked_or_expired"):
            self.client.status()

    def test_missing_client_certificate_rejected_at_transport(self):
        context = ssl.create_default_context(cadata=self.ca)
        connection = http.client.HTTPSConnection(
            "mail.rsalehin24.me", self.port, context=context, timeout=2
        )
        try:
            with self.assertRaises(
                (OSError, http.client.RemoteDisconnected)
            ):
                connection.request(
                    "GET", "/integration/v1/status", headers={"X-Client-Certificate": "forged"}
                )
                connection.getresponse()
        finally:
            connection.close()

    def test_wrong_ca_is_rejected_and_disconnected_operation_fails_closed(self):
        token = self.authority.enrollment(f"https://mail.rsalehin24.me:{self.port}")[
            "enrollment_token"
        ]
        self.client.enroll(token)
        saved = self.client.load()
        _, unrelated_ca = ca("unrelated")
        self.client.save({**saved, "ca": unrelated_ca})
        with self.assertRaisesRegex(ServiceError, "management_connection_unavailable"):
            self.client.identify("first@rsalehin24.me")
        self.client.save(saved)
        self.stop()
        with self.assertRaisesRegex(ServiceError, "management_connection_unavailable"):
            self.client.identify("first@rsalehin24.me")
