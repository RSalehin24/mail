"""Certificate-verified, UID-only IMAP access with a deadline for the whole operation."""

import base64
import imaplib
import re
import secrets
import ssl
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any

from backend.webmail_models import MessageRef
from shared.errors import ServiceError
from shared.installation import site
from shared.validation import utf8_size

MAX_MESSAGE = 16 * 1024 * 1024
MAX_ATTACHMENTS = 10 * 1024 * 1024
SYSTEM_FOLDERS = {"inbox", "sent", "drafts", "trash", "junk", "archive"}


def folder_name(value: str) -> str:
    if not value or utf8_size(value) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ServiceError("invalid_folder", 400)
    return value


def quote(value: str) -> str:
    if any(c in value for c in "\r\n\0"):
        raise ServiceError("invalid_imap_value", 400)
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def utf7(value: str) -> str:
    result, pending = "", ""
    for char in value + "\0":
        if 32 <= ord(char) <= 126 or char == "\0":
            if pending:
                result += (
                    "&"
                    + base64.b64encode(pending.encode("utf-16-be"))
                    .decode()
                    .rstrip("=")
                    .replace("/", ",")
                    + "-"
                )
                pending = ""
            if char != "\0":
                result += "&-" if char == "&" else char
        else:
            pending += char
    return result


def unutf7(value: str) -> str:
    def decode(match: re.Match[str]) -> str:
        data = match[1].replace(",", "/")
        return base64.b64decode(data + "=" * (-len(data) % 4)).decode("utf-16-be") if data else "&"

    try:
        return re.sub(r"&([^-]*)-", decode, value)
    except (ValueError, UnicodeError) as error:
        raise ServiceError("unsupported_folder_encoding", 502) from error


def sexpr(data: bytes) -> list[Any]:
    """Parse bounded IMAP lists, quoted strings, atoms and literals (BODYSTRUCTURE/LIST)."""
    if len(data) > 256 * 1024:
        raise ServiceError("imap_metadata_too_large", 502)
    position = 0

    def read(depth: int) -> Any:
        nonlocal position
        if depth > 25:
            raise ServiceError("mime_too_complex", 422)
        while position < len(data) and data[position] == 32:
            position += 1
        if position >= len(data):
            raise ServiceError("invalid_imap_response", 502)
        char = data[position]
        position += 1
        if char == 40:
            parts: list[Any] = []
            while position < len(data):
                if data[position] == 41:
                    position += 1
                    return parts
                if data[position] == 32:
                    position += 1
                else:
                    parts.append(read(depth + 1))
            raise ServiceError("invalid_imap_response", 502)
        if char == 34:
            result = bytearray()
            while position < len(data):
                char = data[position]
                position += 1
                if char == 34:
                    return result.decode("utf-8", "replace")
                if char == 92 and position < len(data):
                    char = data[position]
                    position += 1
                result.append(char)
            raise ServiceError("invalid_imap_response", 502)
        if char == 123:
            end = data.index(b"}\r\n", position)
            length = int(data[position:end])
            position = end + 3
            literal = data[position : position + length]
            position += length
            return literal.decode("utf-8", "replace")
        start = position - 1
        while position < len(data) and data[position] not in b" ()\r\n":
            position += 1
        atom = data[start:position].decode("ascii", "replace")
        return None if atom.upper() == "NIL" else atom

    values = []
    while position < len(data):
        if data[position] in b" \r\n":
            position += 1
        else:
            values.append(read(0))
    return values


