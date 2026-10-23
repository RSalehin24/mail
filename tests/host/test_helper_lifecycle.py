import os
import stat
import unittest

from tests.host_support import HELPER_UNIT, SOCKET, SOCKET_DIRECTORY, GuestHelper


class HelperLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.host = GuestHelper.load()
        self.host.command(("systemctl", "start", HELPER_UNIT))
        self.host.ready()
        self.before = self.host.outcome()
        self.credentials = self.host.credentials()
        self.parent_inode = SOCKET_DIRECTORY.stat().st_ino
        self.assertEqual(self.host.consumer_inode(), self.parent_inode)

    def assert_preserved(self):
        self.host.ready()
        self.assertEqual(SOCKET_DIRECTORY.stat().st_ino, self.parent_inode)
        self.assertEqual(self.host.consumer_inode(), self.parent_inode)
        self.assertEqual(self.host.outcome(), self.before)
        response = self.host.retry_completed()
        self.assertTrue(response["accepted"])
        self.assertEqual(response["result"]["state"], "complete")
        self.assertEqual(self.host.credentials(), self.credentials)
        metadata = SOCKET.stat()
        self.assertEqual((stat.S_IMODE(metadata.st_mode), metadata.st_uid, metadata.st_gid),
                         (0o660, 0, 10001))

    def test_helper_restart_replaces_only_the_socket_and_preserves_the_bound_directory(self):
        inode, process = SOCKET.stat().st_ino, self.host.helper_pid()
        held = os.open(SOCKET, os.O_PATH | os.O_NOFOLLOW)
        try:
            self.host.command(("systemctl", "restart", HELPER_UNIT))
            self.assert_preserved()
            self.assertNotEqual(SOCKET.stat().st_ino, inode)
        finally:
            os.close(held)
        self.assertNotEqual(self.host.helper_pid(), process)
        denied = self.host.call({"operation": "outcome",
                                 "request_id": self.host.record["request_id"]}, "65534:10001")
        self.assertFalse(denied["accepted"])
        self.assertIn(denied["error"], {"helper_access_denied", "helper_unavailable"})

    def test_docker_restart_reconnects_without_repeating_a_completed_mutation(self):
        self.host.command(("systemctl", "restart", "docker.service"))
        self.host.verify_consumer()
        self.assert_preserved()
