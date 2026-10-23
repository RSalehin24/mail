"""Bounded loopback TCP/UDP peers receive data but deliberately never answer."""

import socketserver
import threading


class SilentHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.server.received.set()
        if not self.server.release.wait(10):
            raise RuntimeError('Silent network fixture was not released')


class SilentPeer:
    def __init__(self, transport, address=None):
        server = {'tcp': socketserver.ThreadingTCPServer,
                  'udp': socketserver.ThreadingUDPServer,
                  'unix': socketserver.ThreadingUnixStreamServer}[transport]
        self.received, self.release = threading.Event(), threading.Event()
        self.server = server(address or ('127.0.0.1', 0), SilentHandler)
        self.server.daemon_threads = True
        self.server.received, self.server.release = self.received, self.release
        self.address = self.server.server_address
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True)
        self.thread.start()

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
