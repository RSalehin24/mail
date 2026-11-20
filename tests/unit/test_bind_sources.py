import unittest

from backend.binds import DIRECTORIES, LOCAL_DIRECTORIES, LOCAL_FILES, require_runtime_sources
from shared.errors import ServiceError
from tests.runtime_compose_support import displaced_source
from tests.support import StateFixture


class RuntimeBindSourceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        for relative in DIRECTORIES:
            (self.fixture.root / relative).mkdir(parents=True, exist_ok=False)
        self.marker = self.fixture.root / 'run/mail-admin-installation.json'
        self.marker.touch(mode=0o600)

    def test_valid_directories_allow_a_late_helper_without_creating_a_socket(self):
        require_runtime_sources(self.fixture.root)
        self.assertFalse((self.fixture.root / 'run/mail-admin-helper/control.sock').exists())

    def test_missing_wrong_type_and_linked_bind_sources_are_rejected_without_replacement(self):
        paths = [self.fixture.root / relative for relative in DIRECTORIES] + [self.marker]
        for path in paths:
            with self.subTest(source=path.name), displaced_source(path) as directory:
                with self.assertRaisesRegex(ServiceError, 'runtime_bind_type_mismatch'):
                    require_runtime_sources(self.fixture.root)
                self.assertFalse(path.exists())
                replacement = path.with_name(path.name + '.replacement')
                if directory:
                    path.touch(mode=0o600)
                else:
                    path.mkdir()
                with self.assertRaisesRegex(ServiceError, 'runtime_bind_type_mismatch'):
                    require_runtime_sources(self.fixture.root)
                path.rename(replacement)
                path.symlink_to(replacement)
                with self.assertRaisesRegex(ServiceError, 'runtime_bind_type_mismatch'):
                    require_runtime_sources(self.fixture.root)


class LocalHostBindSourceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        for relative in LOCAL_DIRECTORIES:
            (self.fixture.root / relative).mkdir(parents=True, exist_ok=True)
        for relative in LOCAL_FILES:
            path = self.fixture.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(mode=0o600)

    def test_valid_local_sources_need_no_helper_socket_directory(self):
        require_runtime_sources(self.fixture.root, local_host=True)
        self.assertFalse((self.fixture.root / 'run/mail-admin-helper').exists())

    def test_missing_wrong_type_and_linked_local_sources_are_rejected(self):
        paths = [self.fixture.root / relative for relative in LOCAL_DIRECTORIES + LOCAL_FILES]
        for path in paths:
            with self.subTest(source=str(path.relative_to(self.fixture.root))), \
                    displaced_source(path) as directory:
                with self.assertRaisesRegex(ServiceError, 'runtime_bind_type_mismatch'):
                    require_runtime_sources(self.fixture.root, local_host=True)
                if directory:
                    path.touch(mode=0o600)
                else:
                    path.mkdir()
                with self.assertRaisesRegex(ServiceError, 'runtime_bind_type_mismatch'):
                    require_runtime_sources(self.fixture.root, local_host=True)
                (path.unlink if directory else path.rmdir)()
                path.symlink_to(path.with_name(path.name + '.original-link'))
                with self.assertRaisesRegex(ServiceError, 'runtime_bind_type_mismatch'):
                    require_runtime_sources(self.fixture.root, local_host=True)
                path.unlink()
