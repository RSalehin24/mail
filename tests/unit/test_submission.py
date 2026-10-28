"""Credential injection guards and rollback of failed host synchronization."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helper.process import CommandResult
from helper.protocol import HelperRequest
from helper.submission import SubmissionControl
from shared.errors import ServiceError
from shared.submission import relay_configuration


class SubmissionControlTests(unittest.TestCase):
    def test_credentials_cannot_inject_configuration_or_macros(self):
        for credential in ('x\n}', '$(hostname)', '{env:SECRET}', 'x"', 'x\\', 'x y', ''):
            with self.subTest(credential=credential), self.assertRaises(ServiceError):
                relay_configuration({'username': 'synthetic', 'credential': credential})
        self.assertIn('starttls yes', relay_configuration(
            {'username': 'user@example.com', 'credential': 'xsmtpsib-abc+/='}))

    def test_helper_rejects_extra_fields_and_bad_protocol(self):
        payload = {'protocol': 1, 'operation': 'submission_sync',
                   'request_id': '28d251dd-443e-4541-b993-8d7ea226aee0',
                   'username': 'synthetic', 'credential': 'fixture-secret', 'revision': 1}
        self.assertEqual(HelperRequest.parse(payload).operation, 'submission_sync')
        for changes in ({'path': '/etc/shadow'}, {'protocol': True}, {'credential': 'x\ny'}):
            with self.subTest(changes=changes), self.assertRaises(ServiceError):
                HelperRequest.parse({**payload, **changes})

    def test_failure_restores_previous_working_include_and_uses_restricted_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'submission-relay.conf'
            path.write_bytes(b'previous working configuration')
            path.chmod(0o600)
            control = SubmissionControl(root)
            with patch('helper.submission.protected_bytes',
                       side_effect=lambda p: p.read_bytes()), \
                    patch.object(control, 'restart') as restart, \
                    patch('helper.submission.execute', return_value=CommandResult(1, 'failure')):
                with self.assertRaisesRegex(ServiceError, 'submission_synchronization_failed'):
                    control.synchronize({'username': 'synthetic', 'credential': 'new-secret',
                                         'revision': 1})
            self.assertEqual(path.read_bytes(), b'previous working configuration')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.stat().st_uid, os.getuid())
            self.assertFalse(control.rollback.exists())
            restart.assert_called_once()

    def test_interrupted_sync_keeps_only_a_committed_revision(self):
        import hashlib
        import sqlite3

        from helper.submission import write_configuration
        from shared.files import atomic_json

        for committed in (False, True):
            with self.subTest(committed=committed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                database = root / 'dashboard.db'
                with sqlite3.connect(database) as connection:
                    connection.execute('CREATE TABLE relay_settings(id INTEGER, revision INTEGER)')
                    connection.execute('INSERT INTO relay_settings VALUES(1,?)',
                                       (2 if committed else 1,))
                candidate = b'new configuration'
                write_configuration(root / 'submission-relay.conf', candidate)
                write_configuration(root / 'submission-relay.rollback', b'old configuration')
                atomic_json(root / 'submission-relay.pending.json', {
                    'revision': 2, 'sha256': hashlib.sha256(candidate).hexdigest()})
                with patch('helper.submission.protected_bytes',
                           side_effect=lambda p: p.read_bytes()), \
                        patch('helper.submission.protected_json',
                              side_effect=lambda p: __import__('json').loads(p.read_text())), \
                        patch.object(SubmissionControl, 'restart') as restart:
                    control = SubmissionControl(root, database)
                self.assertEqual(control.path.read_bytes(),
                                 candidate if committed else b'old configuration')
                self.assertEqual(restart.call_count, 0 if committed else 1)
                self.assertFalse(control.rollback.exists())
                self.assertFalse(control.pending.exists())

    def test_completed_commit_cleanup_can_resume_with_only_the_pending_marker(self):
        from helper.submission import write_configuration

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_configuration(root / 'submission-relay.conf', b'committed configuration')
            write_configuration(root / 'submission-relay.pending.json', b'{}')
            with patch.object(SubmissionControl, 'restart') as restart:
                control = SubmissionControl(root)
            self.assertEqual(control.path.read_bytes(), b'committed configuration')
            self.assertFalse(control.pending.exists())
            restart.assert_not_called()
