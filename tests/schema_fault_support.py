"""Kill real SQLite provisioning before COMMIT, retaining the compatible fixture file."""

import os
import signal
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

PROVISION = """import os,sqlite3,sys,time
from pathlib import Path
from unittest.mock import patch
from backend.database import Database
connect=sqlite3.connect
def trace(statement):
 if statement.strip().rstrip(';').upper()=='COMMIT':
  descriptor=os.open(sys.argv[2],os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
  os.write(descriptor,b'before-commit');os.fsync(descriptor);os.close(descriptor)
  time.sleep(60)
def observed(*arguments,**keywords):
 connection=connect(*arguments,**keywords);connection.set_trace_callback(trace)
 return connection
with patch('backend.database.sqlite3.connect',side_effect=observed):
 Database.provision(Path(sys.argv[1]),sys.argv[3])
"""


def stop_before_commit(arguments):
    process = subprocess.Popen((sys.executable, '-c', PROVISION, *arguments),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not Path(arguments[1]).exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('Provisioning exited before its real COMMIT barrier')
            time.sleep(0.01)
        if not Path(arguments[1]).exists():
            raise RuntimeError('Provisioning did not reach its COMMIT barrier')
        process.kill()
        process.communicate(timeout=5)
        if process.returncode != -signal.SIGKILL:
            raise RuntimeError('The provisioning process was not actually killed')
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


@contextmanager
def interrupted_schema(database):
    path = database.path
    identifier = str(uuid.uuid4())
    retained = path.with_name(path.name + '.compatible-' + identifier)
    barrier = path.with_name('.commit-barrier-' + identifier)
    metadata = path.stat()
    path.rename(retained)
    try:
        stop_before_commit((str(path), str(barrier), database.identity))
        for candidate in (path, Path(str(path) + '-journal')):
            if candidate.exists():
                os.chown(candidate, metadata.st_uid, metadata.st_gid)
                candidate.chmod(0o600)
        yield
    finally:
        for candidate in (path, Path(str(path) + '-journal')):
            if candidate.exists():
                candidate.rename(candidate.with_name(candidate.name + '.interrupted-' + identifier))
        retained.rename(path)
