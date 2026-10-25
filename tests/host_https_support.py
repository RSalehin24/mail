"""Local fixture transport preserves the application's actual Host and Origin policy."""

import json

from shared.validation import MAIL_HOST
from tests.host_tls_support import LoopbackHttps


def request(transport, message, cookie):
    context, timeout = transport
    method, path, body = message
    connection = LoopbackHttps(context, timeout)
    headers = {'Host': MAIL_HOST, 'Origin': 'https://' + MAIL_HOST,
               'Content-Type': 'application/json'}
    if cookie:
        headers['Cookie'] = cookie
    try:
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), json.loads(response.read())
    finally:
        connection.close()
