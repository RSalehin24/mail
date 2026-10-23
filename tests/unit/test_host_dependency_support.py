import unittest
from unittest.mock import patch

from tests.host_dependency_support import DependencyFixture


class DependencyFixtureGuardTests(unittest.TestCase):
    def test_nonfixture_guest_is_rejected_before_reading_units_or_creating_directories(self):
        with (patch('tests.host_dependency_support.require_fixture_guest',
                    side_effect=RuntimeError('wrong guest')),
              patch('tests.host_dependency_support.Path.read_text') as read,
              patch('tests.host_dependency_support.Path.mkdir') as mkdir,
              self.assertRaisesRegex(RuntimeError, 'wrong guest')):
            DependencyFixture('wrong', 'unused')
        read.assert_not_called()
        mkdir.assert_not_called()

    def test_foreign_unit_targets_are_rejected_before_systemd_or_filesystem_changes(self):
        from pathlib import Path

        with (patch('tests.host_dependency_support.require_fixture_guest'),
              patch('tests.host_dependency_support.Path.read_text',
                    return_value='[Install]\nWantedBy=unrelated-live-service.service\n'),
              patch('tests.host_dependency_support.Path.mkdir') as mkdir,
              patch('tests.host_dependency_support.command') as command,
              self.assertRaisesRegex(RuntimeError, 'Unexpected dependency')):
            DependencyFixture('fixture', Path('unit'))
        mkdir.assert_not_called()
        command.assert_not_called()
