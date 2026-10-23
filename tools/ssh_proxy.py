"""Carry SSH bytes through the exact local mock container's private loopback socket."""

import re
import shlex

STREAM_PROXY = """import os,select,socket,sys
def receive_output(connection):
 data=connection.recv(65536)
 if not data: return False
 sys.stdout.buffer.write(data)
 sys.stdout.buffer.flush()
 return True
def send_input(connection):
 data=os.read(sys.stdin.fileno(),65536)
 if data:
  connection.sendall(data)
  return True
 connection.shutdown(socket.SHUT_WR)
 return False
connection=socket.create_connection(('127.0.0.1',int(sys.argv[1])),timeout=10)
connection.settimeout(None)
channels=[connection,sys.stdin.buffer]
while channels:
 ready,_,_=select.select(channels,[],[])
 if connection in ready and not receive_output(connection): break
 if sys.stdin.buffer in ready and not send_input(connection):
  channels.remove(sys.stdin.buffer)
connection.close()
"""


def docker_ssh_proxy(identifier: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", identifier) is None:
        raise ValueError("An immutable local mock container ID is required")
    return shlex.join(("docker", "exec", "-i", identifier, "python", "-c", STREAM_PROXY, "2222"))
