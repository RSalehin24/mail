import configparser
import re
import unittest
from pathlib import Path

UNITS = Path(__file__).resolve().parents[2] / "host" / "systemd"
HELPER_STATE = "/var/lib/mail-admin-helper"
# Each worker is started by a request landing in its database; the timer is only a fallback.
WORKERS = {
    "mail-admin-backup-control": "backup-control.db",
    "mail-admin-recovery": "recovery.db",
}
MINIMUM_FALLBACK_SECONDS = 60
SECONDS_PER_UNIT = {"s": 1, "sec": 1, "min": 60}


def unit(name):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read(UNITS / name)
    return parser


def seconds(value):
    match = re.fullmatch(r"(\d+)(s|sec|min)", value)
    return int(match[1]) * SECONDS_PER_UNIT[match[2]]


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


class WorkerActivationTests(unittest.TestCase):
    def test_requests_start_the_worker_through_a_path_unit(self):
        for worker, database in WORKERS.items():
            with self.subTest(worker=worker):
                path = unit(worker + ".path")
                self.assertEqual(path["Path"]["PathChanged"], f"{HELPER_STATE}/{database}")
                self.assertEqual(path["Path"]["Unit"], worker + ".service")
                self.assertEqual(path["Install"]["WantedBy"], "paths.target")

    def test_fallback_timers_do_not_poll_every_few_seconds(self):
        for worker in WORKERS:
            with self.subTest(worker=worker):
                interval = seconds(unit(worker + ".timer")["Timer"]["OnUnitInactiveSec"])
                self.assertGreaterEqual(interval, MINIMUM_FALLBACK_SECONDS)


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
