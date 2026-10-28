"""Prepare a reviewable Maddy 0.9.6 submission change without reading provider secrets."""

import argparse
import json
from pathlib import Path

from shared.submission import SUBMISSION


def prepare(configuration: str, compose: dict) -> tuple[str, dict]:
    if 'submission tcp://' in configuration or 'submission_queue' in configuration:
        raise ValueError('Submission is already configured; review instead of appending twice')
    service = compose['services']['maddy']
    if set(service['ports']) != {'25:25', '993:993'}:
        raise ValueError('Review unexpected published ports')
    service['ports'].append('587:587')
    return configuration + '\nimport /data/submission-relay.conf\n' + SUBMISSION, compose


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('configuration', type=Path)
    parser.add_argument('compose', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    configuration, compose = prepare(args.configuration.read_text(),
                                     json.loads(args.compose.read_text()))
    args.output.mkdir(mode=0o700, exist_ok=False)
    (args.output / 'maddy.conf').write_text(configuration)
    (args.output / 'docker-compose.yml').write_text(json.dumps(compose, indent=2) + '\n')


if __name__ == '__main__':
    main()
