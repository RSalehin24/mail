import json
import shlex
import unittest
from unittest.mock import patch

from tests.support import StateFixture
from tools.run_local_mock import (
    LocalMock,
    create,
    launch_arguments,
    local_engine,
    require_capacity,
    verify_capacity_image,
)
from tools.vm.mock_profile import LOCAL_CONTAINER_MEMORY_MIB
from tools.vm.start import guest_memory, qemu_arguments


class LocalMockTests(unittest.TestCase):
    def test_kvm_profile_exposes_only_virtualization_without_expanding_limits(self):
        record = {**self.record, 'acceleration': 'kvm', 'engine_architecture': 'amd64'}
        arguments = launch_arguments(record)
        self.assertEqual(arguments[arguments.index('--device') + 1], '/dev/kvm:/dev/kvm:rw')
        self.assertEqual(arguments[arguments.index('--memory') + 1], '1280m')
        self.assertEqual(arguments[arguments.index('--cpus') + 1], '1')
        self.assertNotIn('--privileged', arguments)
        with patch.dict('os.environ', {'MOCK_ACCELERATION': 'kvm',
                                     'MOCK_LOCAL_REPLACEMENT': '1'}):
            qemu = qemu_arguments('1024')
            self.assertEqual(qemu[qemu.index('-accel') + 1], 'kvm')
            self.assertEqual(qemu[qemu.index('-cpu') + 1], 'host')
        with (patch.dict('os.environ', {'MOCK_ACCELERATION': 'kvm'}, clear=True),
              self.assertRaisesRegex(RuntimeError, 'explicit local')):
            qemu_arguments('1024')

    def test_kvm_cannot_be_selected_without_the_device_or_on_an_arm_engine(self):
        for architecture, access in (('amd64', False), ('arm64', True)):
            with (self.subTest(architecture=architecture),
                  patch('tools.run_local_mock.local_engine', return_value={
                      'Architecture': architecture}), patch('tools.run_local_mock.os.umask'),
                  patch('tools.run_local_mock.os.access', return_value=access),
                  patch('tools.run_local_mock.docker') as docker,
                  self.assertRaisesRegex(RuntimeError, 'virtualization device')):
                create('fixture', 'kvm')
            docker.assert_not_called()

    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.state = self.fixture.root / "state.json"
        identifier = "11d22643-b7f6-4e73-957f-1bab53c10289"
        self.name = "mail-admin-local-mock-" + identifier
        self.record = {
            "id": identifier, "container": self.name, "volume": self.name + "-disk",
            "image_id": "sha256:" + "a" * 64, "container_id": "b" * 64, "engine_id": "engine",
        }
        self.state.write_text(json.dumps(self.record))
        self.inspection = {
            "Id": self.record["container_id"], "Image": self.record["image_id"],
            "Config": {"Labels": {"mail-admin.local-mock": identifier}},
            "HostConfig": {
                "Privileged": False, "ReadonlyRootfs": True,
                "Memory": LOCAL_CONTAINER_MEMORY_MIB * 1024 * 1024,
                "MemorySwap": LOCAL_CONTAINER_MEMORY_MIB * 1024 * 1024,
                "NanoCpus": 1_000_000_000, "PidsLimit": 64,
            },
            "Mounts": [{"Type": "volume", "Name": self.record["volume"],
                        "Destination": "/fixture"}],
            "State": {"Running": False},
            "NetworkSettings": {"Ports": {"2222/tcp": [
                {"HostIp": "127.0.0.1", "HostPort": "32123"}
            ]}},
        }

    def test_remote_context_cannot_be_disguised_by_a_unix_environment_override(self):
        context = [{"Endpoints": {"docker": {"Host": "ssh://remote"}}}]
        with (
            patch.dict("os.environ", {"DOCKER_HOST": "unix:///local.sock"}),
            patch("tools.run_local_mock.docker", return_value=json.dumps(context)) as execute,
            self.assertRaisesRegex(RuntimeError, "local Unix"),
        ):
            local_engine()
        execute.assert_called_once_with(("context", "inspect"))

    def test_native_amd64_engine_is_admitted_and_other_architectures_are_rejected(self):
        context = [{"Endpoints": {"docker": {"Host": "unix:///var/run/docker.sock"}}}]
        for architecture in ('x86_64', 'amd64', 'aarch64', 'arm64', 'riscv64'):
            engine = {'ID': 'engine', 'OSType': 'linux', 'Architecture': architecture}
            with (self.subTest(architecture=architecture),
                  patch('tools.run_local_mock.docker', side_effect=[
                      json.dumps(context), json.dumps(engine)])):
                if architecture == 'riscv64':
                    with self.assertRaisesRegex(RuntimeError, 'native'):
                        local_engine()
                else:
                    self.assertEqual(local_engine(), engine)

    def test_amd64_guest_cannot_use_an_arm_tool_image_or_changed_engine(self):
        engine = {'ID': 'engine', 'OSType': 'linux', 'Architecture': 'x86_64'}
        image = {'Os': 'linux', 'Architecture': 'arm64', 'Config': {'User': '10001:10001'}}
        with (patch('tools.run_local_mock.local_engine', return_value=engine),
              patch('tools.run_local_mock.os.umask'),
              patch('tools.run_local_mock.docker', return_value=json.dumps([image])) as execute,
              self.assertRaisesRegex(RuntimeError, 'native unprivileged')):
            create('fixture')
        execute.assert_called_once_with(('image', 'inspect', 'fixture'))
        self.record['engine_architecture'] = 'x86_64'
        self.state.write_text(json.dumps(self.record))
        with (patch('tools.run_local_mock.local_engine', return_value={
                  **engine, 'Architecture': 'aarch64'}),
              patch('tools.run_local_mock.docker') as execute,
              self.assertRaisesRegex(RuntimeError, 'engine changed')):
            LocalMock(self.state).control('power-cut')
        execute.assert_not_called()

    def test_amd64_restart_requires_native_utility_and_keeps_resource_bounds(self):
        selected = 'sha256:' + 'c' * 64
        image = {'Id': selected, 'Os': 'linux', 'Architecture': 'amd64',
                 'Config': {'User': '10001:10001'}}
        self.record['engine_architecture'] = 'x86_64'
        self.state.write_text(json.dumps(self.record))
        with (patch.object(LocalMock, 'inspect', return_value=self.inspection),
              patch('tools.run_local_mock.docker', return_value=json.dumps([image])),
              patch('tools.run_local_mock.require_capacity') as capacity):
            LocalMock(self.state).control('start-existing', selected)
        capacity.assert_called_once_with(selected, self.record['volume'], 'x86_64')
        with (patch('tools.run_local_mock.docker', return_value=json.dumps([image])),
              self.assertRaises(ValueError)):
            verify_capacity_image(selected, 'arm64')
        arguments = launch_arguments(self.record)
        self.assertEqual(arguments[arguments.index('--platform') + 1], 'linux/amd64')
        self.assertEqual(arguments[arguments.index('--memory') + 1], '1280m')
        self.assertEqual(arguments[arguments.index('--memory-swap') + 1], '1280m')
        self.assertNotIn('--device', arguments)

    def test_local_profile_cannot_expand_an_existing_shared_host_limit(self):
        with patch.dict("os.environ", {
            "MOCK_GUEST_MEMORY_MIB": "1024", "MOCK_LOCAL_REPLACEMENT": "1",
        }):
            with patch("pathlib.Path.read_text", return_value=str(384 * 1024 * 1024)):
                with self.assertRaisesRegex(RuntimeError, "exact memory bounds"):
                    guest_memory()
            with patch("pathlib.Path.read_text", return_value=str(1280 * 1024 * 1024)):
                self.assertEqual(guest_memory(), "1024")
        with patch.dict("os.environ", {"MOCK_GUEST_MEMORY_MIB": "1024"}, clear=True):
            with patch("pathlib.Path.read_text", return_value=str(1280 * 1024 * 1024)):
                with self.assertRaisesRegex(RuntimeError, "bounded mock"):
                    guest_memory()

    def test_wrong_engine_container_image_label_or_mount_refuses_power_cut(self):
        mutations = (
            {"Id": "another-container"}, {"Image": "another-image"},
            {"Config": {"Labels": {}}},
            {"Mounts": [{"Type": "bind", "Source": "/opt/maddy", "Destination": "/fixture"}]},
            {"HostConfig": {**self.inspection["HostConfig"], "Privileged": True}},
            {"HostConfig": {**self.inspection["HostConfig"], "MemorySwap": -1}},
            {"HostConfig": {**self.inspection["HostConfig"], "Devices": [{
                'PathOnHost': '/dev/sda', 'PathInContainer': '/dev/sda',
                'CgroupPermissions': 'rwm'}]}},
        )
        for mutation in mutations:
            with (
                self.subTest(mutation=mutation),
                patch("tools.run_local_mock.local_engine", return_value={"ID": "engine"}),
                patch("tools.run_local_mock.docker", return_value=json.dumps([
                    {**self.inspection, **mutation}
                ])) as execute,
            ):
                with self.assertRaisesRegex(RuntimeError, "exact owned"):
                    LocalMock(self.state).control("power-cut")
                execute.assert_called_once_with(("inspect", self.name))
        with patch("tools.run_local_mock.local_engine", return_value={"ID": "another-engine"}):
            with patch("tools.run_local_mock.docker") as execute:
                with self.assertRaisesRegex(RuntimeError, "engine changed"):
                    LocalMock(self.state).control("power-cut")
                execute.assert_not_called()

    def test_owned_power_cut_uses_the_recorded_immutable_container_id(self):
        with (
            patch("tools.run_local_mock.local_engine", return_value={"ID": "engine"}),
            patch("tools.run_local_mock.docker", return_value=json.dumps([self.inspection]))
            as execute,
        ):
            LocalMock(self.state).control("power-cut")
        self.assertEqual(execute.call_args.args[0], (
            "kill", "--signal", "KILL", self.record["container_id"]
        ))

    def test_disk_shortage_refuses_to_allocate_or_start_a_guest(self):
        with (
            patch("tools.run_local_mock.shutil.disk_usage", return_value=type(
                "Usage", (), {"free": 1024**3}
            )()),
            patch("tools.run_local_mock.docker") as execute,
            self.assertRaisesRegex(RuntimeError, "3 GiB"),
        ):
            require_capacity(self.record["image_id"], self.record["volume"])
        execute.assert_not_called()

    def test_restart_can_use_a_new_admission_utility_without_changing_the_old_guest_image(self):
        selected = "sha256:" + "c" * 64
        image = {"Id": selected, "Os": "linux", "Architecture": "arm64",
                 "Config": {"User": "10001:10001"}}
        with (patch.object(LocalMock, "inspect", return_value=self.inspection),
              patch("tools.run_local_mock.docker", return_value=json.dumps([image])) as execute,
              patch("tools.run_local_mock.require_capacity") as capacity):
            LocalMock(self.state).control("start-existing", selected)
        capacity.assert_called_once_with(selected, self.record["volume"])
        self.assertEqual(execute.call_args.args[0], ("start", self.record["container_id"]))
        self.assertEqual(json.loads(self.state.read_text()), self.record)

    def test_restart_rejects_mutable_root_or_foreign_platform_admission_utilities(self):
        selected = "sha256:" + "c" * 64
        image = {"Id": selected, "Os": "linux", "Architecture": "arm64",
                 "Config": {"User": "10001:10001"}}
        for changed in ({**image, "Config": {"User": "0:0"}},
                        {**image, "Architecture": "amd64"}, {**image, "Id": "different"}):
            with (self.subTest(changed=changed),
                  patch.object(LocalMock, "inspect", return_value=self.inspection),
                  patch("tools.run_local_mock.docker", return_value=json.dumps([changed])),
                  patch("tools.run_local_mock.require_capacity") as capacity):
                with self.assertRaises(ValueError):
                    LocalMock(self.state).control("start-existing", selected)
                capacity.assert_not_called()
        with patch.object(LocalMock, "inspect", return_value=self.inspection):
            with self.assertRaises(ValueError):
                LocalMock(self.state).control("start-existing", "mail-admin:local-mock")
            with self.assertRaises(ValueError):
                LocalMock(self.state).control("power-cut", selected)

    def test_guest_connection_rejects_a_non_loopback_port_and_scopes_ssh_trust(self):
        with (
            patch.object(LocalMock, "inspect", return_value=self.inspection),
            patch("tools.run_local_mock.command", return_value="fixture") as execute,
        ):
            self.assertEqual(LocalMock(self.state).guest(("uname", "-m")), "fixture")
        arguments = execute.call_args.args[0]
        self.assertIn("HostKeyAlias=" + self.name, arguments)
        self.assertIn("ControlMaster=no", arguments)
        self.assertIn("ControlPath=none", arguments)
        proxy = next(value.removeprefix("ProxyCommand=") for value in arguments
                     if value.startswith("ProxyCommand="))
        forwarded = shlex.split(proxy)
        self.assertEqual(forwarded[:6], ["docker", "exec", "-i", self.record["container_id"],
                                        "python", "-c"])
        self.assertEqual(forwarded[-1], "2222")
        self.assertEqual(arguments[-1], "uname -m")
        self.inspection["NetworkSettings"]["Ports"]["2222/tcp"][0]["HostIp"] = "198.51.100.7"
        with patch.object(LocalMock, "inspect", return_value=self.inspection):
            with self.assertRaisesRegex(RuntimeError, "loopback only"):
                LocalMock(self.state).guest(("uname", "-m"))

    def test_launch_uses_one_private_volume_and_no_host_device_or_docker_socket(self):
        arguments = launch_arguments(self.record)
        self.assertIn("127.0.0.1::2222", arguments)
        self.assertIn("--read-only", arguments)
        self.assertIn("no-new-privileges:true", arguments)
        self.assertEqual(arguments.count("--mount"), 1)
        self.assertNotIn("--privileged", arguments)
        self.assertNotIn("--device", arguments)
        self.assertNotIn("docker.sock", " ".join(arguments))
