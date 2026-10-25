"""Keep actual stop/start and served-TLS checks inside the private consumer project."""

from host.certificates import verify_served
from host.docker import Docker


class SnapshotConsumerDocker:
    def __init__(self, services):
        self.services = services
        self.timeout = 15

    def inspect(self, role):
        if role not in self.services.records:
            raise AssertionError('Refusing a snapshot consumer outside the fixture')
        return self.services.compose.docker.inspect(self.services.records[role]['container'])

    def run(self, arguments):
        if arguments[:-1] not in (('stop', '--time', '30'), ('start',),
                                 ('update', '--restart', 'no')):
            raise AssertionError('Unexpected snapshot service operation')
        role = arguments[-1]
        self.inspect(role)
        return self.services.compose.docker.run(arguments[:-1] + (
            self.services.records[role]['container'],
        ))

    def stop_for_maintenance(self, role):
        Docker.stop_for_maintenance(self, role)

    def verify(self, endpoint, expected, timeout):
        roles = {('127.0.0.1', 993): 'maddy', ('127.0.0.1', 443): 'mail-admin'}
        if endpoint not in roles:
            raise AssertionError('Unexpected snapshot verification endpoint')
        role = roles[endpoint]
        target = '993/tcp' if role == 'maddy' else '8443/tcp'
        ports = self.inspect(role)['NetworkSettings']['Ports'].get(target)
        if not ports:
            raise AssertionError('Restarted snapshot consumer has no published TLS port')
        # Docker can allocate a different ephemeral host port when restarting a fixture.
        verify_served(('127.0.0.1', int(ports[0]['HostPort'])), expected, timeout)
