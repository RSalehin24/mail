import copy
import json
import os
import unittest
from unittest.mock import patch

from tests.recovery_install_support import RecoveryInstallation, copy_owned
from tests.support import StateFixture


class RecoveryInstallationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.root = self.fixture.root.resolve() / 'restored'
        self.root.mkdir()
        self.installation = RecoveryInstallation(self.root, ('new-guest', 'source-guest'))

    def test_same_guest_or_unrecovered_credentials_prevent_every_filesystem_change(self):
        self.installation.identity = ('same-guest', 'same-guest')
        with patch('tests.recovery_install_support.require_fixture_guest'):
            with self.assertRaisesRegex(RuntimeError, 'different disposable'):
                self.installation.install()
        self.installation.identity = ('new-guest', 'source-guest')
        with (patch('tests.recovery_install_support.require_fixture_guest'),
              patch('tests.recovery_install_support.protected_json',
                    return_value={'state': 'authentication_closed'})):
            with self.assertRaisesRegex(RuntimeError, 'Private-terminal recovery'):
                self.installation.install()
        self.assertEqual(list(self.root.iterdir()), [])

    def utilities(self):
        expected = {'utility_name': 'prepare-fixture', 'recovery_utility_name': 'recover-fixture'}
        records = [{'Name': '/' + name, 'Image': 'sha256:' + 'a' * 64,
                    'State': {'Running': False, 'Status': 'exited', 'ExitCode': 0},
                    'Config': {'Labels': {'mail-admin.recovery-run': name}}}
                   for name in expected.values()]
        return expected, records

    def verify(self, records):
        expected, _ = self.utilities()
        with (patch('tests.recovery_install_support.protected_json',
                    return_value={'dashboard_image_id': 'sha256:' + 'a' * 64}),
              patch('tests.recovery_install_support.command', side_effect=[
                  'first\nsecond', *(json.dumps([record]) for record in records),
              ])):
            self.installation.verify_utilities(expected)

    def test_only_exact_completed_saved_image_utilities_allow_installation(self):
        _, records = self.utilities()
        self.verify(records)
        for changes in ({'Name': '/maddy'}, {'Image': 'sha256:' + 'b' * 64},
                        {'State': {'Running': True, 'Status': 'running', 'ExitCode': 0}},
                        {'State': {'Running': False, 'Status': 'created', 'ExitCode': 0}},
                        {'State': {'Running': False, 'Status': 'exited', 'ExitCode': 1}},
                        {'Config': {'Labels': {}}}):
            altered = copy.deepcopy(records)
            altered[0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                self.verify(altered)

    def test_fresh_copy_preserves_modes_and_refuses_existing_or_symlinked_targets(self):
        source = self.root / 'source'
        source.mkdir(mode=0o700)
        (source / 'message').write_bytes(b'synthetic body')
        (source / 'message').chmod(0o600)
        target = self.root / 'installed'
        copy_owned(source, target)
        self.assertEqual((target / 'message').read_bytes(), b'synthetic body')
        self.assertEqual(target.stat().st_mode & 0o777, 0o700)
        self.assertEqual((target / 'message').stat().st_mode & 0o777, 0o600)
        self.assertEqual((target / 'message').stat().st_uid, os.getuid())
        with self.assertRaises(RuntimeError):
            copy_owned(source, target)
        link = self.root / 'symlink'
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            copy_owned(source, link / 'new')

    def test_saved_image_identity_platform_and_nonroot_dashboard_are_required(self):
        image = {'Id': 'sha256:' + 'a' * 64, 'Architecture': 'amd64', 'Os': 'linux',
                 'Config': {'User': '10001:10001'}}
        expected = ('dashboard_image_id', image['Id'])
        self.installation.verify_image(image, expected)
        for changes in ({'Id': 'sha256:' + 'b' * 64}, {'Architecture': 'arm64'},
                        {'Os': 'windows'}, {'Config': {'User': '0:0'}}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                self.installation.verify_image({**image, **changes}, expected)

    def test_saved_manifest_cannot_select_arbitrary_paths_before_any_image_load(self):
        with (patch('tests.recovery_install_support.protected_json', return_value={
            'sha256': {'../../outside': 'a' * 64},
        }), patch('tests.recovery_install_support.command') as command):
            with self.assertRaisesRegex(RuntimeError, 'artifact set'):
                self.installation.verify_release_images()
        command.assert_not_called()
