"""Daily retention selects verified Dhaka days without losing recovery pins."""

import unittest
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from host.daily_retention import daily_keep


def stamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo("Asia/Dhaka")).timestamp()


class DailyRetentionTests(unittest.TestCase):
    def test_midnight_replacement_and_failed_candidate(self):
        rows = [
            SimpleNamespace(identifier=key, cutoff=stamp(time))
            for key, time in [
                ("a", "2026-10-26T23:59:00"),
                ("b", "2026-10-27T00:01:00"),
                ("c", "2026-10-27T03:00:00"),
                ("failed", "2026-10-27T04:00:00"),
            ]
        ]
        self.assertEqual(
            daily_keep(rows, {"a", "b", "c"}, set(), stamp("2026-10-27T05:00:00"), 604800),
            {"a", "c", "failed"},
        )

    def test_expired_restore_pin_survives(self):
        rows = [
            SimpleNamespace(identifier="old", cutoff=0),
            SimpleNamespace(identifier="new", cutoff=1000000),
        ]
        self.assertEqual(daily_keep(rows, {"old", "new"}, {"old"}, 1000001, 604800), {"old", "new"})
