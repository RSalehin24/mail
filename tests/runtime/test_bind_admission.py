import subprocess
import unittest

from tests.runtime_compose_support import RuntimeCompose, displaced_source
from tests.runtime_support import RuntimeFixture


class BindAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RuntimeFixture()
        self.addCleanup(self.fixture.close)
        self.compose = RuntimeCompose(self.fixture)
        self.addCleanup(self.compose.close)
        self.compose.prepare()

    def sources(self):
        return [source for source, _ in self.fixture.bind_sources()] + [self.fixture.tls]

    def assert_compatible_startup(self):
        self.compose.start()
        self.assertEqual(self.fixture.api('GET', '/api/status')[0], 200)
        self.compose.close()

    def test_actual_compose_refuses_every_missing_source_without_creating_replacements(self):
        for path in self.sources():
            with self.subTest(source=path.name), displaced_source(path):
                try:
                    with self.assertRaises(subprocess.CalledProcessError) as rejected:
                        self.compose.start()
                    self.assertIn('bind source path does not exist', rejected.exception.stderr)
                    self.assertFalse(path.exists())
                finally:
                    self.compose.close()
        self.assert_compatible_startup()

    def test_actual_compose_wrong_source_types_cannot_start_a_healthy_application(self):
        for path in self.sources():
            with self.subTest(source=path.name), displaced_source(path) as directory:
                if directory:
                    path.write_bytes(b'fixture type mismatch')
                else:
                    path.mkdir()
                try:
                    with self.assertRaises((subprocess.CalledProcessError, RuntimeError)):
                        self.compose.start()
                    if self.fixture.identifier:
                        record = self.compose.docker.inspect(self.fixture.identifier)
                        self.assertFalse(record['State']['Running'])
                        self.assertFalse(record['State']['OOMKilled'])
                finally:
                    self.compose.close()
        self.assert_compatible_startup()
