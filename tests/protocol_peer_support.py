"""A private Unix peer returns explicit incompatible responses without running writers."""

import json
import socketserver
import threading


class ProtocolHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(2)
        request = json.loads(self.rfile.readline(65537))
        self.server.peer.requests.append(request)
        response = {'ok': self.server.peer.ok, 'protocol': self.server.peer.protocol,
                    'credentials': [], 'storage': [], 'state': 'complete'}
        if self.server.peer.ok is False:
            response = {'ok': False, 'error': 'helper_access_denied', 'status': 403}
        self.wfile.write(json.dumps(response).encode() + b'\n')


class ProtocolPeer:
    def __init__(self, path):
        self.protocol, self.ok, self.requests = 1, True, []
        self.server = socketserver.ThreadingUnixStreamServer(str(path), ProtocolHandler)
        self.server.daemon_threads = True
        self.server.peer = self
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
