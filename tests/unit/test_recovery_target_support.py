import unittest
from unittest.mock import patch

from tests.recovery_target_support import RecoveryTarget

TARGET = '115167ae-192e-4b01-b32b-2950c8a1ad45'
SOURCE = '8a8f1170-b11c-4adf-8626-537e42976ea3'


class RecoveryTargetGuardTests(unittest.TestCase):
    def context(self):
        return {'target_instance_id': TARGET, 'source_instance_id': SOURCE,
                'directory': '/root/mail-admin-fixture/replacement-' + TARGET}

    def test_source_identity_and_private_target_scope_are_required_before_any_command(self):
        for changes in ({'source_instance_id': TARGET}, {'source_instance_id': 'invalid'},
                        {'directory': '/etc/mail-admin'},
                        {'directory': '/root/mail-admin-fixture/../mail-admin-fixture/replacement-'
                         + TARGET}):
            with (self.subTest(changes=changes),
                  patch('tests.recovery_target_support.require_fixture_guest'),
                  patch('tests.recovery_target_support.command') as command,
                  self.assertRaises((RuntimeError, ValueError))):
                RecoveryTarget({**self.context(), **changes})
            command.assert_not_called()

    def test_existing_container_or_image_blocks_restore_before_repository_access(self):
        with patch('tests.recovery_target_support.require_fixture_guest'):
            target = RecoveryTarget(self.context())
        for replies in (['container'], ['', 'image']):
            with (self.subTest(replies=replies),
                  patch('tests.recovery_target_support.command', side_effect=replies),
                  patch.object(target, 'operation') as operation,
                  self.assertRaisesRegex(RuntimeError, 'without containers or images')):
                target.stage()
            operation.assert_not_called()

    def test_partial_restore_never_loads_images_or_prepares_authentication(self):
        with patch('tests.recovery_target_support.require_fixture_guest'):
            target = RecoveryTarget(self.context())
        target.context['reference'] = 'synthetic-reference'
        with (patch.object(target, 'require_empty'), patch.object(target, 'record'),
              patch.object(target, 'operation') as operation,
              patch.object(target, 'installation') as installation,
              self.assertRaisesRegex(RuntimeError, 'Full-state recovery')):
            operation.return_value.stage.return_value = {
                'state': 'authentication_closed', 'full_state_sources_present': False,
            }
            target.stage()
        installation.assert_not_called()
        operation.return_value.prepare_authentication.assert_not_called()

    def test_missing_canonical_docker_blocks_download_before_any_restore_state_is_created(self):
        with patch('tests.recovery_target_support.require_fixture_guest'):
            target = RecoveryTarget(self.context())
        with (patch('tests.recovery_target_support.command', return_value=''),
              patch('tests.recovery_target_support.Path.exists', return_value=False),
              patch('tests.recovery_target_support.Path.is_file', return_value=False),
              patch.object(target, 'operation') as operation,
              patch.object(target, 'record') as record,
              self.assertRaisesRegex(RuntimeError, 'Docker at /usr/bin/docker')):
            target.stage()
        operation.assert_not_called()
        record.assert_not_called()
