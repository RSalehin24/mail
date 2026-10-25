import json
import subprocess
import unittest
from unittest.mock import patch

from tests.support import StateFixture
from tools.run_mock_host import MockHost, cloud_config
from tools.vm.mock_profile import MOCK_DOCKER_STARTUP_SECONDS
from tools.vm.prepare import (
    Partition,
    check_filesystem,
    extract_sparse_partition,
    install_changed_partition,
    install_partition,
)
from tools.vm.start import qemu_arguments


class MockHostTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.state = self.fixture.root / "mock.json"
        self.identifier = "11d22643-b7f6-4e73-957f-1bab53c10289"
        self.record = {
            "id": self.identifier,
            "host": "deployer@192.0.2.1",
            "container": "mail-admin-mock-host-" + self.identifier,
            "live_before": {},
        }
        self.state.write_text(json.dumps(self.record))

    def test_boot_configuration_uses_only_a_fixture_public_key_and_private_guest_swap(self):
        configuration = cloud_config("ssh-ed25519 fixture-public", "a" * 64)
        self.assertFalse(configuration["ssh_pwauth"])
        self.assertTrue(configuration["disable_root"])
        self.assertEqual(
            configuration["users"][0]["ssh_authorized_keys"], ["ssh-ed25519 fixture-public"]
        )
        self.assertEqual(configuration["users"][0]["sudo"], "ALL=(ALL) NOPASSWD:ALL")
        self.assertEqual(configuration["swap"]["filename"], "/swapfile")
        self.assertNotIn("/opt/maddy", str(configuration))
        self.assertNotIn("admin_data.db", str(configuration))

    def test_host_docker_contract_is_ready_before_cloud_init_marks_the_guest_ready(self):
        setup = cloud_config('ssh-ed25519 fixture-public', 'a' * 64)['runcmd'][0]
        self.assertIn('ln -s /usr/local/bin/docker /usr/bin/docker\n', setup)
        self.assertLess(setup.index('ln -s /usr/local/bin/docker /usr/bin/docker'),
                        setup.index('touch /var/lib/mail-admin-mock-ready'))

    def test_only_the_emulated_engine_uses_its_explicit_measured_startup_budget(self):
        files = cloud_config('ssh-ed25519 fixture-public', 'a' * 64)['write_files']
        docker = next(value['content'] for value in files
                      if value['path'] == '/etc/systemd/system/docker.service')
        self.assertIn(f'TimeoutStartSec={MOCK_DOCKER_STARTUP_SECONDS}\n', docker)

    def test_production_container_name_cannot_be_used_as_a_mock_target(self):
        self.state.write_text(json.dumps({**self.record, "container": "maddy"}))
        with patch("tools.run_mock_host.ssh") as remote:
            with self.assertRaises(ValueError):
                MockHost(self.state)
        remote.assert_not_called()

    def test_power_cut_requires_the_exact_owned_fixture_label(self):
        inspection = {"Config": {"Labels": {"mail-admin.mock-host": "another-fixture"}}}
        with patch("tools.run_mock_host.ssh", return_value=json.dumps([inspection])) as remote:
            with self.assertRaises(RuntimeError):
                MockHost(self.state).control("power-cut")
        remote.assert_called_once_with(
            self.record["host"], ("sudo", "-n", "docker", "inspect", self.record["container"])
        )

    def test_offline_partition_copy_overwrites_old_bytes_with_zero_blocks(self):
        disk, filesystem = self.fixture.root / "disk.raw", self.fixture.root / "root.raw"
        disk.write_bytes(b"header-stale-data-tail")
        filesystem.write_bytes(b"\0" * 10)
        install_partition((disk, filesystem), Partition(7, 10))
        self.assertEqual(disk.read_bytes(), b"header-" + b"\0" * 10 + b"-tail")

    def test_changed_partition_copy_preserves_unchanged_bytes_and_overwrites_new_zeros(self):
        disk, filesystem = self.fixture.root / "disk.raw", self.fixture.root / "root.raw"
        disk.write_bytes(b"header-stale-data-tail")
        filesystem.write_bytes(b"\0" * 10)
        install_changed_partition((disk, filesystem), Partition(7, 10))
        self.assertEqual(disk.read_bytes(), b"header-" + b"\0" * 10 + b"-tail")

    def test_new_sparse_partition_retains_its_full_zero_length_and_refuses_existing_files(self):
        disk, filesystem = self.fixture.root / "disk.raw", self.fixture.root / "root.raw"
        disk.write_bytes(b"header-" + b"\0" * 1024)
        extract_sparse_partition((disk, filesystem), Partition(7, 1024))
        self.assertEqual(filesystem.read_bytes(), b"\0" * 1024)
        with self.assertRaises(FileExistsError):
            extract_sparse_partition((disk, filesystem), Partition(7, 1024))

    def test_emulated_docker_uses_prestarted_containerd_instead_of_its_short_child_deadline(self):
        configuration = cloud_config("ssh-ed25519 fixture-public", "a" * 64)
        files = {entry["path"]: entry["content"] for entry in configuration["write_files"]}
        docker = files["/etc/systemd/system/docker.service"]
        self.assertIn("Requires=containerd.service", docker)
        self.assertIn("--containerd=/run/containerd/containerd.sock", docker)
        self.assertIn("Type=notify", files["/etc/systemd/system/containerd.service"])
        setup = configuration["runcmd"][0]
        self.assertIn("install -m 0755 /usr/local/bin/cli-plugins/docker-compose", setup)
        self.assertIn("docker compose version", setup)

    def test_unfixed_guest_filesystem_errors_fail_offline_preparation(self):
        failure = subprocess.CompletedProcess((), 4, "synthetic uncorrected filesystem", "")
        with patch("tools.vm.prepare.subprocess.run", return_value=failure):
            with self.assertRaisesRegex(RuntimeError, "Mock filesystem check failed"):
                check_filesystem(self.fixture.root / "guest.raw")

    def test_restarting_an_already_running_mock_cannot_reconfigure_it(self):
        inspection = {
            "Config": {"Labels": {"mail-admin.mock-host": self.identifier}},
            "State": {"Running": True},
        }
        with patch("tools.run_mock_host.ssh", return_value=json.dumps([inspection])) as remote:
            with self.assertRaisesRegex(RuntimeError, "already running"):
                MockHost(self.state).control("start-existing")
        self.assertEqual(remote.call_count, 1)

    def test_cloud_seed_uses_a_read_only_virtio_disk_with_stable_root_and_swap_order(self):
        arguments = qemu_arguments("256")
        drives = [
            arguments[index + 1] for index, value in enumerate(arguments) if value == "-drive"
        ]
        self.assertEqual(len(drives), 3)
        self.assertTrue(drives[0].startswith("file=/fixture/disk.qcow2,if=none,id=fixture-disk,"))
        self.assertTrue(drives[1].startswith("file=/fixture/swap.raw,if=none,id=fixture-swap,"))
        self.assertEqual(drives[2],
                         "file=/fixture/seed.iso,if=none,id=fixture-seed,format=raw,readonly=on")
        self.assertIn('iothread,id=fixture-io,thread-pool-min=1,thread-pool-max=2', arguments)
        devices = [arguments[i + 1] for i, value in enumerate(arguments) if value == '-device']
        self.assertEqual(devices, [f'virtio-blk-pci,drive=fixture-{name},iothread=fixture-io'
                                   for name in ('disk', 'swap', 'seed')])
        self.assertNotIn("-cdrom", arguments)

    def test_guest_connection_is_pinned_to_the_mock_and_bounded_for_software_emulation(self):
        inspection = {"NetworkSettings": {"Ports": {"2222/tcp": [{"HostPort": "32123"}]}}}
        with (
            patch.object(MockHost, "inspect", return_value=inspection),
            patch("tools.run_mock_host.command", return_value="fixture") as execute,
        ):
            self.assertEqual(MockHost(self.state).guest(("uname", "-m")), "fixture")
        arguments = execute.call_args.args[0]
        self.assertIn("ConnectTimeout=45", arguments)
        self.assertIn("ServerAliveInterval=15", arguments)
        self.assertIn("ServerAliveCountMax=3", arguments)
        self.assertIn("HostKeyAlgorithms=ssh-ed25519", arguments)
        self.assertIn("ControlPath=control", arguments)
        self.assertEqual(execute.call_args.args[1], self.state.parent.resolve())
        self.assertIn("HostKeyAlias=" + self.record["container"], arguments)
        self.assertIn("fixture@127.0.0.1", arguments)
        self.assertEqual(arguments[-1], "uname -m")
