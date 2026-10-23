import subprocess
import sys
import unittest
from unittest.mock import patch

from tests.fixture_values import PASSWORD, REPLACEMENT_PASSWORD
from tests.host_factor_support import totp
from tests.recovery_terminal import PrivateTerminal


class HostFactorSupportTests(unittest.TestCase):
    def test_six_digit_sha1_rfc_vectors_and_step_boundary_match(self):
        secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
        vectors = ((59, '287082'), (1111111109, '081804'), (1111111111, '050471'),
                   (1234567890, '005924'), (2000000000, '279037'))
        for observed_at, expected in vectors:
            with self.subTest(observed_at=observed_at):
                self.assertEqual(totp(secret, observed_at), expected)
        self.assertEqual(totp(secret, 30), totp(secret, 59))
        self.assertNotEqual(totp(secret, 59), totp(secret, 60))

    def test_private_terminal_driver_imports_with_only_the_host_standard_library(self):
        program = ('import sys;from tests.recovery_terminal import PrivateTerminal;'
                   'assert PrivateTerminal;'
                   'assert not {"pyotp","argon2","cryptography"}&sys.modules.keys()')
        result = subprocess.run((sys.executable, '-S', '-c', program),
                                capture_output=True, text=True, check=False, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_replacement_terminal_uses_the_distinct_password_for_both_prompts(self):
        terminal = PrivateTerminal(10, REPLACEMENT_PASSWORD)
        replies = [b'', b'', b'', b'A' * 32, b'', b'', *([b'a' * 32] * 5)]
        with (patch.object(terminal, 'expect', side_effect=replies),
              patch.object(terminal, 'send') as send):
            terminal.credentials()
        self.assertNotEqual(PASSWORD, REPLACEMENT_PASSWORD)
        self.assertEqual([call.args[0] for call in send.call_args_list[:2]],
                         [REPLACEMENT_PASSWORD, REPLACEMENT_PASSWORD])

    def test_echo_detection_uses_the_selected_replacement_password(self):
        terminal = PrivateTerminal(10, REPLACEMENT_PASSWORD)
        with (patch('tests.recovery_terminal.select.select', return_value=([10], [], [])),
              patch('tests.recovery_terminal.os.read', return_value=REPLACEMENT_PASSWORD.encode())):
            terminal._read()
        self.assertTrue(terminal.password_echoed)
