"""Actual dashboard HTTPS in the identity-checked disposable guest, using private fixture auth."""

import json
import ssl
import time
from pathlib import Path

from shared.files import protected_json
from tests.host_https_support import request


class GuestDashboard:
    def __init__(self, host):
        self.host = host
        self.material = protected_json(Path('/root/mail-admin-fixture/web-auth-material.json'))
        self.cookie = self.material['cookie']
        self.container = self.verify()

    def verify(self):
        actual = json.loads(self.host.command(('docker', 'inspect', 'mail-admin')))[0]
        if (actual['Image'] != self.host.record['runtime_image_id']
            or actual['Config']['User'] != '10001:10001'
            or not actual['State']['Running'] or not actual['HostConfig']['ReadonlyRootfs']
            or actual['HostConfig']['Privileged'] or actual['HostConfig']['CapDrop'] != ['ALL']):
            raise RuntimeError('The exact restricted dashboard fixture is required')
        mounts = {m['Destination']: (m['Source'], m['RW'])
                  for m in actual['Mounts'] if m['Type'] == 'bind'}
        expected = {'/app/data': ('/opt/mail-admin/data', True),
                    '/tls': ('/opt/maddy/tls/fixture', False),
                    '/run/mail-admin-helper': ('/run/mail-admin-helper', False),
                    '/run/secrets/mail-admin': ('/etc/mail-admin/secrets', False),
                    '/run/mail-admin-installation.json':
                    ('/etc/mail-admin/installation.json', False)}
        if mounts != expected:
            raise RuntimeError('Dashboard fixture bind identities differ')
        return actual

    def api(self, route, body=None):
        context = ssl.create_default_context(cafile='/root/mail-admin-fixture/fixture-ca.pem')
        return request((context, 10), (*route, body), self.cookie)

    def ready(self):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if self.api(('GET', '/api/status'))[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(0.1)
        raise RuntimeError('Dashboard did not become healthy within fifteen seconds')
