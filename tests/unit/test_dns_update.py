import tempfile
import unittest
from pathlib import Path
from unittest import mock

from host import dns_update
from shared.errors import ServiceError

ZONE = "2" * 32
RECORD = "a" * 32
NAME = "mail.example.test"
OLD, NEW = "35.211.79.14", "34.23.45.67"


class FakeApi:
    zone = ZONE

    def __init__(self, rows):
        self.rows, self.writes, self.reads = rows, [], []

    def request(self, method, path, body=None):
        if method == "GET":
            self.reads.append(path)
            return {"result": self.rows}
        self.writes.append((method, path, body))


def row(content=OLD, **fields):
    return {"id": RECORD, "content": content, **fields}


class SynchronizeTests(unittest.TestCase):
    def test_a_matching_record_is_left_alone(self):
        api = FakeApi([row(NEW)])
        self.assertTrue(dns_update.synchronize(api, NAME, NEW, False).startswith("unchanged"))
        self.assertEqual(api.writes, [])

    def test_a_changed_address_updates_only_the_content_and_keeps_ttl_and_proxy(self):
        api = FakeApi([row(ttl=300, proxied=False)])
        self.assertTrue(dns_update.synchronize(api, NAME, NEW, False).startswith("changed"))
        self.assertEqual(api.writes, [(
            "PUT", f"/zones/{ZONE}/dns_records/{RECORD}",
            {"type": "A", "name": NAME, "content": NEW, "ttl": 300, "proxied": False},
        )])
        # The filter must reach Cloudflare intact: a second "?" would fold into the name.
        self.assertEqual(
            api.reads, [f"/zones/{ZONE}/dns_records?type=A&name={NAME}&per_page=5"]
        )

    def test_a_dry_run_reports_the_change_without_making_it(self):
        api = FakeApi([row()])
        self.assertTrue(dns_update.synchronize(api, NAME, NEW, True).startswith("would change"))
        self.assertEqual(api.writes, [])

    def test_zero_or_several_records_are_never_guessed_between(self):
        for rows in ([], [row(), row()]):
            with self.subTest(count=len(rows)), self.assertRaises(ServiceError):
                dns_update.synchronize(FakeApi(rows), NAME, NEW, False)


class ExternalAddressTests(unittest.TestCase):
    def reply(self, text):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = text.encode()
        return mock.patch("urllib.request.urlopen", return_value=response)

    def test_the_metadata_address_is_returned(self):
        with self.reply(NEW + "\n"):
            self.assertEqual(dns_update.external_address(), NEW)

    def test_private_and_malformed_answers_are_rejected(self):
        for text in ("10.0.0.5", "not-an-address", ""):
            with self.subTest(text=text), self.reply(text), self.assertRaises(ServiceError):
                dns_update.external_address()


class SettingsTests(unittest.TestCase):
    def load(self, **changes):
        settings = {"account_id": "1" * 32, "zone_id": ZONE, "record_name": NAME,
                    "token_reference": "/etc/mail-admin/dns-update.token", **changes}
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            dns_update, "protected_json", return_value=settings
        ):
            return dns_update.load_settings(Path(directory) / "dns-update.json")

    def test_valid_settings_are_returned(self):
        self.assertEqual(self.load()["record_name"], NAME)

    def test_missing_fields_and_bad_names_are_rejected(self):
        for changes in ({"zone_id": 5}, {"record_name": "bad name/../x"}, {"record_name": ""}):
            with self.subTest(changes=changes), self.assertRaises(ServiceError):
                self.load(**changes)
