import unittest

from backend.main import create_app
from tests.api_support import ApiFixture


class FoundationTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_liveness_has_no_inventory(self):
        fixture = ApiFixture()
        self.addCleanup(fixture.close)
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/status",
            "raw_path": b"/api/status",
            "query_string": b"",
            "headers": [(b"host", b"mail.rsalehin24.me")],
            "http_version": "1.1",
            "scheme": "https",
            "server": ("mail.rsalehin24.me", 443),
            "client": ("127.0.0.1", 10000),
        }
        await create_app(fixture.services)(scope, receive, send)
        self.assertEqual(messages[0]["status"], 200)
        self.assertEqual(messages[1]["body"], b'{"status":"ok"}')
