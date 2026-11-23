"""The dashboard manages real Maddy accounts with no root helper and no Docker access."""

import imaplib
import json
import os
import uuid

from tests.maddy_support import SERVICE_ID, imap_login
from tests.restore_support import bodies, deliver
from tests.runtime_mail_support import MailFlowFixture

DOCKER_SOCKET = "docker.sock"
NO_DOCKER_PROBE = (
    "import json, os, shutil;"
    "print(json.dumps({'client': shutil.which('docker'),"
    " 'socket': os.path.exists('/var/run/docker.sock'),"
    " 'host': os.environ.get('DOCKER_HOST'), 'uid': os.geteuid()}))"
)


class LocalHostFlowTests(MailFlowFixture):
    def create_mailbox(self, email, password):
        payload = {
            "request_id": str(uuid.uuid4()),
            "email": email,
            "password": password,
            "display_name": "Local host fixture",
            "notes": "Synthetic mail only",
        }
        self.mutation("/api/accounts/create", payload)
        self.mutation("/api/accounts/create", payload)  # A replayed request must not repeat.
        return payload

    def test_web_api_creates_and_changes_a_real_mailbox_without_a_helper(self):
        email = "local-host@rsalehin24.me"
        original, replacement = "  local host original password  ", "  local host new password  "
        payload = self.create_mailbox(email, original)
        deliver(self.maddy, email, "local-host-body-preserved")
        with imap_login(self.maddy, email, original) as established:
            self.assertIn(b"local-host-body-preserved", bodies(established))
            self.mutation(
                "/api/accounts/" + email + "/password",
                {"request_id": str(uuid.uuid4()), "password": replacement},
            )
            self.assertEqual(established.noop()[0], "OK")
        with self.assertRaises(imaplib.IMAP4.error):
            imap_login(self.maddy, email, original)
        with imap_login(self.maddy, email, replacement) as fresh:
            self.assertIn(b"local-host-body-preserved", bodies(fresh))
        status, _, accounts = self.web.api("GET", "/api/accounts")
        self.assertEqual(status, 200)
        self.assertEqual([account["email"] for account in accounts], [email])
        self.assertEqual(accounts[0]["display_name"], payload["display_name"])
        database = self.web.database.path.read_bytes()
        for password in (original, replacement):
            self.assertNotIn(password.encode(), database)

    def test_state_and_mail_files_belong_to_the_dashboard_user(self):
        self.create_mailbox("owner-check@rsalehin24.me", "owner check fixture password")
        paths = (
            self.web.state,
            self.web.state / "state.db",
            self.web.lock,
            self.maddy.data / "credentials.db",
            self.maddy.data / "imapsql.db",
        )
        for path in paths:
            with self.subTest(path=path.name):
                self.assertEqual(os.stat(path).st_uid, SERVICE_ID)
        self.assertIsNotNone(self.web.journal())

    def test_dashboard_container_has_no_docker_access_and_no_helper_socket(self):
        record = json.loads(self.web.command(("inspect", self.web.identifier)).stdout)[0]
        destinations = {mount["Destination"] for mount in record["Mounts"]}
        sources = {mount["Source"] for mount in record["Mounts"]}
        self.assertNotIn("/run/mail-admin-helper", destinations)
        self.assertFalse(any(DOCKER_SOCKET in path for path in destinations | sources))
        host = record["HostConfig"]
        self.assertFalse(host["Privileged"])
        self.assertEqual(host["CapDrop"], ["ALL"])
        self.assertTrue(host["ReadonlyRootfs"])
        self.assertEqual(record["Config"]["User"], f"{SERVICE_ID}:{SERVICE_ID}")
        probe = self.web.command(("exec", self.web.identifier, "python", "-c", NO_DOCKER_PROBE))
        self.assertEqual(
            json.loads(probe.stdout),
            {"client": None, "socket": False, "host": None, "uid": SERVICE_ID},
        )
