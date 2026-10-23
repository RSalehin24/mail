"""Record artifact-bound before-reboot facts; this command never requests a reboot."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.host_reboot_support import prepare_reboot  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path)
    result = prepare_reboot(parser.parse_args().evidence)
    print(json.dumps({key: result[key] for key in (
        'instance_id', 'boot_id', 'artifact_set_sha256', 'started_at_utc',
    )}))


if __name__ == '__main__':
    main()
