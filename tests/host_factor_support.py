"""Independent RFC 6238 fixture values without installing application packages on hosts."""

import base64
import hashlib
import hmac
import struct


def totp(secret, observed_at):
    counter = struct.pack('>Q', int(observed_at) // 30)
    digest = hmac.new(base64.b32decode(secret), counter, hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff
    return str(value % 1_000_000).zfill(6)
