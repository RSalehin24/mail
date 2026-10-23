import copy
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests.host_reboot_support import require_reboot
from tests.host_support import CONSUMER_PROGRAM, GuestHelper, require_guest


class HostFixtureGuardTests(unittest.TestCase):
    def setUp(self):
        self.identifier = "8a8f1170-b11c-4adf-8626-537e42976ea3"
        self.record = {
            "scope": "mail-admin-local-mock", "instance_id": self.identifier,
            "consumer_name": "mail-admin-fixture-consumer-" + self.identifier,
            "consumer_id": "a" * 64, "runtime_image_id": "sha256:" + "b" * 64,
            "request_id": "af4a338a-4e53-4cb4-be81-ff0a462fde13",
            "email": "helper-lifecycle@rsalehin24.me",
        }

    def guard(self, record, instance):
        with (patch("tests.host_support.os.geteuid", return_value=0),
              patch("tests.host_support.platform.system", return_value="Linux"),
              patch("tests.host_support.platform.machine", return_value="x86_64"),
              patch("tests.host_support.Path.read_text", return_value=instance)):
            require_guest(record)

    def test_identity_and_synthetic_mailbox_are_required_before_lifecycle_commands(self):
        self.guard(self.record, self.identifier)
        changes = ({"scope": "production"}, {"consumer_name": "mail-admin"},
                   {"consumer_id": "maddy"}, {"runtime_image_id": "latest"},
                   {"request_id": "invalid"}, {"email": "real@rsalehin24.me"})
        for changed in changes:
            with self.subTest(changed=changed), self.assertRaises((RuntimeError, ValueError)):
                self.guard({**self.record, **changed}, self.identifier)
        with self.assertRaises(RuntimeError):
            self.guard(self.record, "different-instance")

    def test_non_root_and_non_guest_platforms_fail_before_reading_guest_state(self):
        for user, system, machine in ((10001, "Linux", "x86_64"), (0, "Darwin", "arm64"),
                                      (0, "Linux", "aarch64")):
            with (self.subTest(user=user, system=system, machine=machine),
                  patch("tests.host_support.os.geteuid", return_value=user),
                  patch("tests.host_support.platform.system", return_value=system),
                  patch("tests.host_support.platform.machine", return_value=machine),
                  patch("tests.host_support.Path.read_text") as reader):
                with self.assertRaises(RuntimeError):
                    require_guest(self.record)
                reader.assert_not_called()

    def inspection(self):
        return {
            "Id": self.record["consumer_id"], "Image": self.record["runtime_image_id"],
            "Config": {"User": "10001:10001", "Labels": {"mail-admin.fixture": "true"}},
            "State": {"Running": True},
            "HostConfig": {"ReadonlyRootfs": True, "NetworkMode": "none", "Privileged": False,
                           "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges:true"]},
            "Mounts": [{"Type": "bind", "Source": "/run/mail-admin-helper",
                        "Destination": "/run/mail-admin-helper", "RW": False}],
        }

    def test_replaced_root_privileged_or_extra_mounted_consumer_is_rejected(self):
        host = GuestHelper.__new__(GuestHelper)
        host.record = self.record
        valid = self.inspection()
        changes = (("Id", "c" * 64), ("Image", "sha256:" + "d" * 64),
                   ("Config.User", "0:0"), ("HostConfig.Privileged", True),
                   ("HostConfig.ReadonlyRootfs", False), ("HostConfig.CapDrop", []),
                   ("HostConfig.SecurityOpt", []), ("HostConfig.NetworkMode", "host"),
                   ("State.Running", False),
                   ("Mounts", [*valid["Mounts"], {"Type": "bind", "Source": "/opt/maddy",
                     "Destination": "/data", "RW": False}]))
        for path, value in changes:
            changed = copy.deepcopy(valid)
            parent, _, key = path.rpartition(".")
            (changed[parent] if parent else changed)[key] = value
            with self.subTest(path=path), patch.object(
                host, "command", return_value=json.dumps([changed])
            ), self.assertRaises(RuntimeError):
                host.verify_consumer()
        with patch.object(host, "command", return_value=json.dumps([valid])):
            host.verify_consumer()

    def test_reboot_requires_a_changed_kernel_on_the_same_disposable_instance(self):
        before = {'instance_id': self.identifier,
                  'boot_id': '0de6ec2e-9106-4244-9e41-fba30669ec5f'}
        current = {**before, 'boot_id': '54cb4fa4-b5ee-4369-980e-ac264f994c5c'}
        require_reboot(before, current)
        for changed in (before, {**current, 'instance_id': current['boot_id']},
                        {**current, 'boot_id': 'not-a-boot'}):
            with self.subTest(changed=changed), self.assertRaises((ValueError, RuntimeError)):
                require_reboot(before, changed)

    def test_standby_consumer_exits_cleanly_on_the_daemons_termination_signal(self):
        ready = CONSUMER_PROGRAM.replace('time.sleep(86400)',
                                         "print('ready',flush=True);time.sleep(86400)")
        process = subprocess.Popen((sys.executable, '-c', ready), stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            process.terminate()
            self.assertEqual(process.wait(timeout=5), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