class MailConnection:
    def __init__(self, connection: imaplib.IMAP4_SSL, deadline: float) -> None:
        self.raw, self.deadline = connection, deadline
        self.selected = ""
        self.validity = 0

    def command(self, name: str, *args: Any) -> list[Any]:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ServiceError("mail_timeout", 504)
        self.raw.sock.settimeout(remaining)
        try:
            status, result = getattr(self.raw, name)(*args)
        except TimeoutError as error:
            raise ServiceError("mail_timeout", 504) from error
        except (OSError, imaplib.IMAP4.abort) as error:
            raise ServiceError("mail_connection_lost", 503) from error
        except imaplib.IMAP4.error as error:
            raise ServiceError("imap_rejected", 409) from error
        if status != "OK":
            raise ServiceError("imap_rejected", 409)
        return list(result)

    def uid(self, command: str, *args: Any) -> list[Any]:
        return self.command("uid", command, *args)

    def select(self, folder: str, validity: int | None = None) -> int:
        self.command("select", quote(utf7(folder_name(folder))))
        code = self.raw.response("UIDVALIDITY")[1]
        current = int(code[0]) if code and code[0] else 0
        if current <= 0 or (validity is not None and validity != current):
            raise ServiceError("mailbox_changed_refresh", 409)
        self.selected, self.validity = folder, current
        return current

    def check(self, ref: MessageRef) -> None:
        self.select(ref.folder, ref.uidvalidity)
        result = self.uid("FETCH", str(ref.uid), "(UID)")
        if not any(
            isinstance(x, bytes) and re.search(rb"\bUID " + str(ref.uid).encode() + rb"\b", x)
            for x in result
        ):
            raise ServiceError("message_gone", 404)

    def flags(self, uid: int, add: bool, flags: str) -> None:
        self.uid("STORE", str(uid), "+FLAGS.SILENT" if add else "-FLAGS.SILENT", "(" + flags + ")")

    def remove(self, ref: MessageRef) -> None:
        self.check(ref)
        # EXPUNGE without an explicit UID can destroy unrelated client deletions.
        if "UIDPLUS" not in self.raw.capabilities:
            raise ServiceError("uid_expunge_required", 503)
        self.flags(ref.uid, True, "\\Deleted")
        self.uid("EXPUNGE", str(ref.uid))

    def find_keyword(self, folder: str, keyword: str) -> tuple[int, list[int]]:
        validity = self.select(folder)
        result = self.uid("SEARCH", None, "KEYWORD", keyword)
        return validity, [int(x) for x in (result[0] or b"").split()]

    def append(self, folder: str, message: bytes, flags: str) -> MessageRef:
        marker = secrets.token_hex(16)
        message = ("X-Webmail-Append-ID: " + marker + "\r\n").encode() + message
        self.command("append", quote(utf7(folder_name(folder))), "(" + flags + ")", None, message)
        code = self.raw.response("APPENDUID")[1]
        if not code or not code[0]:
            validity = self.select(folder)
            found = self.uid("SEARCH", None, "HEADER", "X-Webmail-Append-ID", quote(marker))
            uids = (found[0] or b"").split()
            if len(uids) != 1:
                raise ServiceError("append_result_uncertain", 409)
            return MessageRef(folder=folder, uidvalidity=validity, uid=int(uids[0]))
        validity, uid = code[0].decode().split()[:2]
        return MessageRef(folder=folder, uidvalidity=int(validity), uid=int(uid))

    def move(self, ref: MessageRef, destination: str) -> MessageRef | None:
        self.check(ref)
        marker = "wmmove_" + secrets.token_hex(16)
        self.flags(ref.uid, True, marker)
        self.uid("MOVE", str(ref.uid), quote(utf7(folder_name(destination))))
        # Maddy 0.9.6 does not return COPYUID. Keywords persist through MOVE.
        validity, found = self.find_keyword(destination, marker)
        if len(found) != 1:
            return None
        self.flags(found[0], False, marker)
        return MessageRef(folder=destination, uidvalidity=validity, uid=found[0])

    def literal(self, uid: int, query: str) -> bytes:
        result = self.uid("FETCH", str(uid), "(UID " + query + ")")
        for item in result:
            if isinstance(item, tuple) and isinstance(item[1], bytes):
                if not re.search(rb"\bUID " + str(uid).encode() + rb"\b", item[0]):
                    raise ServiceError("invalid_imap_response", 502)
                return item[1]
        raise ServiceError("message_gone", 404)

    def folders(self) -> list[dict[str, Any]]:
        folders = []
        for raw in self.command("list", '""', '"*"'):
            if raw is None:
                continue
            if isinstance(raw, tuple):
                raw = raw[0] + b"\r\n" + raw[1]
            parsed = sexpr(raw)
            if len(parsed) < 3 or not isinstance(parsed[0], list):
                raise ServiceError("invalid_imap_response", 502)
            flags = [x.lower() for x in parsed[0]]
            if "\\noselect" in flags:
                continue
            name = unutf7(parsed[2])
            folder_name(name)
            special = next((x[1:] for x in flags if x[1:] in SYSTEM_FOLDERS), "")
            if name.lower() == "inbox":
                special = "inbox"
            folders.append({"name": name, "special": special, "unread": 0})
        if len(folders) > 100:
            raise ServiceError("folder_limit_exceeded", 422)
        return sorted(folders, key=lambda f: (f["name"] != "INBOX", f["name"]))


class ImapTransport:
    def __init__(
        self, host: str | None = None, port: int = 993, context: ssl.SSLContext | None = None
    ) -> None:
        self.host, self.port = host or site().mail_hostname, port
        self.context = context or ssl.create_default_context()

    @contextmanager
    def connect(self, email: str, password: str) -> Iterator[MailConnection]:
        raw = None
        deadline = time.monotonic() + 9
        try:
            raw = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=self.context, timeout=9)
            raw.sock.settimeout(max(0.01, deadline - time.monotonic()))
            credentials = ("\0" + email + "\0" + password).encode()
            # PLAIN is permitted only inside this verified TLS connection.
            raw.authenticate("PLAIN", lambda _: credentials)
            password, credentials = "", b""
            yield MailConnection(raw, deadline)
        except TimeoutError as error:
            raise ServiceError("mail_timeout", 504) from error
        except imaplib.IMAP4.abort as error:
            raise ServiceError("mail_connection_lost", 503) from error
        except imaplib.IMAP4.error as error:
            raise ServiceError("mail_authentication_failed", 401) from error
        except (OSError, ValueError) as error:
            raise ServiceError("mail_unavailable", 503) from error
        finally:
            if raw is not None:
                # LOGOUT can block for another network timeout. Closing the TLS
                # socket needs no protocol roundtrip and never expunges messages.
                with suppress(OSError, imaplib.IMAP4.error):
                    raw.shutdown()
