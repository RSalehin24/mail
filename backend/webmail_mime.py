"""Bounded MIME parsing and conservative HTML rendering for a sandboxed frame."""

import base64
import binascii
import email.policy
import html
import quopri
import re
from email.header import decode_header, make_header
from email.headerregistry import Address
from email.parser import BytesHeaderParser
from email.utils import collapse_rfc2231_value, decode_params, getaddresses
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from backend.webmail_imap import MailConnection, sexpr
from shared.errors import ServiceError
from shared.validation import utf8_size


def header(value: str) -> str:
    utf8_size(value)
    if any(ord(c) < 32 and c != "\t" for c in value) or "\x7f" in value:
        raise ServiceError("invalid_header", 400)
    return value


def email_address(value: str) -> str:
    header(value)
    try:
        parsed = Address(addr_spec=value)
        if not parsed.username or not parsed.domain or "." not in parsed.domain:
            raise ValueError("incomplete address")
        value.encode("ascii")
    except (ValueError, IndexError, UnicodeError) as error:
        raise ServiceError("invalid_recipient", 400) from error
    return value


def recipients(value: str) -> list[str]:
    if not value.strip():
        return []
    header(value)
    try:
        result = [email_address(address) for _, address in getaddresses([value], strict=True)]
    except (ValueError, TypeError) as error:
        raise ServiceError("invalid_recipient", 400) from error
    if not result or len(result) > 100:
        raise ServiceError("invalid_recipient", 400)
    return result


def metadata(raw: bytes) -> dict[str, str]:
    message = BytesHeaderParser(policy=email.policy.default).parsebytes(raw[:65536])
    return {
        key.replace("-", "_"): str(message.get(key, ""))[:4096]
        for key in (
            "from",
            "to",
            "cc",
            "bcc",
            "reply-to",
            "subject",
            "date",
            "message-id",
            "in-reply-to",
            "references",
        )
    }


def safe_filename(value: str) -> str:
    try:
        value = str(make_header(decode_header(value)))
    except (LookupError, UnicodeError, ValueError):
        pass
    value = re.sub(r"[\x00-\x1f\x7f/\\]", "_", value).strip(" .")[:180]
    return value or "attachment"


def _params(value: Any) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    parameters = [(str(value[i]).lower(), str(value[i + 1])) for i in range(0, len(value) - 1, 2)]
    try:
        return {
            key: collapse_rfc2231_value(item)
            for key, item in decode_params([("", ""), *parameters])[1:]
        }
    except (ValueError, LookupError):
        return dict(parameters)


def structure(mail: MailConnection, uid: int) -> list[dict[str, Any]]:
    data = mail.uid("FETCH", str(uid), "(UID BODYSTRUCTURE)")
    raw = next((x for x in data if isinstance(x, bytes)), b"")
    tokens = sexpr(raw)
    if len(tokens) < 2 or not isinstance(tokens[1], list):
        raise ServiceError("invalid_mime_structure", 502)
    items = tokens[1]
    try:
        root = items[items.index("BODYSTRUCTURE") + 1]
    except (ValueError, IndexError) as error:
        raise ServiceError("invalid_mime_structure", 502) from error
    parts: list[dict[str, Any]] = []

    def walk(node: Any, prefix: str, depth: int = 0) -> None:
        if not isinstance(node, list) or len(parts) > 100 or depth > 15:
            raise ServiceError("mime_too_complex", 422)
        if node and isinstance(node[0], list):
            children = []
            for child in node:
                if not isinstance(child, list):
                    break
                children.append(child)
            # Display only the final supported alternative, preferably text/plain.
            for number, child in enumerate(children, 1):
                walk(child, f"{prefix}.{number}" if prefix else str(number), depth + 1)
            return
        if len(node) < 7:
            raise ServiceError("invalid_mime_structure", 502)
        major, minor = str(node[0]).lower(), str(node[1]).lower()
        content_type = major + "/" + minor
        parameters = _params(node[2])
        offset = 9 if content_type == "message/rfc822" else 7 if major == "text" else 6
        disposition = node[offset + 2] if len(node) > offset + 2 else None
        disposition_params = (
            _params(disposition[1])
            if isinstance(disposition, list) and len(disposition) > 1
            else {}
        )
        filename = disposition_params.get("filename", parameters.get("name", ""))
        is_attachment = bool(filename) or (
            isinstance(disposition, list)
            and disposition
            and str(disposition[0]).lower() == "attachment"
        )
        try:
            size = int(node[6])
        except (ValueError, TypeError) as error:
            raise ServiceError("invalid_mime_structure", 502) from error
        parts.append(
            {
                "part": prefix or "1",
                "content_type": content_type,
                "charset": parameters.get("charset", "utf-8"),
                "encoding": str(node[5]).lower(),
                "size": size,
                "filename": safe_filename(filename) if filename else "attachment",
                "attachment": is_attachment or content_type not in ("text/plain", "text/html"),
            }
        )

    walk(root, "")
    return parts


