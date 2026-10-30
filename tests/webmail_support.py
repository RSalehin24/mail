"""Deterministic authentication peer for security/API tests, with no network access."""

import dataclasses
from contextlib import contextmanager

from backend.webmail_services import MailServices
from shared.errors import ServiceError
from tests.api_support import ApiFixture
from tests.fixture_values import MAILBOX_PASSWORD


class IdentityImap:
    def __init__(self):
        self.accounts = {
            "first@rsalehin24.me": [MAILBOX_PASSWORD, 1234],
            "second@rsalehin24.me": [MAILBOX_PASSWORD, 5678],
        }

    @contextmanager
    def connect(self, email, password):
        if email not in self.accounts or self.accounts[email][0] != password:
            raise ServiceError("mail_authentication_failed", 401)
        yield IdentityConnection(self.accounts[email][1])


class IdentityConnection:
    def __init__(self, validity):
        self.validity = validity

    def select(self, folder, validity=None):
        if validity is not None and self.validity != validity:
            raise ServiceError("mailbox_changed_refresh", 409)
        return self.validity


class WebmailApiFixture(ApiFixture):
    def __init__(self):
        super().__init__()
        self.imap = IdentityImap()
        self.services.settings = dataclasses.replace(
            self.services.settings,
            webmail_enabled=True,
            webmail_allowed_mailboxes=tuple(self.imap.accounts),
        )
        self.services.webmail = MailServices(self.auth, self.imap)
        self.services.mailboxes.on_security_change = lambda: self.services.webmail.auth.cleanup(
            force=True
        )
        self.services.mailboxes.ownership = lambda email: self.services.webmail.auth.owner_guard(
            email, wait=True
        )
