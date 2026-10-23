"""Bounded off-loop work. Cancellation never releases capacity before work finishes."""

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from shared.errors import ServiceError

Result = TypeVar("Result")


class WorkPool:
    def __init__(self, concurrency: int, timeout: float) -> None:
        self.capacity = threading.BoundedSemaphore(concurrency)
        self.executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="mail-admin")
        self.timeout = timeout

    async def run(self, operation: Callable[[], Result]) -> Result:
        if not self.capacity.acquire(blocking=False):
            raise ServiceError("work_overloaded", 503, 1)
        try:
            work = self.executor.submit(operation)
        except RuntimeError as error:
            self.capacity.release()
            raise ServiceError("work_unavailable", 503) from error
        work.add_done_callback(lambda _: self.capacity.release())
        try:
            return await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(work)), self.timeout)
        except TimeoutError as error:
            raise ServiceError("operation_pending", 503) from error

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=False)
