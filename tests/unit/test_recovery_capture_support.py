import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.recovery_capture_support import ObservedManifest, source_dependencies


class RecoveryCaptureDependenciesTests(unittest.TestCase):
    def test_publication_observes_verification_even_when_file_mtime_is_coarse(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.json'
            publication = ObservedManifest(path, SimpleNamespace(verified_at_ns=1))
            from shared.files import atomic_json

            def coarse_write(target, value):
                atomic_json(target, value)
                os.utime(target, ns=(0, 0))

            with (patch('tests.recovery_capture_support.atomic_json', coarse_write),
                  patch('tests.recovery_capture_support.time.time_ns', return_value=2)):
                publication(path, {'verified': True})
            self.assertEqual(path.stat().st_mtime_ns, 0)
            self.assertEqual(publication.published_at_ns, 2)

    def test_manifest_cannot_be_written_before_verification_or_written_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.json'
            publication = ObservedManifest(path, SimpleNamespace())
            with self.assertRaisesRegex(RuntimeError, 'follow repository verification'):
                publication(path, {})
            self.assertFalse(path.exists())
            publication.repository.verified_at_ns = 1
            publication(path, {'first': True})
            with self.assertRaisesRegex(RuntimeError, 'once'):
                publication(path, {'second': True})
            self.assertEqual(json.loads(path.read_text()), {'first': True})

    def test_missing_alert_provider_inputs_do_not_block_independent_fixture_capture(self):
        profile = {'inputs': {'service_gid': 10001, 'lock_wait_seconds': 1,
                              'operation_timeout_seconds': 5}}
        journal = object()
        dependencies = source_dependencies(profile, SimpleNamespace(journal=journal))
        self.assertIs(dependencies.coordination.journal, journal)
        self.assertEqual(dependencies.services.docker.timeout, 5)
