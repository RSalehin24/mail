import hashlib
import http.server
import ssl
import threading
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree


class R2Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_arguments):
        pass

    def do_PUT(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.requests.append(("PUT", self.path))
        if self.headers.get("X-Amz-Content-Sha256") != hashlib.sha256(body).hexdigest():
            self.reply(400)
        elif self.headers.get("If-None-Match") != "*":
            self.reply(400)
        elif self.path in self.server.objects:
            self.reply(412)
        else:
            self.server.objects[self.path] = (body, datetime.now(UTC))
            self.server.upload_headers.append(dict(self.headers.items()))
            self.reply(200)

    def do_GET(self):
        self.server.requests.append(("GET", self.path))
        parsed = urlsplit(self.path)
        if self.server.error_status:
            self.reply(self.server.error_status)
        elif parsed.query:
            self.listing(parse_qs(parsed.query, keep_blank_values=True))
        elif self.path in self.server.objects:
            body, _modified = self.server.objects[self.path]
            self.reply(200, body)
        else:
            self.reply(404)

    def do_DELETE(self):
        self.server.requests.append(("DELETE", self.path))
        if self.server.error_status:
            self.reply(self.server.error_status)
            return
        self.server.objects.pop(self.path, None)
        self.reply(204)

    def listing(self, query):
        if self.server.listing_body is not None:
            self.reply(200, self.server.listing_body)
            return
        prefix = "/fixture-bucket/" + query["prefix"][0]
        root = ElementTree.Element("ListBucketResult", xmlns="http://s3.amazonaws.com/doc/2006-03-01/")
        ElementTree.SubElement(root, "IsTruncated").text = "false"
        for key, (body, modified) in self.server.objects.items():
            if key.startswith(prefix):
                item = ElementTree.SubElement(root, "Contents")
                ElementTree.SubElement(item, "Key").text = key.removeprefix("/fixture-bucket/")
                ElementTree.SubElement(item, "Size").text = str(len(body))
                ElementTree.SubElement(item, "LastModified").text = modified.isoformat()
        self.reply(200, ElementTree.tostring(root))

    def reply(self, status, body=b""):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body) + self.server.extra_content_length))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (OSError, ssl.SSLError):
            self.server.disconnected += 1


class R2Peer:
    def __init__(self, certificate):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), R2Handler)
        self.server.daemon_threads = True
        self.server.objects = {}
        self.server.requests = []
        self.server.upload_headers = []
        self.server.error_status = 0
        self.server.listing_body = None
        self.server.extra_content_length = 0
        self.server.disconnected = 0
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(*(certificate / name for name in ("fullchain.pem", "privkey.pem")))
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True
        )
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
