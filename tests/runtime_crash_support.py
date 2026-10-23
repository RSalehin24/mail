"""Pause one real helper command while killing only its private HTTPS caller."""

import http.client
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from tests.runtime_support import RuntimeFixture


class DeletionEnabledRuntime(RuntimeFixture):
    def command(self, arguments):
        if arguments[0] == 'create':
            arguments = arguments[:-1] + ('-e', 'PERMANENT_DELETION_ENABLED=true', arguments[-1])
        return super().command(arguments)


class CommandBarrier:
    def __init__(self, execute, command):
        self.execute, self.command = execute, command
        self.reached, self.release = threading.Event(), threading.Event()
        self.arguments = []

    def __call__(self, arguments, password=''):
        self.arguments.append(arguments)
        if arguments[:2] == self.command:
            self.reached.set()
            if not self.release.wait(10):
                raise RuntimeError('The private mutation barrier was not released')
        return self.execute(arguments, password)


def kill_pending_api(assertions, web, operation):
    barrier, request = operation
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(web.api, *request)
        try:
            assertions.assertTrue(barrier.reached.wait(10))
            assertions.assertFalse(future.done())
            web.command(('kill', '--signal', 'KILL', web.identifier))
            state = web.exited()
            assertions.assertEqual(state['ExitCode'], 137)
            assertions.assertFalse(state['OOMKilled'])
        finally:
            barrier.release.set()
        with assertions.assertRaises((OSError, http.client.HTTPException)):
            future.result(timeout=10)


def wait_for_complete(journal, identifier):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        record = journal.lookup(identifier)
        if record and record['state'] != 'pending':
            return record
        time.sleep(0.1)
    raise RuntimeError('The retained helper operation did not complete')
