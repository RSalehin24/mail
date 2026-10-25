"""Real non-root HTTPS and Unix helper for the existing workstation Chrome runner."""

import argparse
import base64
import hashlib
import json
import signal
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

from host.locks import HostLock, LockPolicy  # noqa: E402
from shared.files import atomic_json  # noqa: E402
from tests.auth_support import PASSWORD  # noqa: E402
from tests.runtime_auth_support import RuntimeAuthentication  # noqa: E402
from tests.runtime_support import RuntimeFixture  # noqa: E402
from tools.run_tests import source_digest  # noqa: E402


class BrowserFixture(RuntimeFixture):
    def __init__(self):
        self.api_call_times = []
        super().__init__()

    def command(self, arguments):
        if arguments[0] == 'create':
            # Secret-handling flows must not depend on external MX latency.
            arguments = ('create', '--dns', '127.0.0.1') + tuple(arguments[1:])
        rewritten = tuple('127.0.0.1:443:8443' if value == '127.0.0.1::8443' else value
                          for value in arguments)
        return super().command(rewritten)

    def api(self, method, path, body=None):
        self.api_call_times.append(time.monotonic())
        return super().api(method, path, body)


def fixture_material(web, authentication):
    certificate = x509.load_pem_x509_certificate((web.tls / 'fullchain.pem').read_bytes())
    spki = certificate.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return {'password': PASSWORD, 'codes': authentication.codes,
            'relay_key': 'browser-isolated-relay-secret-' + web.root.name,
            'mailbox_password': ' isolated browser mailbox password ',
            'spki': base64.b64encode(hashlib.sha256(spki).digest()).decode(),
            'runtime_container_id': web.identifier, 'source_sha256': source_digest()}


def execute(directory):
    web = BrowserFixture()
    try:
        authentication = RuntimeAuthentication(unittest.TestCase(), web)
        sequence = [time.monotonic()]
        token = authentication.bootstrap()
        sequence.append(time.monotonic())
        with HostLock(web.host.lock, LockPolicy(1, 0)):
            web.mount_root = web.root
            web.launch()
            sequence.append(time.monotonic())
        sequence.append(time.monotonic())
        web.ready()
        authentication.confirm(token)
        sequence.append(time.monotonic())
        material = fixture_material(web, authentication)
        material['bootstrap_sequence'] = sequence
        path = directory / 'material.json'
        atomic_json(path, material)
        deadline = time.monotonic() + 600
        while not (directory / 'stop').exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('The bounded HTTPS browser fixture expired')
            time.sleep(0.1)
        logs = web.command(('logs', web.identifier))
        secrets = (material['password'], material['relay_key'], material['mailbox_password'],
                   *material['codes'])
        observation = {'secret_logs_absent': all(
            value not in logs.stdout + logs.stderr for value in secrets
        ), 'bootstrap_sequence': sequence, 'api_call_times': web.api_call_times,
            'runtime_container_id': web.identifier,
            'dns_servers': json.loads(web.command(('inspect', web.identifier)).stdout)[0]
                ['HostConfig']['Dns'],
            'source_sha256': material['source_sha256']}
        (directory / 'fixture-observation.json').write_text(json.dumps(observation) + '\n')
    finally:
        web.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    signal.signal(signal.SIGTERM, terminate)
    execute(parser.parse_args().directory)


def terminate(_number, _frame):
    raise SystemExit(1)


if __name__ == '__main__':
    main()
