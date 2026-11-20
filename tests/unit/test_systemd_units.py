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
