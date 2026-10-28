import time
import unittest
import uuid
from contextlib import nullcontext
from types import SimpleNamespace

from helper.forwarding import ForwardingControl
from helper.protocol import HelperRequest
from host.forwarding import ForwardingWorker
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import RealMaddyFixture, imap_login
from tests.restore_support import bodies, deliver


class MailboxForwardingTests(unittest.TestCase):
    def test_new_delivery_is_forwarded_once_without_removing_local_mail(self):
        fixture = RealMaddyFixture()
        self.addCleanup(fixture.close)
        fixture.start()
        email = "forwarding@rsalehin24.me"
        result = fixture.helper.handle(
            HelperRequest("create", str(uuid.uuid4()), email, MAILBOX_PASSWORD)
        )
        self.assertEqual(result["state"], "complete")
        deliver(fixture, email, "existing mail stays local only")
        root = fixture.root / "forwarding"
        root.mkdir(mode=0o700)
        control = ForwardingControl(root, fixture.data)
        control.handle(
            "forwarding_save",
            str(uuid.uuid4()),
            {
                "email": email,
                "destination": "recipient@example.com",
                "enabled": True,
                "revision": 0,
            },
            time.time(),
        )
        (root / "forwarding-worker.lock").touch(mode=0o600)
        sent = []
        worker = ForwardingWorker(
            control,
            SimpleNamespace(consistent=nullcontext),
            lambda *args: sent.append(args),
        )
        worker.run()
        self.assertEqual(sent, [])
        deliver(fixture, email, "new message forwarded and kept locally")
        worker.run()
        worker.run()
        self.assertEqual(len(sent), 1)
        self.assertIn(b"new message forwarded and kept locally", sent[0][3])
        with imap_login(fixture, email, MAILBOX_PASSWORD) as imap:
            content = bodies(imap)
            self.assertIn(b"existing mail stays local only", content)
            self.assertIn(b"new message forwarded and kept locally", content)
