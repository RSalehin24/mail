import asyncio
import threading
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from backend.webmail_files import MailFiles
from backend.webmail_models import SendRequest
from backend.webmail_pool import MailPool
from backend.webmail_services import MailServices
from shared.errors import ServiceError
from tests.fixture_values import MAILBOX_PASSWORD
from tests.webmail_support import WebmailApiFixture


class WebmailResourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_transfers_leave_interactive_reads_available_and_queue_bounded(self):
        fixture = WebmailApiFixture()
        self.addCleanup(fixture.close)
        app = MailServices(fixture.auth, fixture.imap, independent_io=True)
        self.addCleanup(app.close)
        release, entered = threading.Event(), threading.Event()

        def transfer():
            entered.set()
            release.wait(3)

        task = asyncio.create_task(app.transfers.run(transfer))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            self.assertEqual(
                await asyncio.wait_for(app.pool.run(lambda: "responsive"), 0.5), "responsive"
            )
            self.assertEqual(await asyncio.wait_for(app.compose(lambda: "sent"), 0.5), "sent")
        finally:
            release.set()
            await task

    async def test_upload_admission_reserves_retained_space_before_streaming(self):
        fixture = WebmailApiFixture()
        self.addCleanup(fixture.close)
        app = fixture.services.webmail
        _, token = app.auth.login("first@rsalehin24.me", MAILBOX_PASSWORD, "fixture")
        app.files.close()
        app.files = MailFiles(app.auth, capacity_mib=20)
        release = asyncio.Event()

        async def source():
            yield b"a"
            await release.wait()

        first = asyncio.create_task(app.files.upload(token, "first", source()))
        second = asyncio.create_task(app.files.upload(token, "second", source()))
        await asyncio.sleep(0)
        try:
            with self.assertRaises(ServiceError) as caught:
                await app.files.upload(token, "excess", source())
            self.assertEqual(caught.exception.code, "attachment_capacity_busy")
        finally:
            release.set()
            await asyncio.gather(first, second)
        self.assertEqual(app.files.upload_reserved, 0)
        self.assertEqual(app.files.reserved, 0)
        self.assertEqual(len(app.files.uploads), 2)

    async def test_account_mutation_waits_for_readers_and_excludes_new_readers(self):
        fixture = WebmailApiFixture()
        self.addCleanup(fixture.close)
        auth = fixture.services.webmail.auth
        email = "first@rsalehin24.me"
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def mutation():
            with auth.owner_guard(email, wait=True):
                entered.set()
                release.wait(2)

        with auth.owner_guard(email):
            task = asyncio.create_task(asyncio.to_thread(mutation))
            for _ in range(100):
                if auth.owner_locks[email].waiting_writers:
                    break
                await asyncio.sleep(0.005)
            self.assertFalse(entered.is_set())
            self.assertEqual(auth.owner_locks[email].waiting_writers, 1)
            with self.assertRaises(ServiceError):
                with auth.owner_guard(email):
                    self.fail("New read admitted during account mutation")
        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
        with self.assertRaises(ServiceError):
            with auth.owner_guard(email):
                self.fail("Read admitted while account mutation holds ownership")
        release.set()
        await asyncio.wait_for(task, 2)
        self.assertEqual(auth.owner_versions[email], 2)

    async def test_login_proof_cannot_bind_identity_after_account_mutation(self):
        fixture = WebmailApiFixture()
        self.addCleanup(fixture.close)
        auth = fixture.services.webmail.auth
        email = "first@rsalehin24.me"
        connect = fixture.imap.connect

        @contextmanager
        def account_changed(address, password):
            with connect(address, password) as mail:
                yield mail
            with auth.owner_guard(email, wait=True):
                pass

        with patch.object(fixture.imap, "connect", account_changed):
            with self.assertRaises(ServiceError) as caught:
                auth.login(email, MAILBOX_PASSWORD, "fixture")
        self.assertEqual(caught.exception.code, "mailbox_changed_refresh")
        self.assertEqual(auth.vault, {})
        with fixture.database.transaction() as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM mailbox_users").fetchone()[0], 0
            )

    async def test_interrupted_upload_removes_spool_and_releases_reserved_capacity(self):
        fixture = WebmailApiFixture()
        self.addCleanup(fixture.close)
        app = fixture.services.webmail
        _, token = app.auth.login("first@rsalehin24.me", MAILBOX_PASSWORD, "fixture")

        async def interrupted():
            yield b"first chunk"
            raise OSError("synthetic disconnect")

        with self.assertRaises(OSError):
            await app.files.upload(token, "interrupted.bin", interrupted())
        self.assertEqual(app.files.reserved, 0)
        self.assertEqual(list(app.files.root.iterdir()), [])

    async def test_encoded_limit_rejects_before_creating_or_submitting_a_send(self):
        fixture = WebmailApiFixture()
        self.addCleanup(fixture.close)
        app = fixture.services.webmail
        _, token = app.auth.login("first@rsalehin24.me", MAILBOX_PASSWORD, "fixture")
        request = SendRequest(operation_id="a" * 32, to="recipient@example.net", text="x" * 4096)
        with (
            patch("backend.webmail_sending.MAX_MESSAGE", 1024),
            patch.object(app.sending.submission, "submit") as submit,
        ):
            with self.assertRaises(ServiceError) as caught:
                app.sending.send(token, request)
        self.assertEqual(caught.exception.code, "encoded_message_limit_exceeded")
        submit.assert_not_called()
        self.assertEqual(app.sending.operations(token), [])

    async def test_three_active_operations_queue_bound_and_cancelled_io_keeps_its_slot(self):
        pool = MailPool()
        self.addCleanup(pool.close)
        release = threading.Event()
        self.addCleanup(release.set)
        started = 0
        guard = threading.Lock()

        def work():
            nonlocal started
            with guard:
                started += 1
            release.wait(3)

        tasks = [asyncio.create_task(pool.run(work)) for _ in range(3)]
        for _ in range(100):
            if started == 3:
                break
            await asyncio.sleep(0.005)
        self.assertEqual(started, 3)
        waiting = [asyncio.create_task(pool.run(lambda: None)) for _ in range(9)]
        await asyncio.sleep(0.02)
        with self.assertRaises(ServiceError) as caught:
            await pool.run(lambda: None)
        self.assertEqual(caught.exception.code, "webmail_busy")
        for task in tasks + waiting:
            task.cancel()
        await asyncio.gather(*tasks, *waiting, return_exceptions=True)
        self.assertEqual(pool.waiting, 0)
        self.assertFalse(pool.pool.capacity.acquire(blocking=False))
        release.set()
