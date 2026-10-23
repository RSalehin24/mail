import json
import socket
import time
import unittest
import uuid

from helper.server import HelperServer, SocketPolicy
from shared.errors import ServiceError
from tests.helper_support import HelperFixture
from tests.socket_support import SocketFixture


class LinuxBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = HelperFixture()
        self.addCleanup(self.fixture.close)
        self.socket = SocketFixture(self.fixture.root,
                                    (self.fixture.helper, SocketPolicy(10001, 10001, 1)))
        self.directory, self.path = self.socket.directory, self.socket.path
        self.addCleanup(self.socket.close)

    def client(self, identity):
        request = {'protocol': 1, 'operation': 'status', 'request_id': str(uuid.uuid4())}
        return self.socket.client(request, identity)

    def test_service_uid_allowed_and_other_group_member_denied_by_peer_credentials(self):
        self.assertTrue(self.client(10001)["ok"])
        denied = self.client(65534)
        self.assertFalse(denied["ok"])
        # Rejecting peer credentials before reading can reset an already-writing peer.
        self.assertIn(denied["error"], {"helper_access_denied", "peer_disconnected"})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o660)
        self.assertEqual(self.path.stat().st_gid, 10001)

    def test_restart_preserves_parent_inode_and_rejects_symlink_parent(self):
        inode = self.directory.stat().st_ino
        self.socket.close()
        self.socket.start()
        self.assertEqual(self.directory.stat().st_ino, inode)
        self.assertTrue(self.client(10001)["ok"])
        link = self.fixture.root / "socket-link"
        link.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(ServiceError):
            HelperServer(link / "other.sock", (self.fixture.helper, SocketPolicy(10001, 10001, 1)))

    def test_oversized_request_fails_before_any_cli_work(self):
        with socket.socket(socket.AF_UNIX) as connection:
            connection.connect(str(self.path))
            connection.sendall(b"x" * 8193 + b"\n")
            result = json.loads(connection.recv(65536))
        self.assertEqual(result["error"], "helper_request_limit")
        self.assertEqual(self.fixture.maddy.calls, [])

    def test_partial_request_times_out_without_work_and_the_worker_recovers(self):
        started = time.monotonic()
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(3)
            connection.connect(str(self.path))
            connection.sendall(b'{')
            result = json.loads(connection.recv(65536))
        self.assertEqual((result['status'], result['error']), (503, 'helper_unavailable'))
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(self.fixture.maddy.calls, [])
        self.assertTrue(self.client(10001)['ok'])

    def test_four_real_idle_connections_fill_the_queue_and_excess_is_refused(self):
        connections = []
        try:
            for _ in range(4):
                connection = socket.socket(socket.AF_UNIX)
                connection.connect(str(self.path))
                connections.append(connection)
            self.wait_for_full_capacity()
            with socket.socket(socket.AF_UNIX) as extra:
                extra.settimeout(3)
                extra.connect(str(self.path))
                result = json.loads(extra.recv(65536))
            self.assertEqual((result['status'], result['error']), (503, 'helper_overloaded'))
            self.assertEqual(self.fixture.maddy.calls, [])
        finally:
            for connection in connections:
                connection.close()
        self.assertTrue(self.client(10001)['ok'])

    def wait_for_full_capacity(self):
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            if not self.socket.server.capacity.acquire(blocking=False):
                return
            self.socket.server.capacity.release()
            time.sleep(0.005)
        self.fail('Four real handler threads did not reserve their queue slots')
