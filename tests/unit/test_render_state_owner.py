import unittest
from pathlib import Path

from host.render import rendered_files

UNITS = Path(__file__).resolve().parents[2] / "host" / "systemd"
DASHBOARD_UID = 10001
SHARED_LOCKS = ("renewal", "maddy", "backup")


def render(**extra):
    configuration = {
        "inputs": {"service_gid": DASHBOARD_UID, "backup_schedule": "*-*-* 03:00:00"},
        "backup_resources": {"memory_limit_bytes": 1024, "cpu_quota_percent": 50},
        **extra,
    }
    sources = {"mail-admin.tmpfiles.in": (UNITS / "mail-admin.tmpfiles.in").read_text()}
    return rendered_files(configuration, sources)["mail-admin.tmpfiles"].decode()


class TmpfilesOwnerTests(unittest.TestCase):
    def test_shared_locks_are_root_owned_unless_a_state_owner_is_configured(self):
        lines = render().splitlines()
        for name in SHARED_LOCKS:
            self.assertIn(f"f /run/lock/mail-admin-{name}.lock 0600 0 root - -", lines)

    def test_shared_locks_belong_to_the_configured_state_owner(self):
        lines = render(state_owner_uid=DASHBOARD_UID).splitlines()
        for name in SHARED_LOCKS:
            expected = f"f /run/lock/mail-admin-{name}.lock 0600 {DASHBOARD_UID} root - -"
            self.assertIn(expected, lines)

    def test_no_helper_socket_directory_is_created(self):
        self.assertNotIn("mail-admin-helper", render())
