import configparser
import unittest
from pathlib import Path

UNITS = Path(__file__).resolve().parents[2] / "host" / "systemd"
HELPER_STATE = "/var/lib/mail-admin-helper"


def unit(name):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read(UNITS / name)
    return parser


class MaddyRestartUnitTests(unittest.TestCase):
    """The restart worker is request-file driven only; there is no fallback timer."""

    def test_request_file_starts_a_hardened_oneshot_that_needs_docker(self):
        path = unit("mail-admin-maddy-restart.path")
        self.assertEqual(path["Path"]["PathChanged"], f"{HELPER_STATE}/maddy-restart.request.json")
        self.assertEqual(path["Path"]["Unit"], "mail-admin-maddy-restart.service")
        self.assertEqual(path["Install"]["WantedBy"], "paths.target")
        service = unit("mail-admin-maddy-restart.service")
        self.assertEqual(service["Service"]["Type"], "oneshot")
        self.assertEqual(service["Unit"]["Requires"], "docker.service")
        self.assertEqual(service["Service"]["ExecStart"],
                         "/usr/bin/python3 -m host.maddy_restart")
        self.assertIn(HELPER_STATE, service["Service"]["ReadWritePaths"].split())
        self.assertEqual(service["Service"]["ProtectSystem"], "strict")
        self.assertEqual(service["Service"]["NoNewPrivileges"], "true")


class BackupUnitTests(unittest.TestCase):
    """Backing up is `systemctl start`; restoring is a command; nothing waits for a request."""

    def test_the_backup_control_worker_is_gone(self):
        for removed in ("path", "service", "timer"):
            self.assertFalse((UNITS / f"mail-admin-backup-control.{removed}").exists(), removed)

    def test_the_catalog_refresh_follows_every_backup_without_failing_it(self):
        service = unit("mail-admin-backup.service")
        self.assertEqual(service["Service"]["Type"], "oneshot")
        self.assertEqual(service["Service"]["ExecStart"], "/usr/bin/python3 -m host.cli backup")
        self.assertEqual(
            service["Service"]["ExecStartPost"], "-/usr/bin/python3 -m host.backup_catalog"
        )


class WorkerActivationTests(unittest.TestCase):
    def test_recovery_has_no_worker_only_a_daily_expiry_timer(self):
        for removed in ("mail-admin-recovery.path", "mail-admin-recovery.timer"):
            self.assertFalse((UNITS / removed).exists(), removed)
        timer = unit("mail-admin-recovery-expire.timer")
        self.assertEqual(timer["Timer"]["OnCalendar"], "daily")
        self.assertEqual(timer["Timer"]["Persistent"], "true")
        service = unit("mail-admin-recovery-expire.service")
        self.assertEqual(service["Service"]["Type"], "oneshot")
        self.assertEqual(
            service["Service"]["ExecStart"], "/usr/bin/python3 -m host.expire_recovery")
        self.assertEqual(service["Service"]["ReadWritePaths"], HELPER_STATE)


class DnsUpdateUnitTests(unittest.TestCase):
    """The address fix runs once per boot, after the network is up, and only when configured."""

    def test_boot_oneshot_waits_for_the_network_and_the_owner_installed_settings(self):
        service = unit("mail-admin-dns-update.service")
        self.assertEqual(service["Service"]["Type"], "oneshot")
        self.assertEqual(service["Service"]["ExecStart"], "/usr/bin/python3 -m host.dns_update")
        self.assertEqual(service["Install"]["WantedBy"], "multi-user.target")
        self.assertEqual(service["Unit"]["After"], "network-online.target")
        self.assertEqual(service["Unit"]["ConditionPathExists"], "/etc/mail-admin/dns-update.json")
        self.assertFalse((UNITS / "mail-admin-dns-update.timer").exists())
