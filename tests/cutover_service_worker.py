"""The private loaded renewal service uses the real locks, generations and Docker activation."""

import os
import sys
import time
from pathlib import Path

from host.docker import Docker, DockerConsumer
from host.locks import HostLock
from host.renewal import Renewal
from shared.files import atomic_json, protected_json
from tests.host_support import require_fixture_guest
from tests.kernel_scenario_support import generation_host, require_matching_source


def run(path):
    record = protected_json(path)
    require_fixture_guest(record['instance_id'])
    require_matching_source(record)
    root = Path(record['mail_root']) / 'renewal'
    if not root.is_relative_to(path.parent):
        raise RuntimeError('Private renewal state escaped the cutover fixture')
    os.environ.update(SSL_CERT_FILE=record['trust'], SSL_CERT_DIR='/nonexistent-fixture-ca')
    host = generation_host(root, Path(record['trust']))
    locks = host.coordination.locks
    # A service invoked under either parent's shared lock fails at the original bound.
    with HostLock(locks.renewal, locks.policy), HostLock(locks.maddy, locks.policy):
        observed_at = time.time()
    consumers = {role: DockerConsumer(consumer, Docker(timeout=15))
                 for role, consumer in record['records'].items()}
    Renewal(host.coordination, (None, consumers)).retry_activation()
    result_path = path.parent / 'service-observations.json'
    previous = protected_json(result_path)['runs'] if result_path.exists() else []
    previous.append({'observed_at': observed_at, 'shared_locks_available': True,
                     'served_validation_preceded_timer':
                     (path.parent / 'served-verified.json').exists()})
    atomic_json(result_path, {'runs': previous})


if __name__ == '__main__':
    run(Path(sys.argv[1]))
