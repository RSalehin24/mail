import struct
import unittest
from unittest.mock import patch

from backend.dns import dns_name, mx_records, observe_mx


class DnsTests(unittest.TestCase):
    def response(self):
        question = b"\x0arsalehin24\x02me\0\0\x0f\0\x01"
        answer = b"\xc0\x0c" + struct.pack("!HHIH", 15, 1, 60, 9)
        return (
            struct.pack("!6H", 123, 0x8180, 1, 1, 0, 0)
            + question
            + answer
            + b"\0\x0a\x04mail\xc0\x0c"
        )

    def test_compressed_mx_response_and_question_binding(self):
        packet = self.response()
        self.assertEqual(mx_records(packet, 123), ["10 mail.rsalehin24.me"])
        with self.assertRaises(ValueError):
            mx_records(packet, 124)

    def test_bad_compression_truncation_and_response_flags_fail(self):
        with self.assertRaises(ValueError):
            dns_name(b"\xc0\0", 0)
        for packet in (
            self.response()[:-1],
            self.response()[:2] + b"\x83\x80" + self.response()[4:],
        ):
            with self.assertRaises((ValueError, IndexError, struct.error)):
                mx_records(packet, 123)

    def test_unavailable_resolver_is_unknown_without_sending_mail(self):
        with patch("backend.dns._resolver", side_effect=OSError("fixture unavailable")):
            result = observe_mx()
        self.assertEqual(result["state"], "unknown")
        self.assertNotIn("records", result)
