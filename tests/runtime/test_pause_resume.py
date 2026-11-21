"""Real Maddy and dashboard containers stopped and resumed by the backup's `DockerSnapshots`.

Both serve a real fixture certificate; resuming must see the certificate each served before the
pause, and a crash between pause and resume must stay visible in the persisted record.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from host import certificates
from host.consumers import ConsumerIntents
from host.docker import DockerSnapshots
from shared.errors import ServiceError
from tests.maddy_support import UnprivilegedMaddyFixture
from tests.runtime_support import RuntimeFixture

THRESHOLDS = {"disk_free_minimum_bytes": 1024, "inode_free_minimum": 10,
              "recovery_point_seconds": 93600}
TLS_PORTS = {
    ("127.0.0.1", 993): ("maddy", "993/tcp"),
    ("127.0.0.1", 443): ("mail-admin", "8443/tcp"),
}


class FixtureDocker:
    """The Docker adapter DockerSnapshots expects, bound to the two fixture containers."""

    timeout = 15

    def __init__(self, containers):
        self.containers = containers

    def inspect(self, name):
        container = self.containers[name]
        return json.loads(container.command(("inspect", container.identifier)).stdout)[0]

    def run(self, arguments):
        if arguments[:-1] not in (("stop", "--time", "30"), ("start",)):
            raise AssertionError("Unexpected consumer operation")
        name = arguments[-1]
        self.containers[name].command(arguments[:-1] + (self.containers[name].identifier,))
        return ""

    def published(self, endpoint):
        name, target = TLS_PORTS[endpoint]
        # A restarted container may be given a different ephemeral host port.
        ports = self.inspect(name)["NetworkSettings"]["Ports"][target]
        return ("127.0.0.1", int(ports[0]["HostPort"]))


class PauseResumeTests(unittest.TestCase):
    def setUp(self):
        maddy = UnprivilegedMaddyFixture()
        self.addCleanup(maddy.close)
        maddy.start()
        self.web = RuntimeFixture(maddy, THRESHOLDS)
        self.addCleanup(self.web.close)
        self.web.start()
        self.docker = FixtureDocker({"maddy": maddy, "mail-admin": self.web})
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        bundle = Path(directory.name) / "trust.pem"
        bundle.write_text(Path(maddy.certificates.trust).read_text()
                          + Path(self.web.certificates.trust).read_text())
        self.environment = patch.dict(os.environ, {"SSL_CERT_FILE": str(bundle)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.intents = ConsumerIntents(Path(directory.name) / "consumers.json", os.getuid())
        self.snapshots = DockerSnapshots(
            (self.docker, MagicMock(), self.intents), startup_timeout=15
        )
        real_fingerprint, real_verify = certificates.served_fingerprint, certificates.verify_served
        for target, real in (("host.docker.served_fingerprint", real_fingerprint),
                             ("host.docker.verify_served", real_verify)):
            patcher = patch(target, side_effect=self.translated(real))
            patcher.start()
            self.addCleanup(patcher.stop)

    def translated(self, real):
        def call(endpoint, *arguments):
            return real(self.docker.published(endpoint), *arguments)
        return call

    def fingerprints(self):
        return {name: certificates.served_fingerprint(self.docker.published(endpoint))
                for endpoint, (name, _) in TLS_PORTS.items()}

    def running(self):
        return {name: self.docker.inspect(name)["State"]["Running"]
                for name in self.docker.containers}

    def test_both_writers_stop_and_resume_serving_the_certificate_they_served_before(self):
        before = self.fingerprints()
        self.assertEqual(self.snapshots.running(), ["maddy", "mail-admin"])
        self.snapshots.pause(["maddy", "mail-admin"])
        self.assertEqual(self.running(), {"maddy": False, "mail-admin": False})
        recorded = self.intents.read()["consumers"]
        self.assertEqual({record["intent"] for record in recorded.values()}, {"maintenance"})
        self.assertEqual(
            {name: record["served_sha256"] for name, record in recorded.items()}, before
        )
        self.snapshots.resume(["maddy", "mail-admin"])
        self.assertEqual(self.running(), {"maddy": True, "mail-admin": True})
        self.assertEqual(self.fingerprints(), before)
        consumers = self.intents.read()["consumers"]
        self.assertEqual({record["intent"] for record in consumers.values()}, {"expected_running"})

    def test_only_previously_running_consumers_resume(self):
        self.docker.run(("stop", "--time", "30", "mail-admin"))
        names = self.snapshots.running()
        self.assertEqual(names, ["maddy"])
        self.snapshots.pause(names)
        self.snapshots.resume(names)
        self.assertEqual(self.running(), {"maddy": True, "mail-admin": False})

    def test_a_consumer_started_during_a_crashed_pause_blocks_the_next_capture(self):
        self.snapshots.pause(["maddy", "mail-admin"])
        self.docker.run(("start", "maddy"))
        survivor = DockerSnapshots((self.docker, MagicMock(), self.intents))
        with self.assertRaisesRegex(ServiceError, "maintenance_consumer_unexpectedly_running"):
            survivor.running()
        self.docker.run(("stop", "--time", "30", "maddy"))
        self.snapshots.resume(["maddy", "mail-admin"])
        self.assertEqual(self.running(), {"maddy": True, "mail-admin": True})


if __name__ == "__main__":
    unittest.main()
