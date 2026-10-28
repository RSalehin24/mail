"""Provider synchronization cannot modify its SQLite state during snapshot capture."""

import os
import tempfile
import threading
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

from host.backup import BackupPaths, CaptureDependencies, SnapshotCapture
from host.locks import HostLock, LockPolicy


class ForwardingBackupTests(unittest.TestCase):
    def test_capture_waits_for_provider_writer_and_copies_its_committed_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / 'helper'
            staging = root / 'staging'
            helper.mkdir()
            staging.mkdir()
            lock = helper / 'forwarding-sync.lock'
            lock.touch(mode=0o600)
            (helper / 'state').write_text('before provider update')
            ready = threading.Event()
            committed = threading.Event()

            def writer():
                with HostLock(lock, LockPolicy(1, os.getuid())):
                    ready.set()
                    time.sleep(.1)
                    (helper / 'state').write_text('committed provider update')
                committed.set()

            thread = threading.Thread(target=writer)
            thread.start()
            self.assertTrue(ready.wait(1))

            def pause(_):
                self.assertTrue(committed.is_set())

            services = SimpleNamespace(
                inventories=lambda: {'credentials': [], 'storage': []},
                running=lambda: [], pause=pause, resume=lambda _: None)
            scope = SimpleNamespace(consistent=nullcontext, journal=None)
            capture = SnapshotCapture(BackupPaths(staging, root / 'manifest', {'helper': helper}),
                                      CaptureDependencies(scope, services, time.time))
            snapshot = capture.capture()
            thread.join(1)
            self.assertEqual((snapshot / 'helper/state').read_text(), 'committed provider update')
