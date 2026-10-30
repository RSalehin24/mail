"""Private lab DNS and authenticated TLS relay; delivery is restricted to synthetic mail."""

# Listeners are inside the private, unpublished simulation Docker network.
# ruff: noqa: S104

import base64
import json
import re
import smtplib
import socket
import socketserver
import ssl
import struct
import threading
from pathlib import Path

from backend.dns import dns_name

ROOT = Path("/fixture")
CONFIG = json.loads((ROOT / "provider.json").read_text())


def wire(name):
    return b"".join(bytes([len(label)]) + label.encode() for label in name.split(".")) + b"\0"


class DNS(socketserver.BaseRequestHandler):
    def handle(self):
        packet, connection = self.request
        try:
            name, end = dns_name(packet, 12)
            kind, category = struct.unpack_from("!HH", packet, end)
            question = packet[12 : end + 4]
            answers = []
            if kind == 1 and name in CONFIG["hosts"]:
                answers = [socket.inet_aton(CONFIG["hosts"][name])]
            elif kind == 15 and name == "split.test":
                answers = [struct.pack("!H", 10) + wire("mail.split.test")]
            elif kind == 16 and name in {"split.test", "_dmarc.split.test"}:
                value = b"v=spf1 -all" if name == "split.test" else b"v=DMARC1; p=none"
                answers = [bytes([len(value)]) + value]
            elif not name.endswith(".test") and not name.endswith(".in-addr.arpa"):
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as upstream:
                    upstream.settimeout(2)
                    upstream.sendto(packet, ("127.0.0.11", 53))
                    response = upstream.recv(8192)
                connection.sendto(response, self.client_address)
                return
            response = packet[:2] + struct.pack("!5H", 0x8180, 1, len(answers), 0, 0) + question
            for answer in answers:
                response += (
                    b"\xc0\x0c" + struct.pack("!HHIH", kind, category, 30, len(answer)) + answer
                )
            connection.sendto(response, self.client_address)
        except (OSError, ValueError, IndexError, struct.error):
            return


class Relay(socketserver.StreamRequestHandler):
    def reply(self, text):
        self.wfile.write(text.encode() + b"\r\n")
        self.wfile.flush()

    def handle(self):
        secured = authorized = False
        sender = ""
        recipients = []
        self.connection.settimeout(30)
        self.reply("220 private synthetic relay")
        try:
            while line := self.rfile.readline(8193):
                verb, _, value = line.rstrip(b"\r\n").partition(b" ")
                verb = verb.upper()
                if verb in {b"EHLO", b"HELO"}:
                    self.reply("250-private synthetic relay")
                    self.reply("250 AUTH PLAIN" if secured else "250 STARTTLS")
                elif verb == b"STARTTLS" and not secured:
                    self.reply("220 begin TLS")
                    self.rfile.close()
                    self.wfile.close()
                    self.connection = TLS.wrap_socket(self.connection, server_side=True)
                    self.rfile = self.connection.makefile("rb")
                    self.wfile = self.connection.makefile("wb")
                    secured = True
                elif verb == b"AUTH":
                    expected = b"PLAIN " + base64.b64encode(b"\0synthetic\0fixture-secret")
                    authorized = secured and value == expected
                    self.reply("235 authenticated" if authorized else "535 rejected")
                elif verb == b"MAIL":
                    match = re.match(rb"FROM:<([^>]+)>", value, re.I)
                    sender = match[1].decode() if match else ""
                    recipients = []
                    self.reply(
                        "250 sender accepted"
                        if authorized and sender.endswith("@split.test")
                        else "550 rejected"
                    )
                elif verb == b"RCPT":
                    match = re.match(rb"TO:<([^>]+)>", value, re.I)
                    address = match[1].decode() if match else ""
                    if authorized and address.endswith("@split.test"):
                        recipients.append(address)
                        self.reply("250 recipient accepted")
                    else:
                        self.reply("550 synthetic recipients only")
                elif verb == b"DATA" and authorized and recipients:
                    self.reply("354 send message")
                    body = bytearray()
                    while data := self.rfile.readline(65537):
                        if data == b".\r\n":
                            break
                        body.extend(data[1:] if data.startswith(b"..") else data)
                        if len(body) > 18 * 1024 * 1024:
                            raise OSError("Synthetic message limit")
                    try:
                        with smtplib.SMTP("mail.split.test", 25, timeout=15) as incoming:
                            incoming.ehlo()
                            incoming.starttls(context=TRUST)
                            incoming.ehlo()
                            rejected = incoming.sendmail(sender, recipients, bytes(body))
                        if rejected:
                            raise OSError("Synthetic recipient rejected")
                        self.reply("250 delivered to isolated Maddy")
                        with (ROOT / "relay-results.jsonl").open("a") as writer:
                            writer.write(
                                json.dumps({"bytes": len(body), "recipients": len(recipients)})
                                + "\n"
                            )
                    except (OSError, smtplib.SMTPException):
                        self.reply("451 isolated delivery unavailable")
                elif verb == b"RSET":
                    sender = ""
                    recipients = []
                    self.reply("250 reset")
                elif verb == b"QUIT":
                    self.reply("221 bye")
                    break
                else:
                    self.reply("500 unsupported")
        except (OSError, ssl.SSLError):
            return


TLS = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
TLS.load_cert_chain(ROOT / "relay.pem", ROOT / "relay.key")
TRUST = ssl.create_default_context(cafile=ROOT / "ca.pem")
DNS_SERVER = socketserver.ThreadingUDPServer(("0.0.0.0", 53), DNS)
DNS_SERVER.daemon_threads = True
threading.Thread(target=DNS_SERVER.serve_forever, daemon=True).start()
SMTP_SERVER = socketserver.ThreadingTCPServer(("0.0.0.0", 587), Relay)
SMTP_SERVER.daemon_threads = True
SMTP_SERVER.serve_forever()
