"""Connect only to the disposable guest's loopback; verify its actual mail hostname."""

import http.client
import imaplib
import socket
import ssl
from pathlib import Path

from shared.files import protected_bytes
from shared.validation import MAIL_HOST


def recovery_context():
    trust = Path('/etc/mail-admin/fixture-ca.pem')
    if not trust.exists():
        trust = Path('/opt/maddy/tls/fixture/fullchain.pem')
    return ssl.create_default_context(cadata=protected_bytes(trust).decode())


def connect_tls(context, port, timeout):
    connection = socket.create_connection(('127.0.0.1', port), timeout=timeout)
    try:
        return context.wrap_socket(connection, server_hostname=MAIL_HOST)
    except BaseException:
        connection.close()
        raise


class LoopbackHttps(http.client.HTTPSConnection):
    def __init__(self, context, timeout):
        super().__init__(MAIL_HOST, 443, context=context, timeout=timeout)

    def connect(self):
        self.sock = connect_tls(self._context, self.port, self.timeout)


class LoopbackImap(imaplib.IMAP4_SSL):
    def __init__(self, context, timeout):
        super().__init__(MAIL_HOST, 993, ssl_context=context, timeout=timeout)

    def _create_socket(self, timeout):
        return connect_tls(self.ssl_context, self.port, timeout)
