import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from host.docker import Docker, DockerConsumer, DockerSnapshots
from shared.errors import ServiceError


class DockerFixture:
    timeout = 0.1

    def __init__(self):
        self.calls = []
        self.missing = False
        self.container = {
            "Image": "fixture-image",
            "State": {"Running": True},
            "Mounts": [{"Destination": "/tls", "Source": "/generations/current"}],
        }

    def inspect(self, name):
        if self.missing:
            raise ServiceError("docker_operation_failed", 503)
        return self.container

    def run(self, arguments, environment):
        self.calls.append((arguments, environment))
        self.container["Mounts"][0]["Source"] = environment["TLS_DIRECTORY"]


class ConsumerTests(unittest.TestCase):
    def setUp(self):
        self.docker = DockerFixture()
        self.record = {
            "container": "mail-admin",
            "image_id": "fixture-image",
            "tls_target": "/tls",
            "tls_port": 443,
            "tls_variable": "TLS_DIRECTORY",
            "environment": {},
            "compose_file": "/fixture/compose.yml",
            "service": "mail-admin",
        }
        self.consumer = DockerConsumer(self.record, self.docker)

    def test_valid_current_generation_does_not_recreate_consumer(self):
        with patch("host.docker.served_fingerprint", return_value="verified"):
            self.consumer.activate(Path("/generations/current"), {"served_sha256": "verified"})
        self.assertEqual(self.docker.calls, [])

    def test_missing_deployed_consumer_remains_pending(self):
        self.docker.missing = True
        with self.assertRaises(ServiceError):
            self.consumer.activate(Path("/generations/current"), {"served_sha256": "verified"})
        self.assertEqual(self.docker.calls, [])

    def test_new_generation_waits_for_served_certificate_after_recreation(self):
        with (
            patch("host.docker.served_fingerprint", side_effect=[OSError("starting"), "verified"]),
            patch("host.docker.time.sleep"),
            patch("host.docker.time.monotonic", side_effect=[0, 0.01, 0.02]),
        ):
            self.consumer.activate(Path("/generations/new"), {"served_sha256": "verified"})
        self.assertEqual(len(self.docker.calls), 1)
        self.assertIn("--pull", self.docker.calls[0][0])
        self.assertIn("never", self.docker.calls[0][0])

    def test_interrupted_pause_cannot_hide_a_running_writer_from_backup(self):
        generations = SimpleNamespace(
            read=lambda: {
                "consumers": {"maddy": {"intent": "maintenance", "reason": "backup_capture"}}
            }
        )
        snapshots = DockerSnapshots((self.docker, MagicMock(), generations))
        with self.assertRaisesRegex(ServiceError, "maintenance_consumer_unexpectedly_running"):
            snapshots.running()
        self.assertEqual(self.docker.calls, [])

    def test_offline_recovery_blocks_backup_even_when_the_web_container_is_stopped(self):
        generations = SimpleNamespace(
            read=lambda: {
                "consumers": {"mail-admin": {"intent": "maintenance", "reason": "offline_recovery"}}
            }
        )
        maddy = MagicMock()
        snapshots = DockerSnapshots((self.docker, maddy, generations))
        with self.assertRaisesRegex(ServiceError, "offline_recovery_in_progress"):
            snapshots.inventories()
        maddy.validate.assert_not_called()
        self.assertEqual(self.docker.calls, [])

    def test_maintenance_stop_verifies_restart_policy_before_stopping_and_confirms_exit(self):
        docker = Docker()
        with (
            patch.object(docker, "run") as execute,
            patch.object(
                docker,
                "inspect",
                side_effect=[
                    {"HostConfig": {"RestartPolicy": {"Name": "no"}}},
                    {"State": {"Running": False}},
                ],
            ),
        ):
            docker.stop_for_maintenance("mail-admin")
        self.assertEqual(
            [call.args[0] for call in execute.call_args_list],
            [("update", "--restart", "no", "mail-admin"), ("stop", "--time", "30", "mail-admin")],
        )

    def test_failed_restart_pause_does_not_stop_then_accidentally_autostart_the_dashboard(self):
        docker = Docker()
        with (
            patch.object(docker, "run") as execute,
            patch.object(
                docker,
                "inspect",
                return_value={"HostConfig": {"RestartPolicy": {"Name": "always"}}},
            ),
        ):
            with self.assertRaisesRegex(ServiceError, "consumer_restart_pause_incomplete"):
                docker.stop_for_maintenance("mail-admin")
        execute.assert_called_once_with(("update", "--restart", "no", "mail-admin"))
