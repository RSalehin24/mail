"""Bound real native SQLite writes without filling the fixture host's filesystem."""

import sqlite3


class ObservedConnection(sqlite3.Connection):
    def execute(self, *arguments, **keywords):
        try:
            return super().execute(*arguments, **keywords)
        except sqlite3.Error as error:
            self.observations.append(error.sqlite_errorcode)
            raise


class FaultedConnections:
    def __init__(self, path, fault):
        if fault not in {'full', 'read_only'}:
            raise ValueError('Unsupported isolated SQLite fault')
        self.path, self.fault = path, fault
        self.connect, self.errors = sqlite3.connect, []

    def __call__(self, *arguments, **keywords):
        if str(arguments[0]) not in {str(self.path), self.path.as_uri() + '?mode=rw'}:
            raise RuntimeError('Refusing a SQLite fault outside the selected fixture database')
        connection = self.connect(*arguments, factory=ObservedConnection, **keywords)
        connection.observations = self.errors
        if self.fault == 'read_only':
            connection.execute('PRAGMA query_only=ON')
        else:
            pages = connection.execute('PRAGMA page_count').fetchone()[0]
            connection.execute('PRAGMA max_page_count=' + str(pages))
        return connection
