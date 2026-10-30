"""Verified Brevo SMTP transport shared by application delivery and independent host alerts."""

import smtplib
import ssl
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from shared.errors import ServiceError
from shared.installation import site


@dataclass(frozen=True)
class SmtpCredentials:
    host: str
    port: int
    username: str
    credential: str


@dataclass(frozen=True)
class OutboundMessage:
    sender: str
    subject: str
    body: str


@contextmanager
def authenticated(configuration: SmtpCredentials, timeout: float) -> Iterator[smtplib.SMTP]:
    if configuration.host != site().relay_host or type(configuration.port) is not int:
        raise ServiceError("invalid_relay_endpoint", 400)
    if configuration.port not in {465, 587}:
        raise ServiceError("invalid_relay_endpoint", 400)
    context = ssl.create_default_context()
    connection = (
        smtplib.SMTP_SSL(configuration.host, 465, timeout=timeout, context=context)
        if configuration.port == 465
        else smtplib.SMTP(configuration.host, 587, timeout=timeout)
    )
    with connection:
        if configuration.port == 587:
            connection.ehlo()
            connection.starttls(context=context)
            connection.ehlo()
        connection.login(configuration.username, configuration.credential)
        yield connection


def send_message(connection: smtplib.SMTP, message: OutboundMessage) -> None:
    envelope = EmailMessage()
    envelope["From"], envelope["To"] = message.sender, site().admin_email
    envelope["Subject"] = message.subject
    envelope["Date"] = formatdate(usegmt=True)
    envelope["Message-ID"] = make_msgid(domain=site().mail_hostname)
    envelope.set_content(message.body)
    rejected = connection.send_message(
        envelope, from_addr=message.sender, to_addrs=[site().admin_email]
    )
    if rejected:
        raise ServiceError("relay_recipient_rejected", 502)


def __getattr__(name: str) -> str:
    fields = {"SMTP_HOST": "relay_host"}
    if name in fields:
        return str(getattr(site(), fields[name]))
    raise AttributeError(name)
