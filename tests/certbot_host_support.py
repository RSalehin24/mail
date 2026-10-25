"""Keep the real host Certbot adapter while remapping only its isolated environment."""

from host.container_identity import ContainerIdentity
from host.docker import Docker
from tests.certbot_support import CERTBOT_DEADLINE_SECONDS
from tools.fixture_images import CERTBOT_IMAGE


class PrivateCertbotDocker:
    def __init__(self, certbot):
        self.client = certbot
        self.docker = Docker(certbot.fixture.docker, CERTBOT_DEADLINE_SECONDS)
        self.created = set()

    def run(self, arguments):
        if arguments[0] == 'create':
            identifier = self.docker.run(self.create_arguments(arguments))
            self.created.add(identifier)
            self.client.network.created.append(identifier)
            return identifier
        if arguments[:2] == ('container', 'ls'):
            return self.docker.run(arguments)
        if arguments[0] == 'rm' or arguments[:2] == ('start', '--attach'):
            identifier = arguments[-1]
            self.require_owned(identifier)
            result = self.docker.run(arguments)
            if arguments[0] == 'rm':
                self.created.remove(identifier)
                self.client.network.created.remove(identifier)
            return result
        raise AssertionError('Unexpected private Certbot operation')

    def create_arguments(self, arguments):
        position = arguments.index(CERTBOT_IMAGE)
        prefix, command = arguments[:position], arguments[position:]
        if command not in ((CERTBOT_IMAGE, 'renew', '--non-interactive'),
                           (CERTBOT_IMAGE, 'renew', '--non-interactive', '--dry-run')):
            raise AssertionError('Unexpected real host Certbot command')
        port = prefix.index('-p')
        if prefix[port + 1] != '80:80':
            raise AssertionError('Unexpected published ACME port')
        prefix = prefix[:port] + prefix[port + 2:]
        substitutions = {
            'type=bind,src=/opt/maddy/letsencrypt,dst=/etc/letsencrypt':
                'type=bind,src=' + self.client.fixture.daemon_path(
                    self.client.root / 'configuration') + ',dst=/etc/letsencrypt',
            'type=bind,src=/opt/maddy/letsencrypt_lib,dst=/var/lib/letsencrypt':
                'type=bind,src=' + self.client.fixture.daemon_path(
                    self.client.root / 'work') + ',dst=/var/lib/letsencrypt',
        }
        prefix = tuple(substitutions.get(value, value) for value in prefix)
        if any('/opt/maddy' in value for value in prefix):
            raise AssertionError('Production bind path in private Certbot fixture')
        return prefix + self.network_arguments() + command

    def network_arguments(self):
        return ('--platform', 'linux/amd64', '--network', self.client.network.name,
                '--ip', self.client.network.client_ip, '--user', '0:0', '--label',
                'mail-admin.fixture=' + self.client.network.name,
                '--mount', 'type=bind,src=' + self.client.fixture.daemon_path(
                    self.client.fixture.certificates.trust) + ',dst=/api-ca.pem,readonly',
                '-e', 'REQUESTS_CA_BUNDLE=/api-ca.pem')

    def require_owned(self, identifier):
        if identifier not in self.created:
            raise AssertionError('Refusing a container outside the private Certbot fixture')

    def inspect(self, identifier):
        self.require_owned(identifier)
        return self.docker.inspect(identifier)

    def adopt_recorded_run(self, run):
        identifier = run['container_id']
        record = ContainerIdentity(self.docker, ('certbot', run['name'])).observe(identifier)
        expected_image = self.docker.inspect(CERTBOT_IMAGE)['Id']
        if (record['Image'] != expected_image
                or record['Config']['Labels'].get('mail-admin.fixture') != self.client.network.name
                or record['HostConfig']['NetworkMode'] != self.client.network.name):
            raise AssertionError('Refusing adoption outside this private Certbot fixture')
        self.created.add(identifier)
        self.client.network.created.append(identifier)
