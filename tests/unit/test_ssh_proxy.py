import shlex
import socket
import struct
import subprocess
import sys
import threading
import unittest

from tools.ssh_proxy import STREAM_PROXY, docker_ssh_proxy


class SshProxyTests(unittest.TestCase):
    def listener(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(server.close)
        server.bind(("127.0.0.1", 0))
        server.settimeout(3)
        return server

    def test_binary_stream_and_input_eof_survive_the_private_socket_proxy(self):
        server = self.listener()
        server.listen(1)
        payload, received, failures = bytes(range(256)) * 1024, bytearray(), []

        def echo():
            try:
                with server.accept()[0] as peer:
                    peer.settimeout(3)
                    peer.sendall(b"fixture banner\n")
                    self.receive(peer, received)
                    peer.sendall(b"echo:" + received)
            except OSError as error:
                failures.append(error)

        worker = threading.Thread(target=echo, daemon=True)
        worker.start()
        result = subprocess.run((sys.executable, "-c", STREAM_PROXY,
                                 str(server.getsockname()[1])), input=payload,
                                capture_output=True, check=False, timeout=5)
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(received, payload)
        self.assertEqual(result.stdout, b"fixture banner\necho:" + payload)

    def receive(self, peer, received):
        while data := peer.recv(65536):
            received.extend(data)

    def test_peer_reset_is_an_explicit_failure_with_no_fallback_connection(self):
        server = self.listener()
        server.listen(1)

        def reset():
            with server.accept()[0] as peer:
                peer.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))

        worker = threading.Thread(target=reset, daemon=True)
        worker.start()
        result = subprocess.run((sys.executable, "-c", STREAM_PROXY,
                                 str(server.getsockname()[1])), input=b"fixture",
                                capture_output=True, check=False, timeout=5)
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertTrue(any(name in result.stderr for name in (
            b"ConnectionResetError", b"BrokenPipeError",
        )), result.stderr)

    def test_proxy_command_uses_an_exact_container_id_and_fixed_private_port(self):
        identifier = "a" * 64
        arguments = shlex.split(docker_ssh_proxy(identifier))
        self.assertEqual(arguments[:6], ["docker", "exec", "-i", identifier, "python", "-c"])
        self.assertEqual(arguments[-1], "2222")
        for invalid in ("maddy", "a" * 63, identifier + ";id", "../another", "$(id)"):
            with self.subTest(identifier=invalid), self.assertRaises(ValueError):
                docker_ssh_proxy(invalid)
