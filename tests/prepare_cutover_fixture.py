"""Create an independent Maddy-only legacy TLS deployment inside one bounded image utility."""

import os
import sys
from pathlib import Path

from shared.files import atomic_json
from tests.host_support import require_fixture_guest
from tests.standalone_tls_support import StandaloneTls
from tools.run_tests import source_digest


def host_paths(value):
    if isinstance(value, str) and value.startswith('/fixtures/'):
        return os.environ['MAIL_ADMIN_FIXTURE_DAEMON_ROOT'] + value[len('/fixtures'):]
    if isinstance(value, dict):
        return {key: host_paths(item) for key, item in value.items()}
    return value


def prepare(directory, instance):
    require_fixture_guest(instance)
    scene = StandaloneTls()
    scene.services.start_mail = scene.mail.launch
    scene.services.start_mail_only()
    original = scene.mail.tls
    certificate = scene.generations.validator.validate(original)
    scene.services.consumers()['maddy'].activate(original, certificate)
    desired = scene.generations.stage(scene.mail.certificates.issue('cutover-next'))
    scene.generations.publish(desired)
    state = scene.generations.read()
    state['coordination_migrated'] = False
    scene.generations.write(state)
    atomic_json(directory / 'tls-context.json', host_paths({
        'instance_id': instance, 'source_sha256': source_digest(),
        'mail_root': str(scene.mail.root), 'web_root': str(scene.services.web.root),
        'project': scene.services.compose.project, 'names': scene.services.compose.names,
        'records': scene.services.records, 'trust': str(scene.mail.certificates.trust),
    }))
    # The fixture intentionally outlives its preparation controller and its helper thread.
    sys.stdout.flush()
    os._exit(0)


if __name__ == '__main__':
    prepare(Path(sys.argv[1]), sys.argv[2])
