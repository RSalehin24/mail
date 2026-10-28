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

    def test_graceful_stop_has_time_for_declared_grace_but_other_bounds_are_unchanged(self):
        from helper.process import CommandResult
        docker = Docker(timeout=5)
        with patch('host.docker.execute', return_value=CommandResult(0, '')) as execute:
            docker.run(('stop', '--time', '30', 'maddy'))
            self.assertEqual(execute.call_args.args[0].timeout, 35)
            docker.run(('inspect', 'maddy'))
            self.assertEqual(execute.call_args.args[0].timeout, 5)

    def test_canceled_start_retries_only_known_stopped_container_and_keeps_other_errors(self):
        docker = MagicMock()
        docker.run.side_effect = [ServiceError('command_completion_unknown', 503), '']
        docker.inspect.return_value = {'State': {'Running': False, 'Status': 'exited',
                                                 'Error': 'Canceled: context canceled'}}
        snapshots = DockerSnapshots((docker, MagicMock(), MagicMock()))
        snapshots._start_existing('maddy')
        self.assertEqual(docker.run.call_count, 2)
        docker.reset_mock()
        docker.run.side_effect = ServiceError('docker_operation_failed', 503)
        docker.inspect.return_value = {'State': {'Running': False, 'Status': 'exited',
                                                 'Error': 'permission denied'}}
        with self.assertRaises(ServiceError):
            snapshots._start_existing('maddy')
        self.assertEqual(docker.run.call_count, 1)

class SnapshotReadinessTests(unittest.TestCase):
    def test_tls_readiness_retries_within_existing_budget(self):
        docker = SimpleNamespace(timeout=5)
        snapshots = DockerSnapshots((docker, MagicMock(), MagicMock()))
        with (
            patch('host.docker.time.monotonic', side_effect=[0, 0.1, 0.2, 0.3, 0.4]),
            patch('host.docker.time.sleep'),
            patch(
                'host.docker.verify_served', side_effect=[ConnectionRefusedError(), None],
            ) as probe,
        ):
            snapshots._verify_resumed('maddy', 'expected')
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(probe.call_args.args[:2], (('127.0.0.1', 993), 'expected'))
        self.assertLess(probe.call_args.args[2], 5)

    def test_tls_readiness_does_not_extend_budget(self):
        snapshots = DockerSnapshots((SimpleNamespace(timeout=5), MagicMock(), MagicMock()))
        with (
            patch('host.docker.time.monotonic', side_effect=[0, 0.1, 5.1]),
            patch('host.docker.verify_served', side_effect=ConnectionRefusedError()),
        ):
            with self.assertRaises(ConnectionRefusedError):
                snapshots._verify_resumed('mail-admin', 'expected')


class MaintenanceBudgetTests(unittest.TestCase):
    def test_backup_lifecycle_does_not_reuse_the_short_api_cli_deadline(self):
        from host.backup import FULL_STATE_SOURCES
        from host.context import HostContext
        from host.docker import Docker
        deployment = {'inputs': {'operation_timeout_seconds': 5},
                      'backup_sources': {name: '/fixture/' + name for name in FULL_STATE_SOURCES},
                      'maddy_message_paths': ['messages']}
        context = HostContext(deployment, Docker(timeout=5), MagicMock(), MagicMock(), MagicMock())
        with patch('host.context.backup_service', side_effect=lambda capture, *_: capture):
            capture = context.backup()
        self.assertEqual(context.docker.timeout, 5)
        self.assertEqual(capture.dependencies.services.docker.timeout, 30)
        self.assertEqual(capture.dependencies.services.startup_timeout, 30)
