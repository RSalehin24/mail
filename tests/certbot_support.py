"""Actual Certbot CLI in the approved private, challenge-validating ACME network."""

import json
import subprocess
import uuid

from tools.fixture_images import CERTBOT_IMAGE

CERTBOT_MEMORY_MIB = 128
CERTBOT_DEADLINE_SECONDS = 90


class LocalCertbot:
    def __init__(self, fixture, network):
        self.fixture, self.network = fixture, network
        self.root = fixture.root / 'certbot'
        self.root.mkdir(mode=0o700)
        for name in ('configuration', 'work'):
            (self.root / name).mkdir(mode=0o700)
        # A private fixture does not need Certbot's artificial scheduling jitter.
        (self.root / 'configuration/cli.ini').write_text('no-random-sleep-on-renew = true\n')
        self.pair = self.root / 'configuration/live/mail.rsalehin24.me'
        self.runs = []

    def arguments(self, name):
        return ('create', '--pull', 'never', '--name', name, '--platform', 'linux/amd64',
                '--label', 'mail-admin.fixture=' + self.network.name,
                '--network', self.network.name, '--ip', self.network.client_ip,
                '--read-only', '--cap-drop', 'ALL', '--cap-add', 'NET_BIND_SERVICE',
                '--security-opt', 'no-new-privileges:true', '--user', '0:0',
                '--memory', str(CERTBOT_MEMORY_MIB) + 'm', '--memory-swap',
                str(CERTBOT_MEMORY_MIB) + 'm', '--cpus', '0.5', '--pids-limit', '128',
                '--tmpfs', '/tmp:size=8388608,mode=1777',  # noqa: S108 -- private bounded tmpfs.
                '--tmpfs', '/var/log/letsencrypt:size=8388608,mode=0700',
                '--log-driver', 'local', '--log-opt', 'max-size=1m', '--log-opt', 'max-file=2',
                '-e', 'REQUESTS_CA_BUNDLE=/api-ca.pem')

    def mounts(self):
        result = ()
        paths = ((self.root / 'configuration', '/etc/letsencrypt'),
                 (self.root / 'work', '/var/lib/letsencrypt'),
                 (self.fixture.certificates.trust, '/api-ca.pem,readonly'))
        for path, destination in paths:
            result += ('--mount', 'type=bind,src=' + self.fixture.daemon_path(path)
                       + ',dst=' + destination)
        return result

    def run(self, arguments):
        name = self.network.name + '-client-' + str(uuid.uuid4())
        created = self.fixture.command(self.arguments(name) + self.mounts()
                                       + (CERTBOT_IMAGE,) + arguments)
        identifier = created.stdout.strip()
        self.network.created.append(identifier)
        result = subprocess.run(
            (self.fixture.docker, 'start', '--attach', identifier),
            capture_output=True, text=True, timeout=CERTBOT_DEADLINE_SECONDS, check=False,
        )
        state = json.loads(self.fixture.command(('inspect', identifier)).stdout)[0]
        self.runs.append({'container': identifier, 'exit_code': state['State']['ExitCode'],
                          'attached_exit_code': result.returncode,
                          'oom_killed': state['State']['OOMKilled']})
        if state['State']['Running'] or state['State']['OOMKilled']:
            raise RuntimeError('Certbot did not complete within its fixture resources')
        return result

    def issue(self):
        return self.run(('certonly', '--standalone', '--non-interactive', '--agree-tos',
                         '--email', 'rsalehin24@gmail.com', '--server',
                         'https://pebble:14000/dir', '-d', 'mail.rsalehin24.me',
                         '--cert-name', 'mail.rsalehin24.me'))

    def renew(self):
        return self.run(('renew', '--non-interactive'))

    def make_renewal_due(self):
        configuration = self.root / 'configuration/renewal/mail.rsalehin24.me.conf'
        source = configuration.read_text()
        configuration.write_text('renew_before_expiry = 100 days\n' + source)
