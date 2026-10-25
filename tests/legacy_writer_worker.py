"""A bounded observed legacy writer, used only by privately named guest systemd units."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.files import atomic_json  # noqa: E402
from tests.host_support import require_fixture_guest  # noqa: E402


def run(root, instance):
    require_fixture_guest(instance)
    if (root.parent != Path('/root/mail-admin-fixture')
            or not root.name.startswith('mail-admin-legacy-') or root.resolve() != root):
        raise RuntimeError('Only the private legacy fixture may be written')
    atomic_json(root / 'writer-started.json', {'observed_at': time.time()})
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if (root / 'release').exists():
            atomic_json(root / 'writer-completed.json', {'observed_at': time.time()})
            return
        time.sleep(0.1)
    raise RuntimeError('The private legacy writer was not explicitly released')


if __name__ == '__main__':
    run(Path(sys.argv[1]), sys.argv[2])
