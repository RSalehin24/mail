import unittest

from shared.errors import ServiceError
from shared.validation import address, bounded_text, mailbox_password, master_password, new_address


class ValidationTests(unittest.TestCase):
    def test_unpaired_unicode_surrogates_fail_as_validation_errors(self):
        invalid = "x" * 16 + "\ud800"
        for check in (mailbox_password, master_password, lambda value: bounded_text(value, 256)):
            with self.assertRaises(ServiceError) as caught:
                check(invalid)
            self.assertEqual(caught.exception.status, 400)

    def test_baseline_addresses_are_supported(self):
        for local in (
            "cloud",
            "demo_user.library",
            "dev_01",
            "dev_02",
            "library",
            "mail",
            "reyan",
            "salehin",
        ):
            self.assertEqual(address(local.upper() + "@rsalehin24.me"), local + "@rsalehin24.me")

    def test_injection_and_malformed_addresses_fail(self):
        for local in ("-option", "a..b", "a/../b", "a b", "a\nb", "a" * 65, "a;id"):
            with self.subTest(local=local), self.assertRaises(ServiceError):
                address(local + "@rsalehin24.me")
        with self.assertRaises(ServiceError):
            new_address("postmaster@rsalehin24.me")

    def test_internal_underscore_and_hyphen_combinations_are_valid(self):
        self.assertEqual(
            new_address("first__middle-last@rsalehin24.me"), "first__middle-last@rsalehin24.me"
        )

    def test_byte_bounds_and_exact_password_preservation(self):
        password = " " + "é" * 35 + " "
        self.assertEqual(mailbox_password(password), password)
        for invalid in (password + "a", "a" * 15, "a" * 16 + "\n", "a" * 16 + "\0"):
            with self.assertRaises(ServiceError):
                mailbox_password(invalid)

    def test_master_password_character_and_utf8_bounds_preserve_spaces_and_unicode(self):
        for value in ("x" * 16, "x" * 1024, " " + "é" * 511 + " ",
                      " \tMüster password with spaces\t "):
            self.assertEqual(master_password(value), value)
        for value in ("", "x" * 15, "x" * 1025, "é" * 512 + " "):
            with self.assertRaises(ServiceError):
                master_password(value)
