"""Webmail against pinned Maddy 0.9.6 and an isolated, certificate-verified relay."""

import asyncio
import secrets
import ssl
import unittest
import uuid
from email import policy
from email.message import EmailMessage
from unittest.mock import patch

from backend.offline import OfflineRecovery
from backend.webmail_imap import ImapTransport
from backend.webmail_models import (
    Bulk,
    Compose,
    LabelChange,
    MessageRef,
    ProfileChange,
    Search,
    SendRequest,
)
from backend.webmail_sending import Submission
from backend.webmail_services import MailServices
from helper.protocol import HelperRequest
from host.webmail_retention import RETENTION_SECONDS, TrashRetention, observe
from shared.errors import ServiceError
from tests.auth_support import AuthFixture
from tests.fixture_values import MAILBOX_PASSWORD
from tests.maddy import test_submission
from tests.maddy_support import imap_login


class WebmailMaddyTests(unittest.TestCase):
    def setUp(self):
        self.peer = test_submission.SubmissionTests(
            "test_tls_password_sender_and_unauthenticated_relay_guards"
        )
        self.addCleanup(self.peer.doCleanups)
        self.peer.setUp()
        self.fixture = self.peer.fixture
        self.state = AuthFixture()
        self.addCleanup(self.state.close)
        context = ssl.create_default_context(cafile=self.fixture.certificates.trust)
        self.app = MailServices(
            self.state.auth, ImapTransport("127.0.0.1", self.fixture.imap_port, context)
        )
        self.addCleanup(self.app.close)
        self.app.sending.submission = Submission("127.0.0.1", self.fixture.submission_port, context)
        _, self.token = self.app.auth.login("sender@rsalehin24.me", MAILBOX_PASSWORD, "fixture")

    def test_independent_identity_detects_native_password_revision(self):
        before = self.fixture.helper.handle(
            HelperRequest("mailbox_identity", str(uuid.uuid4()), "sender@rsalehin24.me")
        )
        self.fixture.helper.handle(
            HelperRequest(
                "password", str(uuid.uuid4()), "sender@rsalehin24.me", "new native fixture password"
            )
        )
        after = self.fixture.helper.handle(
            HelperRequest("mailbox_identity", str(uuid.uuid4()), "sender@rsalehin24.me")
        )
        self.assertEqual(before["native_id"], after["native_id"])
        self.assertEqual(before["inbox_validity"], after["inbox_validity"])
        self.assertNotEqual(before["credential_fingerprint"], after["credential_fingerprint"])

    def append(self, folder="INBOX", subject="webmail synthetic", attachment=False, flags=""):
        message = EmailMessage(policy=policy.SMTP)
        message["From"] = "External Sender <outside@example.net>"
        message["To"] = "sender@rsalehin24.me"
        message["Subject"] = subject
        message["Date"] = "Thu, 1 Jan 2004 12:00:00 +0000"
        message.set_content("A searchable body with বাংলা and synthetic content.")
        message.add_alternative(
            "<p>Hello <strong>world</strong></p><script>bad()</script>"
            '<img src="https://tracker.invalid/pixel" onerror="bad()">',
            subtype="html",
        )
        if attachment:
            message.add_attachment(
                b"synthetic attachment\x00payload",
                maintype="application",
                subtype="octet-stream",
                filename="../safe.bin",
            )
        with self.app.auth.connect(self.token) as (_, mail):
            return mail.append(folder, message.as_bytes(), flags)

    def search(self, **fields):
        return self.app.mail.messages(self.token, Search(**fields))

    def test_cached_connections_are_session_owned_and_close_on_logout_and_expiry(self):
        self.append()
        with self.app.auth.connect(self.token) as (_, first):
            pass
        with self.app.auth.connect(self.token) as (_, reused):
            self.assertIs(first, reused)
        cached = next(iter(self.app.auth.connections.values()))
        frame = cached.manager.gen.gi_frame
        self.assertEqual(frame.f_locals["password"], "")
        self.assertEqual(frame.f_locals["credentials"], b"")
        _, other = self.app.auth.login("other@rsalehin24.me", MAILBOX_PASSWORD, "other")
        self.assertEqual(self.app.mail.messages(other, Search())["total"], 0)
        with self.app.auth.connect(other) as (_, other_connection):
            self.assertIsNot(first, other_connection)
        self.app.auth.logout(other)
        self.assertEqual(other_connection.raw.sock.fileno(), -1)
        self.state.now += 901
        self.app.auth.cleanup()
        self.assertEqual(self.app.auth.connections, {})
        self.assertEqual(first.raw.sock.fileno(), -1)

    def test_password_mutation_rejects_a_recreated_mailbox_generation(self):
        ref = self.append()
        payload = {
            "protocol": 1,
            "operation": "password",
            "request_id": str(uuid.uuid4()),
            "email": "sender@rsalehin24.me",
            "password": "replacement synthetic password",
            "expected_inbox_validity": ref.uidvalidity + 1,
        }
        with self.assertRaises(ServiceError) as caught:
            self.fixture.helper.handle(HelperRequest.parse(payload))
        self.assertEqual(caught.exception.code, "mailbox_changed_refresh")
        self.assertIsNone(self.fixture.helper.journal.lookup(payload["request_id"]))
        with imap_login(self.fixture, payload["email"], MAILBOX_PASSWORD):
            pass
        payload["expected_inbox_validity"] = ref.uidvalidity
        result = self.fixture.helper.handle(HelperRequest.parse(payload))
        self.assertEqual(result["state"], "complete")
        with imap_login(self.fixture, payload["email"], payload["password"]):
            pass

    def test_lists_do_not_mark_read_and_body_and_attachments_are_isolated(self):
        ref = self.append(attachment=True)
        before = self.search()
        path, _ = self.app.files.download(self.token, ref, "2")
        self.app.files.release(path)
        self.assertTrue(before["messages"][0]["unread"])
        self.assertEqual(self.search(unread=True)["total"], 1)
        message = self.app.mail.message(self.token, ref)
        self.assertIn("searchable", message["text"])
        self.assertNotIn("<script", message["html"])
        self.assertNotIn("onerror", message["html"])
        self.assertNotIn('src="https://tracker', message["html"])
        self.assertEqual(message["blocked_images"], 1)
        self.assertEqual(len(message["attachments"]), 1)
        path, name = self.app.files.download(self.token, ref, message["attachments"][0]["part"])
        self.assertEqual(path.read_bytes(), b"synthetic attachment\x00payload")
        path.unlink()
        self.assertNotIn("/", name)
        with imap_login(self.fixture, "sender@rsalehin24.me", MAILBOX_PASSWORD) as external:
            external.select("INBOX")
            self.assertIn(b"\\Seen", external.uid("FETCH", str(ref.uid), "(FLAGS)")[1][0])
        self.assertEqual(self.search(unread=True)["total"], 0)

    def test_keyword_persistence_move_and_partial_bulk_with_stale_uidvalidity(self):
        first, second = self.append(subject="first"), self.append(subject="second")
        label = self.app.mail.save_label(self.token, LabelChange(name="Work", color="#123456"))
        result = self.app.mail.bulk(
            self.token, Bulk(messages=[first, second], action="label", label=label["id"])
        )
        self.assertTrue(all(r["complete"] for r in result["results"]))
        self.assertEqual(self.search(label=label["id"])["total"], 2)
        moved = self.app.mail.bulk(
            self.token, Bulk(messages=[first], action="move", destination="Archive")
        )
        self.assertTrue(moved["results"][0]["complete"], moved)
        self.assertEqual(self.search(folder=None, label=label["id"])["total"], 2)
        with imap_login(self.fixture, "sender@rsalehin24.me", MAILBOX_PASSWORD) as external:
            external.select("Archive")
            self.assertIn(
                ("wml_" + label["id"]).encode(), external.uid("FETCH", "1:*", "(FLAGS)")[1][0]
            )
        invalid = MessageRef(
            folder=second.folder, uidvalidity=second.uidvalidity + 1, uid=second.uid
        )
        result = self.app.mail.bulk(self.token, Bulk(messages=[invalid, second], action="read"))
        self.assertFalse(result["results"][0]["complete"])
        self.assertTrue(result["results"][1]["complete"])
        self.app.mail.delete_label(self.token, label["id"])
        self.assertEqual(self.search(folder=None)["total"], 2)

    def test_search_unicode_all_folders_exclusions_and_date_not_delivery_age(self):
        self.append(subject="বাংলা search")
        self.append(folder="Trash", subject="trash search")
        self.append(folder="Junk", subject="junk search")
        self.assertEqual(self.search(folder=None)["total"], 1)
        self.assertEqual(
            self.search(folder=None, include_trash=True, include_junk=True)["total"], 3
        )
        self.assertEqual(self.search(subject="বাংলা")["total"], 1)
        self.assertEqual(self.search(body="বাংলা")["total"], 1)
        self.assertEqual(self.search(sender="outside@example.net")["total"], 1)
        self.assertEqual(self.search(recipient="sender@rsalehin24.me")["total"], 1)
        self.assertEqual(self.search(before="2005-01-01")["total"], 0)

    def test_drafts_send_one_sent_copy_and_idempotent_replay(self):
        draft = self.app.sending.save_draft(
            self.token, Compose(to="receiver@example.net", subject="first draft", text="draft")
        )
        ref = MessageRef.model_validate(draft["ref"])
        saved = self.app.sending.save_draft(
            self.token,
            Compose(
                to="receiver@example.net", subject="updated draft", text="final draft", draft=ref
            ),
        )
        self.assertEqual(self.search(folder="Drafts")["total"], 1)
        request = SendRequest(
            operation_id=secrets.token_hex(16),
            to="receiver@example.net",
            bcc="private@example.net",
            subject="sent once",
            text="submitted",
            draft=MessageRef.model_validate(saved["ref"]),
        )
        sent = self.app.sending.send(self.token, request)
        self.assertEqual(sent["state"], "complete", sent)
        self.assertEqual(self.app.sending.send(self.token, request)["state"], "complete")
        self.assertEqual(self.search(folder="Drafts")["total"], 0)
        self.assertEqual(self.search(folder="Sent")["total"], 1)
        with imap_login(self.fixture, "sender@rsalehin24.me", MAILBOX_PASSWORD) as external:
            external.select("Sent")
            self.assertEqual(len(external.uid("SEARCH", None, "ALL")[1][0].split()), 1)
            self.assertNotIn(b"\\Draft", external.uid("FETCH", "1:*", "(FLAGS)")[1][0])

    def test_attachment_draft_can_be_saved_repeatedly_without_losing_its_source(self):
        async def chunks():
            yield b"kept across autosaves"

        upload = asyncio.run(self.app.files.upload(self.token, "বাংলা.txt", chunks()))
        saved = self.app.sending.save_draft(
            self.token, Compose(to="receiver@example.net", text="first", uploads=[upload["id"]])
        )
        self.assertNotIn(upload["id"], self.app.files.uploads)
        self.assertEqual(self.app.files.reserved, 0)
        ref = MessageRef.model_validate(saved["ref"])
        saved_again = self.app.sending.save_draft(
            self.token,
            Compose(
                to="receiver@example.net",
                text="second",
                draft=ref,
                attachment_source=ref,
                attachments=[saved["attachments"][0]["part"]],
            ),
        )
        self.assertEqual(self.search(folder="Drafts")["total"], 1)
        final_ref = MessageRef.model_validate(saved_again["ref"])
        self.assertEqual(saved_again["attachments"][0]["filename"], "বাংলা.txt")
        path, filename = self.app.files.download(
            self.token, final_ref, saved_again["attachments"][0]["part"]
        )
        try:
            self.assertEqual(path.read_bytes(), b"kept across autosaves")
            self.assertEqual(filename, "বাংলা.txt")
        finally:
            self.app.files.release(path)

    def test_ten_mib_outgoing_attachment_and_one_sent_copy_with_larger_test_budget(self):
        # Functional maximum-size check on an isolated local peer. The current
        # server's smaller production budgets failed the separate capacity gate.
        self.fixture.command(
            ("update", "--memory", "192m", "--memory-swap", "192m", self.fixture.identifier)
        )

        async def chunks():
            for _ in range(160):
                yield b"x" * 65536

        upload = asyncio.run(self.app.files.upload(self.token, "maximum.bin", chunks()))
        result = self.app.sending.send(
            self.token,
            SendRequest(
                operation_id=secrets.token_hex(16),
                to="other@rsalehin24.me",
                subject="Maximum synthetic attachment",
                text="Synthetic local delivery",
                uploads=[upload["id"]],
            ),
        )
        self.assertEqual(result["state"], "complete", result)
        self.assertNotIn(upload["id"], self.app.files.uploads)
        with imap_login(self.fixture, "sender@rsalehin24.me", MAILBOX_PASSWORD) as external:
            self.assertEqual(external.select("Sent")[1], [b"1"])
            size = external.uid("FETCH", "1:*", "(RFC822.SIZE)")[1][0]
            encoded_size = int(size.split(b"RFC822.SIZE ")[1].split()[0].rstrip(b")"))
            self.assertGreater(encoded_size, 10 * 1024 * 1024)
            self.assertLess(encoded_size, 16 * 1024 * 1024)
        self.peer.wait_for(lambda: len(self.peer.peer.messages) == 1)
        self.assertIn(b"maximum.bin", self.peer.peer.messages[0])
        self.assertGreater(len(self.peer.peer.messages[0]), 10 * 1024 * 1024)

    def test_confirmed_permanent_deletion_is_queued_without_broad_expunge(self):
        ref = self.append(folder="Trash")
        self.assertFalse(
            self.app.mail.bulk(self.token, Bulk(messages=[ref], action="delete"))["results"][0][
                "complete"
            ]
        )
        result = self.app.mail.bulk(
            self.token, Bulk(messages=[ref], action="delete", confirmation="DELETE PERMANENTLY")
        )
        self.assertTrue(result["results"][0]["complete"])
        self.assertEqual(
            result["results"][0]["warning"], "permanent_deletion_queued_for_hourly_maintenance"
        )
        self.assertEqual(self.search(folder="Trash")["total"], 0)
        with self.state.database.transaction() as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM mailbox_permanent_deletions").fetchone()[
                    0
                ],
                1,
            )
            connection.execute("UPDATE webmail_maintenance SET cleanup_enabled=1")
        due = TrashRetention(self.state.database).reconcile(
            observe(self.fixture.data / "imapsql.db"), self.state.now
        )
        self.assertEqual(len(due), 1)

    def test_uncertain_smtp_never_resends_and_sent_failure_retries_only_copy(self):
        request = SendRequest(
            operation_id=secrets.token_hex(16),
            to="receiver@example.net",
            subject="uncertain",
            text="no automatic resend",
        )
        with patch.object(
            self.app.sending.submission,
            "submit",
            side_effect=ServiceError("submission_uncertain", 409),
        ) as submit:
            self.assertEqual(
                self.app.sending.send(self.token, request)["state"], "submission_uncertain"
            )
            self.app.sending.send(self.token, request)
            self.app.sending.retry_sent(self.token, request.operation_id)
            self.assertEqual(submit.call_count, 1)
        another = request.model_copy(update={"operation_id": secrets.token_hex(16)})
        original = self.app.sending._save_sent
        with patch.object(
            self.app.sending,
            "_save_sent",
            side_effect=lambda token, identifier: self.app.sending.operation(token, identifier),
        ):
            self.assertEqual(self.app.sending.send(self.token, another)["state"], "accepted")
        with patch.object(self.app.sending.submission, "submit") as submit:
            self.assertEqual(original(self.token, another.operation_id)["state"], "complete")
            self.assertEqual(original(self.token, another.operation_id)["state"], "complete")
            submit.assert_not_called()
        self.assertEqual(self.search(folder="Sent")["total"], 1)

    def test_trash_entry_age_external_moves_restore_and_native_offline_removal(self):
        ref = self.append()
        moved = self.app.mail.bulk(self.token, Bulk(messages=[ref], action="trash"))
        trash_ref = MessageRef.model_validate(moved["results"][0]["destination"])
        with self.state.database.transaction() as connection:
            timestamp = connection.execute("SELECT entered FROM mailbox_trash").fetchone()[0]
        self.assertEqual(timestamp, self.state.now)
        retention = TrashRetention(self.state.database)
        store = self.fixture.data / "imapsql.db"
        observed = observe(store)
        self.assertEqual(retention.reconcile(observed, self.state.now), [])
        retention.reconcile(observed, self.state.now, True)
        self.assertEqual(retention.reconcile(observed, self.state.now + RETENTION_SECONDS - 1), [])
        due = retention.reconcile(observed, self.state.now + RETENTION_SECONDS)
        self.assertEqual(len(due), 1)
        changed = {
            email: {**entry, "inbox_validity": entry["inbox_validity"] + 1}
            for email, entry in observed.items()
        }
        with self.assertRaises(ServiceError):
            retention.validate_removal(due[0], changed)
        restored = self.app.mail.bulk(self.token, Bulk(messages=[trash_ref], action="restore"))
        self.assertTrue(restored["results"][0]["complete"])
        self.assertEqual(
            retention.reconcile(observe(store), self.state.now + RETENTION_SECONDS), []
        )
        external = self.append(folder="Trash", subject="external client old date")
        retention.reconcile(observe(store), self.state.now + RETENTION_SECONDS)
        self.assertEqual(
            retention.reconcile(observe(store), self.state.now + RETENTION_SECONDS + 1), []
        )
        # Test the actual restricted native primitive with the writer stopped.
        self.app.auth.logout(self.token)
        self.assertEqual(self.app.auth.vault, {})
        self.fixture.command(("stop", self.fixture.identifier))
        try:
            self.fixture.command(
                (
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--volumes-from",
                    self.fixture.container,
                    "--tmpfs",
                    "/run/maddy:size=1048576,mode=0700",
                    "--entrypoint",
                    "/usr/local/libexec/mail-admin-maddy",
                    self.fixture.image_reference(),
                    "imap-msgs",
                    "remove",
                    "--uid",
                    "--yes",
                    "sender@rsalehin24.me",
                    external.folder,
                    str(external.uid),
                )
            )
            self.assertNotIn(
                (external.folder, external.uidvalidity, external.uid),
                observe(store)["sender@rsalehin24.me"]["entries"],
            )
        finally:
            self.fixture.command(("start", self.fixture.identifier))

    def test_recovery_preserves_metadata_and_invalidates_sessions_and_cleanup(self):
        self.app.mail.save_label(self.token, LabelChange(name="Retained", color="#123456"))
        self.app.auth.save_profile(
            self.token,
            ProfileChange(
                display_name="Saved", reply_to="", signature="plain signature", theme="dark"
            ),
        )
        with self.state.database.transaction() as connection:
            connection.execute("UPDATE webmail_maintenance SET cleanup_enabled=1")
        OfflineRecovery(self.state.auth).prepare_restore()
        with self.assertRaises(ServiceError):
            self.app.auth.require(self.token)
        with self.state.database.transaction() as connection:
            self.assertEqual(
                connection.execute("SELECT name FROM mailbox_labels").fetchone()[0], "Retained"
            )
            self.assertEqual(
                connection.execute("SELECT display_name FROM mailbox_users").fetchone()[0], "Saved"
            )
            self.assertEqual(
                connection.execute("SELECT cleanup_enabled FROM webmail_maintenance").fetchone()[0],
                0,
            )
