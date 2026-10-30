"""Dedicated bounded mail queue; cancelled requests retain capacity until I/O exits."""

import asyncio
from collections.abc import Callable
from typing import TypeVar

from backend.work import WorkPool
from shared.errors import ServiceError

T = TypeVar("T")


class MailPool:
    def __init__(
        self,
        concurrency: int = 3,
        admission_timeout: float = 2,
        operation_timeout: float = 12,
        max_waiting: int = 9,
    ) -> None:
        self.pool = WorkPool(concurrency, operation_timeout)
        self.admission = asyncio.Semaphore(concurrency)
        self.admission_timeout = admission_timeout
        self.max_waiting = max_waiting
        self.waiting = 0

    async def run(self, operation: Callable[[], T]) -> T:
        if self.waiting >= self.max_waiting:
            raise ServiceError("webmail_busy", 503, 2)
        self.waiting += 1
        try:
            try:
                await asyncio.wait_for(self.admission.acquire(), self.admission_timeout)
            except TimeoutError as error:
                raise ServiceError("webmail_busy", 503, 2) from error
        finally:
            self.waiting -= 1
        try:
            return await self.pool.run(operation)
        finally:
            self.admission.release()

    def close(self) -> None:
        self.pool.close()
