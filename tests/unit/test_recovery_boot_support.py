import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.recovery_boot_support import RestoredBoot


class RestoredBootGuardTests(unittest.TestCase):
    def observer(self):
        target = SimpleNamespace(root=Path('/fixture'))
        with patch('tests.recovery_boot_support.protected_json', return_value={}):
            return RestoredBoot(target)

    def test_same_kernel_boot_cannot_establish_boot_recovery_or_start_any_service(self):
        observer = self.observer()
        with (patch('tests.recovery_boot_support.protected_json',
                    return_value={'boot_id': 'unchanged'}),
              patch('tests.recovery_boot_support.Path.read_text', return_value='unchanged'),
              patch.object(observer.docker, 'run') as docker,
              self.assertRaisesRegex(RuntimeError, 'different kernel boot')):
            observer.verify()
        docker.assert_not_called()

    def test_foreign_running_images_and_nonpersistent_restart_intent_fail_observation(self):
        observer = self.observer()
        observer.profile = {'dashboard_image_id': 'exact-image'}
        actual = {'Image': 'exact-image', 'State': {'Running': True, 'OOMKilled': False},
                  'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped'}}}
        for changed in ({'Image': 'other'}, {'State': {'Running': False, 'OOMKilled': False}},
                        {'State': {'Running': True, 'OOMKilled': True}},
                        {'HostConfig': {'RestartPolicy': {'Name': 'no'}}}):
            with (self.subTest(changed=changed),
                  patch.object(observer.docker, 'inspect', return_value={**actual, **changed}),
                  patch.object(observer, 'web_consumer') as verify,
                  self.assertRaisesRegex(RuntimeError, 'automatically resume')):
                observer.consumer('mail-admin')
            verify.assert_not_called()

    def test_only_the_owned_completed_create_request_may_be_retried(self):
        observer = self.observer()
        for row in ({'email': 'other@example.net', 'operation': 'create'},
                    {'email': 'helper-lifecycle@rsalehin24.me', 'operation': 'password'}):
            with (self.subTest(row=row), patch.object(observer, 'client') as client,
                  self.assertRaisesRegex(RuntimeError, 'synthetic completed create')):
                observer.retry_completed(row)
            client.assert_not_called()

    def test_changed_credentials_during_kernel_boot_cannot_pass_later_retry_checks(self):
        observer = self.observer()
        with (patch('tests.recovery_boot_support.protected_json', return_value={
            'credential_digest_before': 'saved-digest',
        }), patch('tests.recovery_boot_support.credential_digest', return_value='changed-digest'),
            self.assertRaisesRegex(RuntimeError, 'between activation and kernel boot')):
            observer.require_original_credentials()

    def test_unready_https_never_consumes_credentials_or_emits_a_successful_boot_observation(self):
        observer = self.observer()
        observer.target.record = unittest.mock.Mock()
        with (patch('tests.recovery_boot_support.protected_json', return_value={'boot_id': 'old'}),
              patch('tests.recovery_boot_support.Path.read_text', return_value='new'),
              patch.object(observer, 'require_original_credentials'),
              patch.object(observer, 'consumer'), patch.object(observer, 'mail_and_helper'),
              patch('tests.recovery_boot_support.RecoveredHttps') as web,
              self.assertRaisesRegex(RuntimeError, 'not ready')):
            web.return_value.ready.side_effect = RuntimeError('not ready')
            observer.verify()
        web.return_value.login.assert_not_called()
        observer.target.record.assert_not_called()
