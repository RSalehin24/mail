"""Durable forwarding queue; the transport receives the original message unchanged."""

import email.policy
import email.utils
import smtplib
import time
from collections.abc import Callable
from email.parser import BytesParser
from typing import Any

from helper.forwarding import ForwardingControl
from host.locks import HostLock, LockPolicy
from host.mailbox_store import body_path
from shared.errors import ServiceError

MAX_MESSAGE_BYTES = 25 * 1024**2


class ForwardingWorker:
    def __init__(
        self,
        control: ForwardingControl,
        coordination: Any,
        sender: Callable[[str, str, str, bytes], None],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.control, self.coordination, self.sender, self.clock = (
            control,
            coordination,
            sender,
            clock,
        )

    def discover(self) -> None:
        with self.coordination.consistent():
            with self.control.connect() as db:
                rules = db.execute("SELECT * FROM rules WHERE enabled=1").fetchall()
                for rule in rules:
                    try:
                        rows = self.control.inbox(rule["email"])
                    except ServiceError:
                        # A deleted mailbox disables its rule; restored mail is not
                        # treated as permission to forward old messages automatically.
                        db.execute(
                            "UPDATE rules SET enabled=0,updated=? WHERE email=?",
                            (self.clock(), rule["email"]),
                        )
                        continue
                    for row in rows:
                        if db.execute(
                            "SELECT 1 FROM messages WHERE email=? AND body_key=? AND destination=?",
                            (rule["email"], row["id"], rule["destination"]),
                        ).fetchone():
                            continue
                        raw = None
                        state = "queued"
                        error = ""
                        try:
                            if float(row["date"]) < int(rule["created"]):
                                state = "skipped"
                            elif row["compressAlgo"]:
                                raise ServiceError("forwarding_compression_unsupported", 503)
                            else:
                                path = body_path(self.control.data / "messages", row["id"])
                                if path.stat().st_size > MAX_MESSAGE_BYTES:
                                    raise ServiceError("forwarding_message_too_large", 400)
                                raw = path.read_bytes()
                                parsed = BytesParser(policy=email.policy.default).parsebytes(
                                    raw, headersonly=True
                                )
                                if parsed.get("X-Mail-Admin-Forwarded"):
                                    state = "skipped"
                                    raw = None
                        except (OSError, ValueError, ServiceError) as failure:
                            state = "failed"
                            error = (
                                failure.code
                                if isinstance(failure, ServiceError)
                                else "forwarding_message_unavailable"
                            )
                        db.execute(
                            "INSERT INTO messages"
                            "(email,body_key,destination,state,created,updated,error,body) "
                            "VALUES(?,?,?,?,?,?,?,?)",
                            (
                                rule["email"],
                                row["id"],
                                rule["destination"],
                                state,
                                self.clock(),
                                self.clock(),
                                error,
                                raw if state == "queued" else None,
                            ),
                        )

    def run(self) -> None:
        with HostLock(
            self.control.root / "forwarding-worker.lock",
            LockPolicy(0.1, self.control.root.stat().st_uid),
        ):
            with self.coordination.consistent():
                with self.control.connect() as db:
                    db.execute(
                        "UPDATE messages SET state='uncertain',error='forwarding_interrupted' "
                        "WHERE state='sending'"
                    )
            self.discover()
            for _ in range(10):
                with self.coordination.consistent():
                    with self.control.connect() as db:
                        row = db.execute(
                            "SELECT m.* FROM messages m JOIN rules r ON r.email=m.email "
                            "AND r.destination=m.destination WHERE m.state='queued' "
                            "AND r.enabled=1 ORDER BY m.created LIMIT 1"
                        ).fetchone()
                        if row is None:
                            return
                        db.execute(
                            "UPDATE messages SET state='sending',attempts=attempts+1,updated=? "
                            "WHERE email=? AND body_key=? AND destination=?",
                            (self.clock(), row["email"], row["body_key"], row["destination"]),
                        )
                state = "accepted"
                error = ""
                try:
                    self.sender(
                        row["email"], row["destination"], row["body_key"], bytes(row["body"])
                    )
                except (smtplib.SMTPRecipientsRefused, smtplib.SMTPResponseException, ServiceError):
                    state = "failed"
                    error = "forwarding_provider_rejected"
                except (OSError, smtplib.SMTPException):
                    state = "uncertain"
                    error = "forwarding_outcome_unknown"
                with self.coordination.consistent():
                    with self.control.connect() as db:
                        db.execute(
                            "UPDATE messages SET state=?,error=?,updated=?,body=? "
                            "WHERE email=? AND body_key=? AND destination=?",
                            (
                                state,
                                error,
                                self.clock(),
                                None if state == "accepted" else row["body"],
                                row["email"],
                                row["body_key"],
                                row["destination"],
                            ),
                        )
