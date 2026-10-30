"""Independent deployment, adoption and conservative retention invariants."""

import io
import json
import os
import sqlite3
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pyotp

from applications.bootstrap import bootstrap
from applications.host import deployment
from applications.identity import Identities
from applications.qualification import validate
from applications.runtime import load
from applications.schema import install
from applications.setup import ConfigureRequest, configure
from applications.trash import TrashAuthority, TrashBatch, TrashEvent
from backend.enrollment import Enrollment
from backend.webmail_models import MessageRef
from deployment.adopt import adopt
from deployment.install import compose, preflight
from deployment.package import inspect_packaged_image
from shared.errors import ServiceError
from shared.installation import site
from tests.api_support import ApiFixture
from tests.fixture_values import MAILBOX_PASSWORD
from tests.integration.test_independent_apps import NativeIdentity
from tests.webmail_support import IdentityImap, WebmailApiFixture


class IndependentDeploymentTests(unittest.TestCase):
    def test_portable_package_checks_filesystem_and_configuration_across_image_stores(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = {
                "architecture": "amd64",
                "os": "linux",
                "rootfs": {"diff_ids": ["sha256:" + "a" * 64]},
                "config": {"Cmd": ["python", "-m", "applications.runtime"]},
            }
            with tarfile.open(root / "image.tar", "w") as writer:
                for name, value in (
                    ("manifest.json", [{"Config": "config.json"}]),
                    ("config.json", expected),
                ):
                    data = json.dumps(value).encode()
                    header = tarfile.TarInfo(name)
                    header.size = len(data)
                    writer.addfile(header, io.BytesIO(data))
            actual = {
                "Id": "sha256:" + "b" * 64,
                "Architecture": "amd64",
                "Os": "linux",
                "RootFS": {"Layers": expected["rootfs"]["diff_ids"]},
                "Config": {**expected["config"], "OnBuild": None},
            }
            with patch(
                "deployment.package.subprocess.check_output", return_value=json.dumps([actual])
            ):
                self.assertEqual(
                    inspect_packaged_image(
                        root, {"image": "sha256:" + "c" * 64}, "mail-management:package"
                    )["Id"],
                    actual["Id"],
                )
            actual["Config"]["Cmd"] = ["unreviewed"]
            with patch(
                "deployment.package.subprocess.check_output", return_value=json.dumps([actual])
            ):
                with self.assertRaisesRegex(ValueError, "configuration mismatch"):
                    inspect_packaged_image(root, {}, "mail-management:package")

    def test_operator_context_preserves_live_mailbox_sessions_and_uploads(self):
        previous = os.environ["MAIL_CONFIG"]
        self.addCleanup(os.environ.__setitem__, "MAIL_CONFIG", previous)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bootstrap(
                root,
                "webmail",
                "web.example.test",
                {
                    "hash_memory_kib": 8192,
                    "hash_parallelism": 1,
                    "http_concurrency": 4,
                    "operation_timeout": 15,
                },
            )
            (root / "site.json").write_bytes(Path("tests/site.json").read_bytes())
            serving = load(root, "webmail")
            try:
                serving.webmail.auth.authority = None
                serving.webmail.auth.imap = IdentityImap()
                _, token = serving.webmail.auth.login(
                    "first@rsalehin24.me", MAILBOX_PASSWORD, "fixture"
                )
                attachment = serving.webmail.files.root / "in-progress"
                attachment.write_bytes(b"private attachment")
                operator = load(root, "webmail", mailbox_runtime=False)
                try:
                    self.assertIsNone(operator.webmail)
                    self.assertEqual(attachment.read_bytes(), b"private attachment")
                    self.assertEqual(
                        serving.webmail.auth.require(token).email, "first@rsalehin24.me"
                    )
                finally:
                    operator.close()
            finally:
                serving.close()

    def test_application_upgrade_keeps_the_running_native_launcher_mount(self):
        host = {
            "release": "/opt/mail-management/0.2.1",
            "maddy_image": "sha256:" + "a" * 64,
            "maddy_launcher_path": "/opt/mail-management/0.2.0/host/maddy-launcher",
        }
        self.assertEqual(
            deployment(host)["maddy_mounts"]["/usr/local/libexec/mail-admin-maddy"],
            host["maddy_launcher_path"],
        )

    def test_webmail_has_no_privileged_mount_or_container_access(self):
        config = compose(
            "webmail", Path("/var/lib/mail-webmail"), "sha256:" + "a" * 64, 200, 1000, 1000
        )
        service = config["services"]["application"]
        self.assertEqual(len(config["services"]), 1)
        self.assertTrue(service["read_only"])
        self.assertEqual([item["target"] for item in service["volumes"]], ["/state"])
        self.assertNotIn("docker.sock", json.dumps(config))
        self.assertNotIn("maddy", json.dumps(config))

    def test_management_has_separate_integration_listener_budget(self):
        config = compose("management", Path("/state"), "sha256:" + "a" * 64, 180, 1000, 1000)
        self.assertEqual(config["services"]["application"]["mem_limit"], "180m")
        self.assertEqual(config["services"]["integration"]["mem_limit"], "180m")
        self.assertEqual(config["services"]["integration"]["ports"], ["8444:8444"])

    def test_unknown_existing_installation_refused_before_docker_work(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "deployment.install.platform.freedesktop_os_release",
                return_value={"ID": "ubuntu", "VERSION_ID": "24.04"},
            ),
            patch("deployment.install.os.geteuid", return_value=0),
            patch("deployment.install.run") as runner,
        ):
            with self.assertRaisesRegex(ValueError, "Existing installation"):
                preflight("webmail", Path(directory), 128, "sha256:" + "a" * 64, None, None)
            runner.assert_not_called()

    def test_qualification_refuses_absent_one_host_or_failed_old_capacity(self):
        for report in (
            {},
            {"version": "0.2.0", "users": 10},
            json.loads(Path("verification/webmail-capacity-acceptance.json").read_text()),
        ):
            with self.assertRaises(ServiceError):
                validate(report)

    def test_bootstrap_does_not_overwrite_and_configures_independent_keys(self):
        previous = os.environ["MAIL_CONFIG"]
        self.addCleanup(os.environ.__setitem__, "MAIL_CONFIG", previous)
        resources = {
            "hash_memory_kib": 8192,
            "hash_parallelism": 1,
            "http_concurrency": 4,
            "operation_timeout": 15,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            one, two = root / "management", root / "webmail"
            one.mkdir()
            two.mkdir()
            token = bootstrap(one, "management", "manage.example.test", resources)
            second = bootstrap(two, "webmail", "web.example.test", resources)
            self.assertNotEqual(token, second)
            self.assertNotEqual(
                (one / "secrets/encryption.key").read_bytes(),
                (two / "secrets/encryption.key").read_bytes(),
            )
            with self.assertRaisesRegex(ValueError, "overwrite"):
                bootstrap(one, "management", "manage.example.test", resources)

    def test_browser_wizard_requires_mfa_and_cannot_reopen_initialized_setup(self):
        previous = os.environ["MAIL_CONFIG"]
        self.addCleanup(os.environ.__setitem__, "MAIL_CONFIG", previous)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = bootstrap(
                root,
                "webmail",
                "mail.rsalehin24.me",
                {
                    "hash_memory_kib": 8192,
                    "hash_parallelism": 1,
                    "http_concurrency": 4,
                    "operation_timeout": 15,
                },
            )
            context = load(root, "webmail")
            self.addCleanup(context.close)
            request = ConfigureRequest(
                token=token,
                domain="rsalehin24.me",
                hostname="mail.rsalehin24.me",
                mail_hostname="mail.rsalehin24.me",
                admin_email="admin@example.test",
                backup_destination="/var/backups/mail-webmail",
                fullchain=(root / "tls/active/fullchain.pem").read_text(),
                private_key=(root / "tls/active/privkey.pem").read_text(),
            )
            self.assertTrue(configure(context, root, request, "test")["configured"])
            enrollment = Enrollment(context.authentication)
            material = enrollment.begin(token, "test")
            with self.assertRaises(ServiceError):
                enrollment.confirm(
                    {"token": token, "password": MAILBOX_PASSWORD, "code": ""}, "test"
                )
            codes = enrollment.confirm(
                {
                    "token": token,
                    "password": MAILBOX_PASSWORD,
                    "code": pyotp.TOTP(material["secret"]).now(),
                },
                "test",
            )
            self.assertEqual(len(codes), 5)
            with self.assertRaises(ServiceError):
                configure(context, root, request, "test")
            with context.authentication.database.transaction() as connection:
                self.assertFalse(
                    json.loads(
                        connection.execute("SELECT settings FROM application_state").fetchone()[0]
                    )["webmail_enabled"]
                )

    def test_adoption_preserves_profiles_ids_labels_sends_without_copying_mail(self):
        fixture = WebmailApiFixture()
        self.addCleanup(fixture.close)
        previous = os.environ["MAIL_CONFIG"]
        self.addCleanup(os.environ.__setitem__, "MAIL_CONFIG", previous)
        old_config = site()
        _, token = fixture.services.webmail.auth.login(
            "first@rsalehin24.me", MAILBOX_PASSWORD, "test"
        )
        owner = fixture.services.webmail.auth.require(token)
        secret = pyotp.random_base32()
        with fixture.database.transaction() as connection:
            connection.execute(
                "UPDATE mailbox_users SET display_name='Preserved',totp_secret=? WHERE id=?",
                (fixture.keys.seal(secret, "mailbox-totp:" + owner.id), owner.id),
            )
            connection.execute(
                "INSERT INTO mailbox_labels VALUES('label',?,'Personal','#123456')", (owner.id,)
            )
            connection.execute(
                "INSERT INTO mailbox_sends(id,user_id,state,created,updated) "
                "VALUES('send',?,'submitting',0,0)",
                (owner.id,),
            )
        store = fixture.root / "native"
        store.mkdir()
        with sqlite3.connect(store / "imapsql.db") as connection:
            connection.executescript(
                "CREATE TABLE users(id INTEGER,username TEXT,inboxId INTEGER); "
                "CREATE TABLE mboxes(id INTEGER,uid INTEGER,name TEXT,uidvalidity INTEGER,"
                "specialuse TEXT);"
            )
            connection.execute("INSERT INTO users VALUES(1,'first@rsalehin24.me',1)")
            connection.execute("INSERT INTO mboxes VALUES(1,1,'INBOX',1234,'')")
        os.environ["MAIL_CONFIG"] = str(fixture.root / "absent-legacy-site.json")
        result = adopt(
            fixture.database,
            fixture.keys,
            store,
            old_config,
            fixture.root / "management",
            fixture.root / "webmail",
            "web.example.test",
            {
                "hash_memory_kib": 8192,
                "hash_parallelism": 1,
                "http_concurrency": 4,
                "operation_timeout": 15,
            },
        )
        self.assertFalse(result["mail_store_copied"])
        self.assertFalse(result["routing_changed"])
        with sqlite3.connect(fixture.root / "webmail/webmail.db") as connection:
            self.assertEqual(
                connection.execute("SELECT id,display_name FROM mailbox_users").fetchone(),
                (owner.id, "Preserved"),
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM mailbox_labels").fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute("SELECT state FROM mailbox_sends").fetchone()[0],
                "submission_uncertain",
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM mailbox_sessions").fetchone()[0], 0
            )
        with sqlite3.connect(fixture.root / "management/management.db") as connection:
            self.assertEqual(
                connection.execute("SELECT id FROM managed_mailbox_identities").fetchone()[0],
                owner.id,
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM mailbox_users").fetchone()[0], 0
            )


class TrashCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ApiFixture()
        self.addCleanup(self.fixture.close)
        self.database = self.fixture.database
        install(self.database, "management")
        identities = Identities(self.database, NativeIdentity())
        self.user = identities.lookup("first@rsalehin24.me")
        self.authority = TrashAuthority(self.database, identities)
        self.ref = MessageRef(folder="Trash", uidvalidity=99, uid=7)
        self.observed = {
            self.user["email"]: {
                "native_id": 1,
                "inbox_validity": 1234,
                "entries": {("Trash", 99, 7)},
            }
        }
        with self.database.transaction() as connection:
            connection.execute("UPDATE application_state SET cleanup_enabled=1")
            connection.execute(
                "INSERT INTO integration_credentials "
                "VALUES('cred','peer','rsalehin24.me','fp','secret',?,0,NULL,1,0)",
                (time.time() + 86400,),
            )

    def event(self, identifier, action, hold=""):
        return TrashEvent(
            id=identifier * 32,
            mailbox_id=self.user["id"],
            action=action,
            hold_id=hold,
            ref=self.ref if action not in {"hold", "ready"} else None,
        )

    def test_lost_events_and_stale_heartbeat_delay_cleanup(self):
        entered = self.event("a", "delete")
        hold = self.event("b", "hold")
        self.authority.receive("peer", TrashBatch(recovery_epoch="epoch", events=[hold, entered]))
        self.assertEqual(self.authority.due(self.observed, time.time()), [])
        self.authority.receive(
            "peer", TrashBatch(recovery_epoch="epoch", events=[self.event("c", "ready", hold.id)])
        )
        self.assertEqual(len(self.authority.due(self.observed, time.time())), 1)
        self.assertEqual(self.authority.due(self.observed, time.time() + 121), [])

    def test_idempotency_restoration_and_recovery_epoch_hold(self):
        entered = self.event("a", "entered")
        batch = TrashBatch(recovery_epoch="epoch", events=[entered])
        self.assertEqual(
            self.authority.receive("peer", batch), self.authority.receive("peer", batch)
        )
        self.authority.receive(
            "peer", TrashBatch(recovery_epoch="epoch", events=[self.event("b", "restored")])
        )
        with self.database.transaction() as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM managed_trash").fetchone()[0], 0
            )
        self.authority.receive("peer", TrashBatch(recovery_epoch="restored", events=[]))
        with self.database.transaction() as connection:
            self.assertEqual(
                connection.execute("SELECT cleanup_enabled FROM application_state").fetchone()[0], 0
            )
