import os
import stat
import unittest
from unittest.mock import patch

from shared.files import atomic_json, atomic_owned_json, protected_json
from tests.support import StateFixture


class AtomicJsonSharingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.path = self.fixture.root / "record.json"

    def mode(self, path):
        return stat.S_IMODE(path.lstat().st_mode)

    def test_new_files_stay_private(self):
        atomic_json(self.path, {"value": 1})
        self.assertEqual(self.mode(self.path), 0o600)
        self.assertEqual(protected_json(self.path, os.geteuid()), {"value": 1})

    def test_a_replaced_file_keeps_its_group_read_mode(self):
        atomic_json(self.path, {"value": 1})
        self.path.chmod(0o640)
        atomic_json(self.path, {"value": 2})
        self.assertEqual(self.mode(self.path), 0o640)
        self.assertEqual(protected_json(self.path, os.geteuid()), {"value": 2})

    def test_a_replaced_file_keeps_its_group(self):
        atomic_json(self.path, {"value": 1})
        self.path.chmod(0o640)
        with patch("shared.files.os.fchown") as change_owner:
            atomic_json(self.path, {"value": 2})
        group = self.path.stat().st_gid
        self.assertEqual([call.args[1:] for call in change_owner.call_args_list], [(-1, group)])

    def test_a_file_never_shared_is_not_widened_by_a_rewrite(self):
        atomic_json(self.path, {"value": 1})
        for _ in range(2):
            atomic_json(self.path, {"value": 2})
            self.assertEqual(self.mode(self.path), 0o600)

    def test_a_symlink_is_replaced_not_followed_and_its_target_is_untouched(self):
        target = self.fixture.root / "target.json"
        target.write_text("{}")
        target.chmod(0o644)
        self.path.symlink_to(target)
        atomic_json(self.path, {"value": 3})
        self.assertFalse(self.path.is_symlink())
        self.assertEqual(self.mode(self.path), 0o600)
        self.assertEqual((target.read_text(), self.mode(target)), ("{}", 0o644))

    def test_an_explicit_owner_handoff_still_applies_to_a_replaced_file(self):
        atomic_json(self.path, {"value": 1})
        self.path.chmod(0o640)
        with patch("shared.files.os.fchown") as change_owner:
            atomic_owned_json(self.path, {"value": 2}, os.geteuid())
        self.assertEqual(change_owner.call_count, 2)
        self.assertEqual(change_owner.call_args_list[-1].args[1:], (os.geteuid(), -1))
