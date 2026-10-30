"""Durable submission, Sent saving and draft cleanup are independently recoverable steps."""

import base64
import datetime
import hashlib
import json
import mimetypes
import secrets
import smtplib
import ssl
import threading
import time
from contextlib import suppress
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage
from typing import Any

from backend.webmail import Webmail
from backend.webmail_files import MailFiles
from backend.webmail_imap import MAX_ATTACHMENTS, MAX_MESSAGE, MailConnection, quote, utf7
from backend.webmail_mime import email_address, header, recipients, structure
from backend.webmail_models import Compose, MessageRef, SendRequest
from shared.errors import ServiceError
from shared.installation import site
from shared.validation import utf8_size


class Submission:
    def __init__(
        self, host: str | None = None, port: int = 587, context: ssl.SSLContext | None = None
    ) -> None:
        self.host, self.port = host or site().mail_hostname, port
        self.context = context or ssl.create_default_context()

    def submit(self, email: str, password: str, destinations: list[str], message: bytes) -> None:
        smtp, started = None, False
        deadline = time.monotonic() + 8

        def budget() -> None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("submission deadline exceeded")
            if smtp is not None and smtp.sock is not None:
                smtp.sock.settimeout(remaining)

        try:
            smtp = smtplib.SMTP(self.host, self.port, timeout=8)
            budget()
            smtp.ehlo()
            budget()
            smtp.starttls(context=self.context)
            budget()
            smtp.ehlo()
            budget()
            code, _ = smtp.docmd(
                "AUTH",
                "PLAIN " + base64.b64encode(("\0" + email + "\0" + password).encode()).decode(),
            )
            if code != 235:
                raise ServiceError("submission_rejected", 409)
            budget()
            code, _ = smtp.mail(
                email, options=[f"SIZE={len(message)}"] if smtp.has_extn("size") else []
            )
            if code != 250:
                raise ServiceError("submission_rejected", 409)
            for destination in destinations:
                budget()
                code, _ = smtp.rcpt(destination)
                if code not in (250, 251):
                    smtp.rset()
                    raise ServiceError("submission_rejected", 409)
            budget()
            started = True
            code, _ = smtp.data(message)
            if code != 250:
                raise ServiceError("submission_rejected", 409)
        except smtplib.SMTPResponseException as error:
            raise ServiceError("submission_rejected", 409) from error
        except (OSError, smtplib.SMTPException) as error:
            raise ServiceError(
                "submission_uncertain" if started else "submission_unavailable",
                409 if started else 503,
            ) from error
        finally:
            if smtp is not None:
                smtp.close()


