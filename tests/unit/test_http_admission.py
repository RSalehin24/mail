import asyncio
import dataclasses
import unittest

from backend.boundary import HttpBoundary
from tests.api_support import ApiFixture


class HttpAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_requests_remain_bounded_and_disconnect_releases_capacity(self):
        fixture = ApiFixture()
        self.addCleanup(fixture.close)
        entered, release = asyncio.Event(), asyncio.Event()

        async def application(scope, receive, send):
            entered.set()
            await release.wait()

        boundary = HttpBoundary(application, dataclasses.replace(
            fixture.services.settings, http_concurrency=1,
        ))
        messages = []

        async def send(message):
            messages.append(message)

        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}

        def scope():
            return {'type': 'http', 'scheme': 'https', 'method': 'GET', 'path': '/api/status',
                    'headers': [(b'host', b'mail.rsalehin24.me')]}

        first = asyncio.create_task(boundary(scope(), receive, send))
        await entered.wait()
        await boundary(scope(), receive, send)
        self.assertEqual(messages[0]['status'], 503)
        self.assertIn((b'retry-after', b'1'), messages[0]['headers'])
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertFalse(boundary.capacity.locked())
        release.set()
        await boundary(scope(), receive, send)