def decode_body(raw: bytes, part: dict[str, Any]) -> str:
    try:
        if part["encoding"] == "base64":
            raw = base64.b64decode(
                re.sub(rb"\s", b"", raw)[: len(re.sub(rb"\s", b"", raw)) // 4 * 4]
            )
        elif part["encoding"] == "quoted-printable":
            raw = quopri.decodestring(raw)
        return raw.decode(part["charset"], "replace")
    except (ValueError, LookupError, binascii.Error):
        return raw.decode("utf-8", "replace")


class HTMLLimit(Exception):
    pass


class SafeHTML(HTMLParser):
    allowed = {
        "p",
        "br",
        "div",
        "span",
        "b",
        "strong",
        "i",
        "em",
        "u",
        "s",
        "blockquote",
        "pre",
        "code",
        "ul",
        "ol",
        "li",
        "table",
        "thead",
        "tbody",
        "tr",
        "td",
        "th",
        "hr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "a",
        "img",
    }
    hidden = {
        "script",
        "style",
        "iframe",
        "object",
        "embed",
        "svg",
        "math",
        "template",
        "form",
        "input",
        "button",
        "textarea",
        "select",
        "video",
        "audio",
        "link",
        "meta",
    }

    def __init__(self, remote: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.remote, self.skip, self.images = remote, 0, 0
        self.output: list[str] = []
        self.output_size = 0

    def emit(self, value: str) -> None:
        self.output_size += len(value)
        if len(self.output) >= 5000 or self.output_size > 262144:
            raise HTMLLimit
        self.output.append(value)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.hidden:
            if tag not in {"input", "embed", "link", "meta"}:
                self.skip += 1
            return
        if self.skip or tag not in self.allowed:
            return
        values = dict(attrs)
        attributes = ""
        if tag == "a":
            url = values.get("href") or ""
            if urlsplit(url).scheme.lower() in {"https", "http", "mailto"}:
                attributes = (
                    ' href="'
                    + html.escape(url, quote=True)
                    + '" target="_blank" rel="noopener noreferrer"'
                )
        if tag == "img":
            url = values.get("src") or ""
            if urlsplit(url).scheme.lower() not in {"https", "http"}:
                return
            self.images += 1
            if not self.remote:
                self.emit("[remote image blocked]")
                return
            attributes = ' src="' + html.escape(url, quote=True) + '" referrerpolicy="no-referrer"'
        self.emit("<" + tag + attributes + ">")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.hidden:
            if self.skip:
                self.skip -= 1
            return
        if not self.skip and tag in self.allowed and tag not in {"br", "hr", "img"}:
            self.emit("</" + tag + ">")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            if len(data) > 65536:
                raise HTMLLimit
            self.emit(html.escape(data))


def sanitize(value: str, remote: bool = False) -> tuple[str, int]:
    parser = SafeHTML(remote)
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, RecursionError, HTMLLimit):
        parser.output = [
            "<p>Complex HTML was simplified to a limited text preview.</p><pre>",
            html.escape(value[:65536]),
            "</pre>",
        ]
        parser.images = 0
    images = "https: http:" if remote else "'none'"
    policy = (
        f"default-src 'none'; img-src {images}; style-src 'none'; "
        "base-uri 'none'; form-action 'none'"
    )
    document = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="'
    )
    return document + html.escape(policy, quote=True) + '"></head><body>' + "".join(
        parser.output
    ) + "</body></html>", parser.images
