"""Atomic mailbox state across storage, credentials, metadata, generation and ledger.

The mail writer must be stopped. Rollback journals and FULL synchronization are
required for SQLite's attached-database super-journal commit protocol. File bodies
are durably staged first; their operation-specific names are recorded before use.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from shared.errors import ServiceError


@contextmanager
def mailbox_transaction(paths: dict[str, Path]) -> Iterator[sqlite3.Connection]:
    if set(paths) != {'main', 'credentials', 'dashboard', 'incarnations', 'ledger'}:
        raise ServiceError('invalid_mailbox_transaction', 503)
    for path in paths.values():
        if path.is_symlink() or not path.is_file():
            raise ServiceError('mailbox_transaction_storage_missing', 503)
    connection = sqlite3.connect(paths['main'].as_uri() + '?mode=rw', uri=True, timeout=0.25)
    connection.row_factory = sqlite3.Row
    try:
        for name, path in paths.items():
            if name != 'main':
                connection.execute(f'ATTACH DATABASE ? AS {name}',
                                   (path.as_uri() + '?mode=rw',))
            # Fixed schema names above, no user-supplied SQL identifiers.
            try:
                mode = connection.execute(f'PRAGMA {name}.journal_mode=DELETE').fetchone()[0]
            except sqlite3.OperationalError as error:
                raise ServiceError('mailbox_atomic_journal_unavailable_' + name, 503) from error
            if mode.lower() != 'delete':
                raise ServiceError('mailbox_atomic_journal_required', 503)
            connection.execute(f'PRAGMA {name}.synchronous=FULL')
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN IMMEDIATE')
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
