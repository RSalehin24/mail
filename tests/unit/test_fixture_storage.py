"""A sparse local engine must not exhaust its actual host filesystem."""

import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tools.resources import FIXTURE_DISK_RESERVE_BYTES, require_fixture_disk
from tools.run_linux import main


class FixtureStorageTests(unittest.TestCase):
    def test_low_mac_disk_stops_before_any_docker_operation_or_fixture_creation(self):
        with (patch.object(sys, 'argv', ['run_linux.py', '--suite', 'acme']),
              patch('shutil.disk_usage', return_value=SimpleNamespace(free=116 * 1024**2)),
              patch('tools.run_linux.docker') as docker):
            with self.assertRaisesRegex(RuntimeError, 'free disk space'):
                main()
        docker.assert_not_called()

    def test_the_existing_native_reserve_is_also_enforced_for_local_builds(self):
        from pathlib import Path

        with patch('shutil.disk_usage', return_value=SimpleNamespace(
            free=FIXTURE_DISK_RESERVE_BYTES - 1,
        )):
            with self.assertRaisesRegex(RuntimeError, 'free disk space'):
                require_fixture_disk(Path('.'))
        with patch('shutil.disk_usage', return_value=SimpleNamespace(
            free=FIXTURE_DISK_RESERVE_BYTES,
        )):
            require_fixture_disk(Path('.'))

    def test_stale_or_damaged_image_is_rejected_before_any_fixture_volume_is_created(self):
        def docker(arguments):
            if arguments == ('context', 'inspect'):
                return json.dumps([{'Endpoints': {'docker': {'Host': 'unix:///fixture.sock'}}}])
            if arguments[:2] == ('image', 'inspect'):
                return json.dumps([{'Id': arguments[2]}])
            self.fail('Fixture mutation occurred before source and runtime verification')

        with (patch.object(sys, 'argv', ['run_linux.py', '--suite', 'acme']),
              patch('tools.run_linux.require_fixture_disk'),
              patch('tools.run_linux.docker', side_effect=docker),
              patch('tools.run_remote.verify_images', side_effect=RuntimeError('image differs'))):
            with self.assertRaisesRegex(RuntimeError, 'image differs'):
                main()
