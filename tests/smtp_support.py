"""Loopback SMTP peer exercising real TLS/authentication without external delivery."""

import base64
import os
import socket
import socketserver
import ssl
import threading
from contextlib import ExitStack
from unittest.mock import patch

from tests.tls_support import CertificateFixture


class SmtpHandler(socketserver.StreamRequestHandler):
    def finish(self):
        try:
            super().finish()
        finally:
            self.connection.close()

    def handle(self):
        self.secured = False
        self.authorized = False
        try:
            if self.server.fixture.implicit_tls:
                self.secure()
            self.reply(b"220 isolated fixture")
            self.conversation()
        except (OSError, ssl.SSLError):
            # Failed TLS/connection tests deliberately close the loopback peer.
            self.server.fixture.disconnects += 1

    def reply(self, line):
        self.wfile.write(line + b"\r\n")
        self.wfile.flush()

    def secure(self):
        self.rfile.close()
        self.wfile.close()
        self.connection = self.server.fixture.context.wrap_socket(
            self.connection, server_side=True
        )
        self.rfile = self.connection.makefile("rb")
        self.wfile = self.connection.makefile("wb")
        self.secured = True

    def conversation(self):
        handlers = {
            b"EHLO": self.hello,
            b"STARTTLS": self.starttls,
            b"AUTH": self.authenticate,
            b"MAIL": self.sender,
            b"RCPT": self.recipient,
            b"DATA": self.data,
            b"RSET": lambda _: self.reply(b"250 reset"),
        }
        while line := self.rfile.readline(8193):
            verb, _, argument = line.rstrip(b"\r\n").partition(b" ")
            verb = verb.upper()
            if verb == b"QUIT":
                self.reply(b"221 goodbye")
                return
            handlers.get(verb, lambda _: self.reply(b"500 unsupported"))(argument)

    def hello(self, argument):
        self.reply(b"250-isolated fixture")
        self.reply(b"250 AUTH PLAIN" if self.secured else b"250 STARTTLS")

    def starttls(self, argument):
        self.reply(b"220 begin TLS")
        self.secure()

    def authenticate(self, argument):
        fixture = self.server.fixture
        fixture.events.append(("authentication", self.secured))
        expected = b"PLAIN " + base64.b64encode(b"\0synthetic\0fixture-secret")
        self.authorized = self.secured and argument == expected and not fixture.reject_auth
        self.reply(b"235 authenticated" if self.authorized else b"535 rejected")

    def sender(self, argument):
        fixture = self.server.fixture
        fixture.events.append(("sender", argument.decode()))
        accepted = self.authorized and not fixture.reject_sender
        self.reply(b"250 sender accepted" if accepted else b"550 sender rejected")

    def recipient(self, argument):
        self.server.fixture.events.append(("recipient", argument.decode()))
        self.reply(b"250 recipient accepted" if self.authorized else b"530 authentication required")

    def data(self, argument):
        self.reply(b"354 send message")
        body = bytearray()
        while line := self.rfile.readline(65537):
            if line == b".\r\n":
                break
            body.extend(line[1:] if line.startswith(b"..") else line)
            if len(body) > 65536:
                raise OSError("Synthetic message exceeded fixture bound")
        fixture = self.server.fixture
        if fixture.reject_data:
            self.reply(b"554 send rejected")
        else:
            fixture.messages.append(bytes(body))
            self.reply(b"250 stored in isolated fixture")


class SmtpFixture:
    def __init__(self, root, certificate=None, implicit_tls=False):
        root.mkdir(mode=0o700)
        self.certificates = CertificateFixture(root)
        pair = self.certificates.issue(hostname="smtp-relay.brevo.com")
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(*(pair / name for name in ("fullchain.pem", "privkey.pem")))
        if certificate is not None:
            self.context.load_cert_chain(
                *(certificate / name for name in ("fullchain.pem", "privkey.pem"))
            )
        self.implicit_tls = implicit_tls
        self.reject_auth = self.reject_sender = self.reject_data = False
        self.events, self.messages = [], []
        self.disconnects = 0
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SmtpHandler)
        self.server.daemon_threads = True
        self.server.fixture = self
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True
        )
        self.thread.start()
        connect = socket.create_connection

        def loopback(endpoint, *arguments, **keywords):
            if endpoint != ("smtp-relay.brevo.com", 465 if implicit_tls else 587):
                raise RuntimeError("Refusing any network destination outside this SMTP fixture")
            return connect(self.server.server_address, *arguments, **keywords)

        self.boundary = ExitStack()
        self.boundary.enter_context(patch("socket.create_connection", side_effect=loopback))
        self.boundary.enter_context(
            patch.dict(os.environ, {"SSL_CERT_FILE": str(self.certificates.trust)})
        )

    def close(self):
        self.boundary.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
