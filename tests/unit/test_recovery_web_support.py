import unittest
from unittest.mock import patch

from shared.validation import MAIL_HOST
from tests.host_https_support import request
from tests.recovery_web_support import RecoveredHttps, verify_closed_authentication


class RecoveredHttpsTests(unittest.TestCase):
    def web(self):
        with patch('tests.recovery_web_support.ssl.create_default_context'):
            return RecoveredHttps()

    def test_any_successful_old_cookie_password_or_factor_blocks_recovery(self):
        old = {'cookie': 'fixture-cookie', 'password': 'fixture-old-password',
               'codes': ['fixture-old-code'], 'seed': 'A' * 32}
        for statuses in ((200,), (401, 200), (401, 401, 200, 200),
                         (401, 401, 200, 401, 200)):
            replies = [(status, {}, {'challenge': 'new', 'email_otp_available': False})
                       for status in statuses]
            with (self.subTest(statuses=statuses),
                  patch.object(RecoveredHttps, 'api', side_effect=replies),
                  self.assertRaisesRegex(RuntimeError, 'after recovery|recovery code or TOTP')):
                self.web().old_authentication_rejected(old)

    def test_successful_recovery_requires_two_factors_and_secure_session_cookie(self):
        web = self.web()
        replies = [(200, {}, {'challenge': 'fixture', 'email_otp_available': False}),
                   (200, {'set-cookie': 'session=fixture; Secure; HttpOnly; SameSite=strict'}, {})]
        with patch.object(web, 'api', side_effect=replies):
            self.assertEqual(web.login({'seed': 'A' * 32}), 'session=fixture')
        replies[-1] = (200, {'set-cookie': 'session=unsafe'}, {})
        with (patch.object(web, 'api', side_effect=replies),
              self.assertRaisesRegex(RuntimeError, 'security attributes')):
            web.login({'seed': 'A' * 32})

    def test_matched_image_assertion_failure_never_becomes_authentication_proof(self):
        from types import SimpleNamespace

        tools = SimpleNamespace(deployment={'dashboard_image_id': 'fixture-image'},
                                authentication_arguments=lambda action: ('run', 'fixture-image'))
        with (patch('tests.recovery_web_support.subprocess.run', return_value=SimpleNamespace(
            returncode=1, stderr='synthetic mismatch', stdout='{}',
        )), self.assertRaisesRegex(RuntimeError, 'recovery authentication assertions failed')):
            verify_closed_authentication(tools, {'synthetic': True})

    def test_local_connection_preserves_exact_application_host_and_origin(self):
        with patch('tests.host_https_support.http.client.HTTPSConnection') as connection:
            response = connection.return_value.getresponse.return_value
            response.status, response.getheaders.return_value = 200, []
            response.read.return_value = b'{"status":"ok"}'
            self.assertEqual(request(('fixture-trust', 10), ('GET', '/api/status', None), '')[0],
                             200)
            self.assertEqual(connection.call_args.kwargs['timeout'], 10)
            headers = connection.return_value.request.call_args.args[3]
            self.assertEqual(headers['Host'], MAIL_HOST)
            self.assertEqual(headers['Origin'], 'https://' + MAIL_HOST)
            connection.return_value.close.assert_called_once()
