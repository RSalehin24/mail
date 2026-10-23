import hashlib
import os
import unittest
import uuid

from helper.protocol import HelperRequest
from host.docker import Docker
from host.release_files import ReleaseFiles, RollbackPackage
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import RealMaddyFixture, imap_login
from tests.restore_support import bodies, deliver


class RealRollbackPackageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RealMaddyFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.start()

    def test_pinned_image_and_host_file_restore_preserves_real_mail_newer_than_the_package(self):
        email = "rollback-preservation@rsalehin24.me"
        result = self.fixture.helper.handle(
            HelperRequest("create", str(uuid.uuid4()), email, MAILBOX_PASSWORD)
        )
        self.assertEqual(result["state"], "complete")
        deliver(self.fixture, email, "before-rollback-package")
        target = self.fixture.configuration
        original = target.read_bytes()
        source = self.fixture.root / "candidate.conf"
        source.write_bytes(original + b"\n# Candidate fixture configuration\n")
        source.chmod(0o600)
        files = ReleaseFiles(
            {
                "maddy_configuration": {
                    "source": str(source),
                    "target": str(target),
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "mode": 0o600,
                }
            },
            (os.getuid(), (target,)),
        )
        package = RollbackPackage(
            self.fixture.root / "rollback-set", files, Docker(self.fixture.docker, 30)
        )
        release = {
            "artifact_set_sha256": "a" * 64,
            "schema_version": 1,
            "helper_protocol": 1,
            "tls_layout": "legacy",
            "consumers": {"maddy": {"image_id": self.fixture.maddy_image}},
        }
        identity = package.create(release)
        files.candidate()
        deliver(self.fixture, email, "after-rollback-package-newer-mail")
        self.assertEqual(package.restore(identity), release)
        self.assertEqual(target.read_bytes(), original)
        self.fixture.command(("restart", self.fixture.identifier))
        self.fixture.refresh_ports()
        self.fixture._wait()
        with imap_login(self.fixture, email, MAILBOX_PASSWORD) as connection:
            observed = bodies(connection)
        self.assertIn(b"before-rollback-package", observed)
        self.assertIn(b"after-rollback-package-newer-mail", observed)
        self.fixture.maddy.validate()
        inventory = self.fixture.maddy.inventories()
        self.assertEqual(inventory, {"credentials": [email], "storage": [email]})
