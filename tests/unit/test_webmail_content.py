import unittest

from backend.webmail_imap import sexpr, unutf7, utf7
from backend.webmail_mime import email_address, header, sanitize
from backend.webmail_sending import MailSending
from shared.errors import ServiceError


class WebmailContentTests(unittest.TestCase):
    def test_html_complexity_is_bounded_with_an_inert_preview(self):
        rendered, _ = sanitize("<br>" * 20000)
        self.assertIn("limited text preview", rendered)
        self.assertLess(len(rendered), 400000)
        self.assertNotIn("<script", rendered)

    def test_folded_bcc_is_removed_only_from_submission_headers(self):
        encoded = (
            b"From: sender@example.net\r\nBcc: first@example.net,\r\n"
            b" second@example.net\r\nSubject: kept\r\n\r\nBcc: body text stays\r\n"
        )
        wire = MailSending.submission_bytes(encoded)
        self.assertNotIn(b"first@example.net", wire)
        self.assertNotIn(b"second@example.net", wire)
        self.assertIn(b"Bcc: body text stays", wire)
        self.assertIn(b"Subject: kept", wire)

    def test_hostile_html_is_inert_and_remote_images_require_explicit_loading(self):
        hostile = """<script>alert(1)</script><svg onload="attack()"><script>bad</script></svg>
        <img src="https://tracker.example/pixel" onerror="bad()"><img src="javascript:bad">
        <div style="background:url(https://tracker.example/css)">Visible</div>
        <a href="javascript:alert(1)">bad link</a><a href="https://example.net">safe</a>
        <form action="https://evil.example"><input name="password"></form>
        <iframe srcdoc="bad"></iframe><base href="https://evil.example">"""
        rendered, blocked = sanitize(hostile)
        self.assertEqual(blocked, 1)
        for value in (
            "<script",
            "<svg",
            "onerror",
            "onload",
            "javascript:",
            "<form",
            "<iframe",
            "<input",
            "<base",
            "style=",
            'src="https://tracker',
        ):
            self.assertNotIn(value, rendered)
        self.assertIn("Visible", rendered)
        self.assertIn("default-src &#x27;none&#x27;", rendered)
        allowed, _ = sanitize(hostile, True)
        self.assertIn('src="https://tracker.example/pixel"', allowed)
        self.assertNotIn("onerror", allowed)

    def test_header_injection_and_invalid_addresses_are_rejected(self):
        for value in ("name\r\nBcc: stolen@example.net", "null\0value"):
            with self.assertRaises(ServiceError):
                header(value)
        for value in ("no-address", "x@", "x@example.net\r\nBcc: other@example.net"):
            with self.assertRaises(ServiceError):
                email_address(value)

    def test_unicode_folder_encoding_and_imap_metadata_literals(self):
        for value in ("INBOX", "বাংলা & projects", "日本語/Archive"):
            self.assertEqual(unutf7(utf7(value)), value)
        self.assertEqual(sexpr(b'(\\HasNoChildren) "/" "Sent"'), [["\\HasNoChildren"], "/", "Sent"])
        self.assertEqual(
            sexpr(b'(UID 42 BODYSTRUCTURE ("TEXT" "PLAIN" NIL NIL NIL "7BIT" 9 1))')[0][1], "42"
        )
