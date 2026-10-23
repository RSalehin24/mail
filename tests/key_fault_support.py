"""Temporary faults preserve each original synthetic key for explicit recovery."""

import uuid
from contextlib import contextmanager


@contextmanager
def missing_key(path):
    retained = path.with_name(path.name + '.retained-' + str(uuid.uuid4()))
    path.rename(retained)
    try:
        yield 'FileNotFoundError'
    finally:
        retained.rename(path)


@contextmanager
def wrong_key(path):
    original = path.read_bytes()
    path.write_bytes(bytes(value ^ 0xff for value in original))
    try:
        yield 'key_identity_mismatch'
    finally:
        path.write_bytes(original)


@contextmanager
def unreadable_key(path):
    original = path.stat().st_mode & 0o777
    path.chmod(0o600)
    try:
        yield 'PermissionError'
    finally:
        path.chmod(original)
