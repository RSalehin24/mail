"""Mailbox-scoped IMAP operations; SQLite stores only application metadata."""

import datetime
import html
import re
import secrets
from typing import Any

from backend.webmail_auth import Identity, MailboxAuthentication
from backend.webmail_imap import (
    SYSTEM_FOLDERS,
    MailConnection,
    folder_name,
    quote,
    utf7,
)
from backend.webmail_mime import decode_body, metadata, sanitize, structure
from backend.webmail_models import Bulk, FolderChange, LabelChange, MessageRef, Search
from shared.errors import ServiceError

HEADERS = (
    "BODY.PEEK[HEADER.FIELDS "
    "(FROM TO CC BCC REPLY-TO SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES)]<0.65536>"
)


class Webmail:
    def __init__(self, authentication: MailboxAuthentication) -> None:
        self.auth = authentication
        self.database, self.clock = authentication.database, authentication.clock

    def labels(self, token: str) -> list[dict[str, str]]:
        user = self.auth.require(token)
        with self.database.transaction() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT id,name,color FROM mailbox_labels WHERE user_id=? ORDER BY name",
                    (user.id,),
                )
            ]

    def label(self, user: Identity, identifier: str) -> str:
        with self.database.transaction() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM mailbox_labels WHERE id=? AND user_id=?", (identifier, user.id)
                ).fetchone()
                is None
            ):
                raise ServiceError("label_not_found", 404)
        return "wml_" + identifier

    def save_label(self, token: str, body: LabelChange, identifier: str = "") -> dict[str, str]:
        user = self.auth.require(token)
        if not body.name.strip() or any(ord(c) < 32 for c in body.name):
            raise ServiceError("invalid_label", 400)
        if identifier:
            self.label(user, identifier)
        else:
            identifier = secrets.token_hex(16)
        with self.database.transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM mailbox_labels WHERE user_id=? AND name=? AND id<>?",
                (user.id, body.name, identifier),
            ).fetchone():
                raise ServiceError("label_name_exists", 409)
            connection.execute(
                "INSERT INTO mailbox_labels VALUES(?,?,?,?) ON CONFLICT(id) "
                "DO UPDATE SET name=excluded.name,color=excluded.color",
                (identifier, user.id, body.name, body.color),
            )
        return {"id": identifier, "name": body.name, "color": body.color}

    def delete_label(self, token: str, identifier: str) -> dict[str, bool]:
        user = self.auth.require(token)
        self.label(user, identifier)
        # Stable keyword becomes inert; removing the metadata never deletes mail.
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM mailbox_labels WHERE id=? AND user_id=?", (identifier, user.id)
            )
        return {"deleted": True}

    def folders(self, token: str) -> list[dict[str, Any]]:
        with self.auth.connect(token) as (_, mail):
            folders = mail.folders()
            for folder in folders:
                response = mail.command("status", quote(utf7(folder["name"])), "(UNSEEN MESSAGES)")
                raw = b" ".join(x for x in response if isinstance(x, bytes))
                for key in ("UNSEEN", "MESSAGES"):
                    match = re.search(key.encode() + rb" (\d+)", raw)
                    folder["unread" if key == "UNSEEN" else "total"] = int(match[1]) if match else 0
            return folders

    def change_folder(self, token: str, body: FolderChange) -> dict[str, bool]:
        name = folder_name(body.name)
        with self.auth.connect(token) as (_, mail):
            folders = mail.folders()
            protected = {f["name"] for f in folders if f["special"]}
            if body.action != "create" and (name in protected or name.lower() in SYSTEM_FOLDERS):
                raise ServiceError("system_folder_protected", 403)
            if body.action == "create":
                mail.command("create", quote(utf7(name)))
            elif body.action == "rename":
                mail.command("rename", quote(utf7(name)), quote(utf7(folder_name(body.new_name))))
            else:
                mail.select(name)
                if self._search(mail, ["ALL"]):
                    raise ServiceError("folder_must_be_empty", 409)
                mail.command("delete", quote(utf7(name)))
        return {"complete": True}

    @staticmethod
    def _search(mail: MailConnection, criteria: list[str]) -> list[int]:
        raw = mail.uid("SEARCH", None, *criteria)[0] or b""
        if len(raw) > 2 * 1024 * 1024:
            raise ServiceError("search_result_too_large", 422)
        return sorted((int(x) for x in raw.split()), reverse=True)

    @staticmethod
    def _unicode_search(mail: MailConnection, field: str, value: str) -> set[int]:
        # One UTF-8 literal per command keeps non-ASCII bytes out of quoted
        # protocol syntax. Filtering uses server-side SEARCH and UID intersections.
        if any(c in value for c in "\r\n\0"):
            raise ServiceError("invalid_search", 400)
        mail.raw.literal = value.encode("utf-8")  # type: ignore[assignment]
        try:
            result = mail.uid("SEARCH", "CHARSET", "UTF-8", field)
            raw = result[0] or b""
            if len(raw) > 2 * 1024 * 1024:
                raise ServiceError("search_result_too_large", 422)
            return {int(x) for x in raw.split()}
        finally:
            mail.raw.literal = None

    def messages(self, token: str, query: Search) -> dict[str, Any]:
        with self.auth.connect(token) as (user, mail):
            folders = mail.folders()
            names = {f["name"] for f in folders}
            if query.folder is not None and query.folder not in names:
                raise ServiceError("folder_not_found", 404)
            criteria = ["UNDELETED"]
            unicode_filters = []
            for key, value in (
                ("FROM", query.sender),
                ("TO", query.recipient),
                ("SUBJECT", query.subject),
                ("BODY", query.body),
            ):
                if value:
                    if not value.isascii():
                        # IMAP quoted UTF-8 requires an explicitly negotiated UTF8
                        # mode or a literal. Use a literal with SEARCH CHARSET.
                        unicode_filters.append((key, value))
                        continue
                    if key == "TO":
                        criteria.extend(
                            [
                                "OR",
                                "OR",
                                "TO",
                                quote(value),
                                "CC",
                                quote(value),
                                "BCC",
                                quote(value),
                            ]
                        )
                    else:
                        criteria.extend([key, quote(value)])
            for key, value in (("SINCE", query.after), ("BEFORE", query.before)):
                if value:
                    try:
                        day = datetime.date.fromisoformat(value)
                    except ValueError as error:
                        raise ServiceError("invalid_search_date", 400) from error
                    months = (
                        "Jan",
                        "Feb",
                        "Mar",
                        "Apr",
                        "May",
                        "Jun",
                        "Jul",
                        "Aug",
                        "Sep",
                        "Oct",
                        "Nov",
                        "Dec",
                    )
                    criteria.extend([key, f"{day.day:02}-{months[day.month - 1]}-{day.year}"])
            if query.label:
                criteria.extend(["KEYWORD", self.label(user, query.label)])
            offset, total = (query.page - 1) * query.size, 0
            output: list[dict[str, Any]] = []
            for folder in folders:
                name = folder["name"]
                if query.folder is not None and name != query.folder:
                    continue
                special = folder["special"] or name.lower()
                if query.folder is None and (
                    (special == "trash" and not query.include_trash)
                    or (special == "junk" and not query.include_junk)
                ):
                    continue
                validity = mail.select(name)
                uids = self._search(mail, criteria)
                if query.unread:
                    # go-imap-sql 0.9.6 incorrectly combines multiple negative
                    # flags in one SEARCH. Intersect server-side UID searches.
                    unseen = set(self._search(mail, ["UNSEEN"]))
                    uids = [uid for uid in uids if uid in unseen]
                for field, value in unicode_filters:
                    matches = self._unicode_search(mail, field, value)
                    if field == "TO":
                        matches |= self._unicode_search(mail, "CC", value)
                        matches |= self._unicode_search(mail, "BCC", value)
                    uids = [uid for uid in uids if uid in matches]
                total += len(uids)
                if offset >= len(uids):
                    offset -= len(uids)
                    continue
                chosen = uids[offset : offset + max(0, query.size - len(output))]
                offset = 0
                if not chosen:
                    continue
                result = mail.uid(
                    "FETCH", ",".join(map(str, chosen)), "(UID FLAGS RFC822.SIZE " + HEADERS + ")"
                )
                rows = {}
                for item in result:
                    if not isinstance(item, tuple):
                        continue
                    uid = re.search(rb"\bUID (\d+)", item[0])
                    flags = re.search(rb"\bFLAGS \(([^)]*)\)", item[0])
                    size = re.search(rb"RFC822.SIZE (\d+)", item[0])
                    if uid is None:
                        raise ServiceError("invalid_imap_response", 502)
                    values = flags[1].decode().split() if flags else []
                    rows[int(uid[1])] = {
                        "ref": {"folder": name, "uidvalidity": validity, "uid": int(uid[1])},
                        **metadata(item[1]),
                        "unread": "\\Seen" not in values,
                        "labels": [x[4:] for x in values if x.startswith("wml_")],
                        "size": int(size[1]) if size else 0,
                    }
                output.extend(rows[uid] for uid in chosen if uid in rows)
            return {
                "messages": output,
                "total": total,
                "page": query.page,
                "size": query.size,
                "order": "folder_then_newest_uid" if query.folder is None else "newest_uid",
            }

    def message(self, token: str, ref: MessageRef, remote: bool = False) -> dict[str, Any]:
        with self.auth.connect(token) as (_, mail):
            mail.check(ref)
            parts = structure(mail, ref.uid)
            headers = metadata(mail.literal(ref.uid, HEADERS))
            text, rendered, blocked, truncated = "", "", 0, False
            # HTML and text are fetched independently of attachment bytes.
            for kind in ("text/plain", "text/html"):
                part = next(
                    (p for p in parts if p["content_type"] == kind and not p["attachment"]), None
                )
                if part:
                    raw = mail.literal(ref.uid, f"BODY.PEEK[{part['part']}]<0.1048576>")
                    value = decode_body(raw, part)
                    truncated = truncated or part["size"] > 1048576
                    if kind == "text/plain":
                        text = value
                    else:
                        rendered, blocked = sanitize(value, remote)
            mail.flags(ref.uid, True, "\\Seen")
            if not text and rendered:
                text = html.unescape(re.sub(r"<[^>]*>", " ", rendered))
            return {
                **headers,
                "ref": ref.model_dump(),
                "text": text,
                "html": rendered,
                "blocked_images": 0 if remote else blocked,
                "truncated": truncated,
                "attachments": [p for p in parts if p["attachment"]],
            }

    @staticmethod
    def special(mail: MailConnection, name: str) -> str:
        for folder in mail.folders():
            if folder["special"] == name or folder["name"].lower() == name:
                return str(folder["name"])
        mail.command("create", quote(name.title()))
        return name.title()

    def bulk(self, token: str, body: Bulk) -> dict[str, Any]:
        outcomes = []
        with self.auth.connect(token) as (user, mail):
            keyword = self.label(user, body.label) if body.action in {"label", "unlabel"} else ""
            for ref in body.messages:
                try:
                    mail.check(ref)
                    outcome = self._action(user, mail, ref, body, keyword)
                    outcomes.append({"ref": ref.model_dump(), "complete": True, **outcome})
                except ServiceError as error:
                    outcomes.append(
                        {"ref": ref.model_dump(), "complete": False, "error": error.code}
                    )
                    if error.code in {"mail_timeout", "mail_connection_lost"}:
                        break
        # Report every explicit message, including those not attempted after a deadline.
        outcomes.extend(
            {"ref": ref.model_dump(), "complete": False, "error": "not_attempted"}
            for ref in body.messages[len(outcomes) :]
        )
        return {"results": outcomes}

    def _action(
        self, user: Identity, mail: MailConnection, ref: MessageRef, body: Bulk, keyword: str
    ) -> dict[str, Any]:
        coordinator = self.auth.trash
        if coordinator is None or body.action in {"read", "unread", "label", "unlabel"}:
            return self._action_native(user, mail, ref, body, keyword)
        hold = coordinator.begin(user.id)
        result = self._action_native(user, mail, ref, body, keyword)
        if body.action == "delete":
            coordinator.enqueue(user.id, "delete", ref)
        else:
            coordinator.enqueue(user.id, "restored", ref)
            target = result.get("destination")
            if target is None:
                # Unknown destination UID needs explicit native reconciliation.
                return {**result, "warning": "trash_reconciliation_required"}
            destination = MessageRef(**target)
            with self.database.transaction() as connection:
                is_trash = connection.execute(
                    "SELECT 1 FROM mailbox_trash WHERE user_id=? AND folder=? "
                    "AND uidvalidity=? AND uid=?",
                    (user.id, destination.folder, destination.uidvalidity, destination.uid),
                ).fetchone()
            if is_trash:
                coordinator.enqueue(user.id, "entered", destination)
        coordinator.enqueue(user.id, "ready", hold=hold)
        coordinator.flush()
        return result

    def _action_native(
        self, user: Identity, mail: MailConnection, ref: MessageRef, body: Bulk, keyword: str
    ) -> dict[str, Any]:
        if body.action in {"read", "unread", "label", "unlabel"}:
            mail.flags(ref.uid, body.action in {"read", "label"}, keyword or "\\Seen")
            return {}
        trash = self.special(mail, "trash")
        if body.action == "delete":
            if ref.folder != trash or body.confirmation != "DELETE PERMANENTLY":
                raise ServiceError("permanent_deletion_confirmation_required", 400)
            if self.auth.trash is None and "UIDPLUS" in mail.raw.capabilities:
                mail.remove(ref)
                self._forget_trash(user, ref)
                return {}
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO mailbox_trash VALUES(?,?,?,?,?,NULL)",
                    (user.id, ref.folder, ref.uidvalidity, ref.uid, self.clock()),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO mailbox_permanent_deletions VALUES(?,?,?,?,?)",
                    (user.id, ref.folder, ref.uidvalidity, ref.uid, self.clock()),
                )
            if self.auth.trash is None:
                mail.flags(ref.uid, True, "\\Deleted")
            return {"warning": "permanent_deletion_queued_for_hourly_maintenance"}
        destination = body.destination
        if body.action == "trash":
            destination = trash
        if body.action == "restore":
            if ref.folder != trash:
                raise ServiceError("message_not_in_trash", 400)
            with self.database.transaction() as connection:
                previous = connection.execute(
                    "SELECT original_folder FROM mailbox_trash WHERE "
                    "user_id=? AND folder=? AND uidvalidity=? AND uid=?",
                    (user.id, ref.folder, ref.uidvalidity, ref.uid),
                ).fetchone()
            destination = (previous[0] if previous else None) or "INBOX"
        if not destination or destination == ref.folder:
            raise ServiceError("invalid_destination", 400)
        if destination not in {f["name"] for f in mail.folders()}:
            if body.action == "restore":
                destination = "INBOX"
            else:
                raise ServiceError("folder_not_found", 404)
        # Folder discovery does not change SELECT, but explicitly revalidate before mutation.
        mail.check(ref)
        if "MOVE" not in mail.raw.capabilities:
            raise ServiceError("imap_move_required", 503)
        target = mail.move(ref, destination)
        if ref.folder == trash:
            self._forget_trash(user, ref)
        if destination == trash and target:
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO mailbox_trash VALUES(?,?,?,?,?,?)",
                    (
                        user.id,
                        target.folder,
                        target.uidvalidity,
                        target.uid,
                        self.clock(),
                        ref.folder,
                    ),
                )
        # A missing COPYUID is a completed move with an unknown destination UID;
        # never repeat it. The maintenance observer will discover Trash entries.
        return {
            "destination": target.model_dump() if target else None,
            "warning": "" if target else "move_complete_refresh_required",
        }

    def discard_draft(self, user: Identity, mail: MailConnection, ref: MessageRef) -> None:
        if "UIDPLUS" in mail.raw.capabilities:
            mail.remove(ref)
            return
        # Maddy has no selective EXPUNGE. Keep superseded drafts recoverable in
        # Trash until the native maintenance worker removes them after 30 days.
        self._action(user, mail, ref, Bulk(messages=[ref], action="trash"), "")

    def _forget_trash(self, user: Identity, ref: MessageRef) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM mailbox_permanent_deletions WHERE user_id=? AND folder=? "
                "AND uidvalidity=? AND uid=?",
                (user.id, ref.folder, ref.uidvalidity, ref.uid),
            )
            connection.execute(
                "DELETE FROM mailbox_trash WHERE user_id=? AND folder=? "
                "AND uidvalidity=? AND uid=?",
                (user.id, ref.folder, ref.uidvalidity, ref.uid),
            )
