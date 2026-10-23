"""Private ACME/DNS services with real challenge validation and bounded resources."""

import http.client
import ipaddress
import json
import os
import socket
import ssl
import time
import uuid
from pathlib import Path

from shared.files import atomic_json
from tools.fixture_images import CHALLENGE_IMAGE, PEBBLE_IMAGE


class AcmeNetwork:
    def __init__(self, fixture):
        self.fixture = fixture
        self.name = 'mail-admin-acme-' + str(uuid.uuid4())
        self.created, self.network_created = [], False
        self.root = fixture.root / 'acme-network'
        self.root.mkdir(mode=0o700)

    def start(self):
        self.fixture.command(('network', 'create', '--internal', '--label',
                              'mail-admin.fixture=' + self.name, self.name))
        self.network_created = True
        network = json.loads(self.fixture.command(('network', 'inspect', self.name)).stdout)[0]
        subnet = ipaddress.ip_network(network['IPAM']['Config'][0]['Subnet'])
        if subnet.version != 4 or subnet.num_addresses < 32:
            raise RuntimeError('The private ACME fixture requires its own IPv4 subnet')
        self.dns_ip, self.pebble_ip, self.client_ip = (
            str(subnet.network_address + offset) for offset in (10, 11, 12)
        )
        self.api_pair = self.fixture.certificates.issue('acme-api', hostname='pebble')
        self.prepare_configuration()
        self.start_dns()
        self.start_authority()
        self.wait_for_authority()

    def prepare_configuration(self):
        os.chown(self.api_pair, 0, 10001)
        self.api_pair.chmod(0o750)
        for path in self.api_pair.iterdir():
            os.chown(path, 0, 10001)
            path.chmod(0o640)
        atomic_json(self.root / 'pebble.json', {'pebble': {
            'listenAddress': '0.0.0.0:14000', 'managementListenAddress': '0.0.0.0:15000',
            'certificate': '/api/fullchain.pem', 'privateKey': '/api/privkey.pem',
            'httpPort': 80, 'tlsPort': 443, 'retryAfter': {'authz': 1, 'order': 1},
            'profiles': {'default': {'validityPeriod': 90 * 86400}},
        }})
        (self.root / 'pebble.json').chmod(0o644)

    def arguments(self, role, address):
        return ('run', '-d', '--pull', 'never', '--name', self.name + '-' + role,
                '--platform', 'linux/amd64', '--label', 'mail-admin.fixture=' + self.name,
                '--network', self.name, '--ip', address, '--read-only', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges:true', '--user', '10001:10001',
                '--memory', '64m', '--memory-swap', '64m', '--cpus', '0.5', '--pids-limit', '64',
                '--log-driver', 'local', '--log-opt', 'max-size=1m', '--log-opt', 'max-file=2')

    def launch(self, arguments):
        result = self.fixture.command(arguments)
        self.created.append(result.stdout.strip())

    def start_dns(self):
        self.launch(self.arguments('dns', self.dns_ip) + (
            CHALLENGE_IMAGE, '-defaultIPv4', self.client_ip, '-defaultIPv6', '',
            '-http01', '', '-https01', '', '-tlsalpn01', '', '-doh', '',
        ))

    def start_authority(self):
        self.launch(self.arguments('authority', self.pebble_ip) + (
            '--network-alias', 'pebble', '-e', 'PEBBLE_VA_NOSLEEP=1',
            '-e', 'PEBBLE_WFE_NONCEREJECT=0', '-e', 'PEBBLE_AUTHZREUSE=0',
            '--mount', 'type=bind,src='
            + self.fixture.daemon_path(self.root / 'pebble.json') + ',dst=/pebble.json,readonly',
            '--mount', 'type=bind,src=' + self.fixture.daemon_path(self.api_pair)
            + ',dst=/api,readonly', PEBBLE_IMAGE,
            '-config', '/pebble.json', '-strict', '-dnsserver', self.dns_ip + ':8053',
        ))

    def request(self, path):
        context = ssl.create_default_context(cafile=self.fixture.certificates.trust)
        with socket.create_connection((self.pebble_ip, 15000), timeout=2) as raw:
            with context.wrap_socket(raw, server_hostname='pebble') as connection:
                connection.sendall(('GET ' + path + ' HTTP/1.1\r\nHost: pebble\r\n'
                                    'Connection: close\r\n\r\n').encode())
                response = http.client.HTTPResponse(connection)
                response.begin()
                body = response.read(65537)
                if response.status != 200 or len(body) > 65536:
                    raise RuntimeError('Unexpected private CA management response')
                return body

    def wait_for_authority(self):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                root = self.request('/roots/0')
                (self.root / 'issuance-root.pem').write_bytes(root)
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError('The private ACME authority did not become ready within fifteen seconds')

    def close(self):
        observations = []
        for identifier in reversed(self.created):
            state = json.loads(self.fixture.command(('inspect', identifier)).stdout)[0]
            if state['Config']['Labels'].get('mail-admin.fixture') != self.name:
                raise RuntimeError('Refusing cleanup of a container outside the ACME fixture')
            observations.append({
                'container_id': state['Id'], 'image_id': state['Image'],
                'state': {name: state['State'][name]
                          for name in ('Status', 'Running', 'ExitCode', 'OOMKilled')},
                'memory_limit_bytes': state['HostConfig']['Memory'],
                'published_ports': state['HostConfig']['PortBindings'],
                'network': state['HostConfig']['NetworkMode'],
            })
            self.fixture.command(('rm', '--force', identifier))
        atomic_json(Path('build') / (self.name + '-containers.json'), {
            'scope': 'private synthetic ACME fixture; not production or host-crash qualification',
            'containers': observations,
        })
        if self.network_created:
            self.fixture.command(('network', 'rm', self.name))

    def stop_dns(self):
        if not self.network_created or not self.created:
            raise RuntimeError('The private DNS fixture has not started')
        self.fixture.command(('stop', '--time', '5', self.created[0]))