class MailSending:
    def __init__(
        self, webmail: Webmail, files: MailFiles, submission: Submission | None = None
    ) -> None:
        self.webmail, self.files = webmail, files
        self.auth, self.database = webmail.auth, webmail.database
        self.submission = submission or Submission()
        self.lock = threading.Lock()

    def build(
        self, token: str, body: Compose, marker: str, validate: bool = True
    ) -> tuple[bytes, list[str]]:
        profile = self.auth.profile(token)
        utf8_size(body.text)
        destinations = (
            list(dict.fromkeys(recipients(body.to) + recipients(body.cc) + recipients(body.bcc)))
            if validate
            else []
        )
        if len(destinations) > 100:
            raise ServiceError("recipient_limit_exceeded", 400)
        message = EmailMessage(policy=policy.SMTP)
        # No client-supplied sender field exists. Display name cannot inject headers.
        message["From"] = Address(
            display_name=header(profile["display_name"]), addr_spec=profile["email"]
        )
        if profile["reply_to"]:
            message["Reply-To"] = email_address(profile["reply_to"])
        for key, value in (
            ("To", body.to),
            ("Cc", body.cc),
            ("Bcc", body.bcc),
            ("Subject", body.subject),
            ("In-Reply-To", body.in_reply_to),
            ("References", body.references),
        ):
            if value:
                message[key] = header(value)
        message["Date"] = datetime.datetime.now(datetime.UTC)
        message["Message-ID"] = f"<{marker}@{site().domain}>"
        message["X-Webmail-Operation"] = marker
        message.set_content(body.text)
        total = 0
        for upload in self.files.get(token, body.uploads):
            total += upload["size"]
            if total > MAX_ATTACHMENTS:
                raise ServiceError("attachment_limit_exceeded", 413)
            mime = mimetypes.guess_type(upload["filename"])[0] or "application/octet-stream"
            message.add_attachment(
                upload["path"].read_bytes(),
                maintype=mime.split("/")[0],
                subtype=mime.split("/")[1],
                filename=upload["filename"],
            )
        if body.attachments:
            attachment_source = body.attachment_source or body.draft
            if attachment_source is None:
                raise ServiceError("draft_reference_required", 400)
            for part in dict.fromkeys(body.attachments):
                path, name = self.files.download(token, attachment_source, part)
                try:
                    total += path.stat().st_size
                    if total > MAX_ATTACHMENTS:
                        raise ServiceError("attachment_limit_exceeded", 413)
                    message.add_attachment(
                        path.read_bytes(),
                        maintype="application",
                        subtype="octet-stream",
                        filename=name,
                    )
                finally:
                    self.files.release(path)
        encoded = message.as_bytes()
        if len(encoded) > MAX_MESSAGE:
            raise ServiceError("encoded_message_limit_exceeded", 413)
        return encoded, destinations

    @staticmethod
    def submission_bytes(encoded: bytes) -> bytes:
        # Strip only the Bcc header (including its continuation lines). Re-parsing
        # a 14 MiB MIME message can multiply its memory use through Unicode buffers.
        boundary = encoded.index(b"\r\n\r\n")
        headers, skipping = [], False
        for line in encoded[:boundary].split(b"\r\n"):
            if not line.startswith((b" ", b"\t")):
                skipping = line.partition(b":")[0].lower() == b"bcc"
            if not skipping:
                headers.append(line)
        return b"".join((b"\r\n".join(headers) + b"\r\n\r\n", memoryview(encoded)[boundary + 4 :]))

    def release_uploads(self, token: str, uploads: list[str]) -> None:
        for identifier in dict.fromkeys(uploads):
            with suppress(ServiceError, OSError):
                self.files.delete(token, identifier)

    def save_draft(self, token: str, body: Compose) -> dict[str, Any]:
        if not self.lock.acquire(blocking=False):
            raise ServiceError("compose_busy", 503, 2)
        try:
            encoded, _ = self.build(token, body, secrets.token_hex(16), validate=False)
            with self.auth.connect(token) as (user, mail):
                drafts = self.webmail.special(mail, "drafts")
                if body.draft is not None and body.draft.folder != drafts:
                    raise ServiceError("draft_reference_required", 400)
                ref = mail.append(drafts, encoded, "\\Draft")
                mail.check(ref)
                attachments = [p for p in structure(mail, ref.uid) if p["attachment"]]
                warning = ""
                if body.draft is not None:
                    try:
                        self.webmail.discard_draft(user, mail, body.draft)
                    except ServiceError:
                        warning = "new_draft_saved_previous_draft_retained"
                self.release_uploads(token, body.uploads)
                return {"ref": ref.model_dump(), "warning": warning, "attachments": attachments}
        finally:
            self.lock.release()

    def operations(self, token: str) -> list[dict[str, Any]]:
        user = self.auth.require(token)
        with self.database.transaction() as connection:
            self._expire_in_progress(connection, user.id)
            rows = connection.execute(
                "SELECT id,state,created,updated,error FROM mailbox_sends "
                "WHERE user_id=? ORDER BY created DESC LIMIT 30",
                (user.id,),
            )
            return [dict(row) for row in rows]

    def operation(self, token: str, identifier: str) -> dict[str, Any]:
        user = self.auth.require(token)
        with self.database.transaction() as connection:
            self._expire_in_progress(connection, user.id)
            row = connection.execute(
                "SELECT * FROM mailbox_sends WHERE id=? AND user_id=?", (identifier, user.id)
            ).fetchone()
        if row is None:
            raise ServiceError("sending_operation_not_found", 404)
        return dict(row)

    def _expire_in_progress(self, connection: Any, user_id: str) -> None:
        for before, after in (
            ("preparing", "preparation_uncertain"),
            ("submitting", "submission_uncertain"),
            ("saving_sent", "sent_save_uncertain"),
        ):
            connection.execute(
                "UPDATE mailbox_sends SET state=?,error=? WHERE user_id=? "
                "AND state=? AND updated<?",
                (after, "completion_not_recorded", user_id, before, self.auth.clock() - 60),
            )

    def state(self, identifier: str, state: str, error: str = "") -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE mailbox_sends SET state=?,error=?,updated=? WHERE id=?",
                (state, error, self.auth.clock(), identifier),
            )

    def send(self, token: str, body: SendRequest) -> dict[str, Any]:
        user = self.auth.require(token)
        with self.auth.owner_guard(user.email):
            return self._send(token, body)

    def _send(self, token: str, body: SendRequest) -> dict[str, Any]:
        user = self.auth.require(token)
        if not self.lock.acquire(blocking=False):
            raise ServiceError("compose_busy", 503, 2)
        try:
            with self.database.transaction() as connection:
                existing = connection.execute(
                    "SELECT user_id FROM mailbox_sends WHERE id=?", (body.operation_id,)
                ).fetchone()
            if existing:
                # Replayed clicks only observe a prior operation; they never send again.
                return self.operation(token, body.operation_id)
            encoded, destinations = self.build(token, body, body.operation_id)
            if not destinations:
                raise ServiceError("recipient_required", 400)
            now = self.auth.clock()
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO mailbox_sends(id,user_id,state,created,updated,"
                    "content_hash,original_draft) VALUES(?,?,?,?,?,?,?)",
                    (
                        body.operation_id,
                        user.id,
                        "preparing",
                        now,
                        now,
                        hashlib.sha256(encoded).hexdigest(),
                        body.draft.model_dump_json() if body.draft else None,
                    ),
                )
            with self.auth.connect(token) as (_, mail):
                drafts = self.webmail.special(mail, "drafts")
                if body.draft is not None and body.draft.folder != drafts:
                    self.state(body.operation_id, "rejected", "draft_reference_required")
                    raise ServiceError("draft_reference_required", 400)
                try:
                    ref = mail.append(drafts, encoded, "\\Draft wmop_" + body.operation_id)
                except ServiceError:
                    self.state(body.operation_id, "preparation_uncertain", "draft_save_uncertain")
                    return self.operation(token, body.operation_id)
                with self.database.transaction() as connection:
                    connection.execute(
                        "UPDATE mailbox_sends SET draft_folder=?,draft_validity=?,"
                        "draft_uid=?,state=? WHERE id=?",
                        (ref.folder, ref.uidvalidity, ref.uid, "submitting", body.operation_id),
                    )
            # SMTP goes to Maddy submission, never directly to provider credentials.
            self.release_uploads(token, body.uploads)
            with self.auth.lock:
                sealed = self.auth.vault.get(user.token_hash)
                if sealed is None:
                    self.state(body.operation_id, "rejected", "session_revoked_before_submission")
                    raise ServiceError("authentication_required", 401)
                password = self.auth.ephemeral.open(sealed[0], user.token_hash)
            # Bcc is stored in the user's draft/Sent copy but never transmitted.
            try:
                self.submission.submit(
                    user.email, password, destinations, self.submission_bytes(encoded)
                )
            except ServiceError as error:
                state = (
                    "submission_uncertain" if error.code == "submission_uncertain" else "rejected"
                )
                self.state(body.operation_id, state, error.code)
                return self.operation(token, body.operation_id)
            self.state(body.operation_id, "accepted")
            return self._save_sent(token, body.operation_id)
        finally:
            self.lock.release()

    def retry_sent(self, token: str, identifier: str) -> dict[str, Any]:
        if not self.lock.acquire(blocking=False):
            raise ServiceError("compose_busy", 503, 2)
        try:
            return self._save_sent(token, identifier)
        finally:
            self.lock.release()

    @staticmethod
    def _ref(operation: dict[str, Any]) -> MessageRef:
        return MessageRef(
            folder=operation["draft_folder"],
            uidvalidity=operation["draft_validity"],
            uid=operation["draft_uid"],
        )

    def _save_sent(self, token: str, identifier: str) -> dict[str, Any]:
        operation = self.operation(token, identifier)
        if operation["state"] not in {
            "accepted",
            "sent_save_failed",
            "saving_sent",
            "sent_save_uncertain",
            "sent_saved",
            "cleanup_failed",
        }:
            return operation
        try:
            with self.auth.connect(token) as (_, mail):
                sent = self.webmail.special(mail, "sent")
                validity, found = mail.find_keyword(sent, "wmop_" + identifier)
                if len(found) > 1:
                    self.state(identifier, "sent_save_uncertain", "duplicate_sent_requires_review")
                    return self.operation(token, identifier)
                if not found:
                    if operation["state"] in {
                        "saving_sent",
                        "sent_save_uncertain",
                        "sent_saved",
                        "cleanup_failed",
                    }:
                        self.state(identifier, "sent_save_uncertain", "sent_result_requires_review")
                        return self.operation(token, identifier)
                    ref = self._ref(operation)
                    mail.check(ref)
                    self.state(identifier, "saving_sent")
                    try:
                        mail.uid("COPY", str(ref.uid), quote(utf7(sent)))
                    except ServiceError as error:
                        self.state(
                            identifier,
                            "sent_save_failed"
                            if error.code == "imap_rejected"
                            else "sent_save_uncertain",
                            error.code,
                        )
                        return self.operation(token, identifier)
                    validity, found = mail.find_keyword(sent, "wmop_" + identifier)
                    if len(found) != 1:
                        self.state(identifier, "sent_save_uncertain", "sent_result_requires_review")
                        return self.operation(token, identifier)
                mail.flags(found[0], False, "\\Draft")
                mail.flags(found[0], True, "\\Seen")
                with self.database.transaction() as connection:
                    connection.execute(
                        "UPDATE mailbox_sends SET sent_folder=?,sent_validity=?,"
                        "sent_uid=?,state=? WHERE id=?",
                        (sent, validity, found[0], "sent_saved", identifier),
                    )
                self._cleanup(token, mail, operation)
                self.state(identifier, "complete")
        except ServiceError as error:
            current = self.operation(token, identifier)
            state = (
                "cleanup_failed"
                if current["state"] == "sent_saved"
                else (
                    "sent_save_uncertain" if current["state"] == "saving_sent" else current["state"]
                )
            )
            self.state(identifier, state, error.code)
        return self.operation(token, identifier)

    def _cleanup(self, token: str, mail: MailConnection, operation: dict[str, Any]) -> None:
        refs = [self._ref(operation)]
        if operation["original_draft"]:
            refs.append(MessageRef.model_validate(json.loads(operation["original_draft"])))
        for ref in refs:
            try:
                self.webmail.discard_draft(self.auth.require(token), mail, ref)
            except ServiceError as error:
                if error.code != "message_gone":
                    raise
