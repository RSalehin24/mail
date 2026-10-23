"""Actual Linux peer identities over the restricted helper socket; payloads use stdin only."""

import json
import os
import subprocess
import sys
import threading

from helper.server import HelperServer

CLIENT = """import json,socket,sys
request=json.load(sys.stdin)
with socket.socket(socket.AF_UNIX) as connection:
 connection.settimeout(float(sys.argv[2]))
 connection.connect(sys.argv[1])
 try:
  connection.sendall(json.dumps(request).encode()+b'\\n')
  with connection.makefile('rb') as stream: response=stream.readline(131073)
  print(response.decode() if response else json.dumps({'ok':False,'error':'peer_disconnected'}))
 except (BrokenPipeError,ConnectionResetError):
  print(json.dumps({'ok':False,'error':'peer_disconnected'}))
"""


class SocketFixture:
    def __init__(self, root, dependencies):
        self.helper, self.policy = dependencies
        root.chmod(0o711)
        self.directory = root / 'socket'
        self.directory.mkdir(mode=0o750)
        os.chown(self.directory, 0, self.policy.service_gid)
        self.directory.chmod(0o750)
        self.path = self.directory / 'control.sock'
        self.start()

    def start(self):
        self.server = HelperServer(self.path, (self.helper, self.policy))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def client(self, request, identity=10001):
        result = subprocess.run(
            (sys.executable, '-c', CLIENT, str(self.path), str(self.policy.timeout + 1)),
            input=json.dumps(request),
            user=identity, group=self.policy.service_gid, extra_groups=(),
            capture_output=True, text=True, timeout=self.policy.timeout + 2, check=False,
        )
        if result.returncode:
            raise RuntimeError('Socket fixture client failed: ' + result.stderr[-1000:])
        return dict(json.loads(result.stdout))
