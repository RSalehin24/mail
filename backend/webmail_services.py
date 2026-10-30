"""Composition for optional webmail, isolated from the administrator I/O pool."""

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

from backend.auth import Authentication
from backend.webmail import Webmail
from backend.webmail_auth import MailboxAuthentication
from backend.webmail_files import MailFiles
from backend.webmail_imap import ImapTransport
from backend.webmail_pool import MailPool
from backend.webmail_schema import require_schema
from backend.webmail_sending import MailSending
from backend.work import WorkPool
from shared.errors import ServiceError

T = TypeVar("T")


class MailServices:
    def __init__(
        self,
        admin: Authentication,
        imap: ImapTransport | None = None,
        authority: Any = None,
        attachment_capacity_mib: int = 32,
        independent_io: bool = False,
    ) -> None:
        require_schema(admin.database)
        self.auth = MailboxAuthentication(admin, imap or ImapTransport(), authority)
        self.mail = Webmail(self.auth)
        self.files = MailFiles(self.auth, attachment_capacity_mib)
        self.sending = MailSending(self.mail, self.files)
        self.pool = MailPool(admission_timeout=5 if independent_io else 2)
        self.transfers = (
            MailPool(2, admission_timeout=15, operation_timeout=15, max_waiting=10)
            if independent_io
            else self.pool
        )
        self.composition = WorkPool(1, 30) if independent_io else self.pool
        self.compose_timeout = 30 if independent_io else 10
        self.compose_lock = asyncio.Lock()
        self.compose_waiting = 0

    async def compose(self, operation: Callable[[], T]) -> T:
        if self.compose_waiting >= 10:
            raise ServiceError("compose_busy", 503, 2)
        self.compose_waiting += 1
        try:
            try:
                await asyncio.wait_for(self.compose_lock.acquire(), self.compose_timeout)
            except TimeoutError as error:
                raise ServiceError("compose_busy", 503, 2) from error
        finally:
            self.compose_waiting -= 1
        try:
            return await self.composition.run(operation)
        finally:
            self.compose_lock.release()

    def cleanup(self) -> None:
        self.files.cleanup()

    def close(self) -> None:
        self.pool.close()
        if self.transfers is not self.pool:
            self.transfers.close()
        if self.composition is not self.pool:
            self.composition.close()
        self.files.close()
        self.auth.close()
