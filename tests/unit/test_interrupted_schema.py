import unittest

from backend.database import Database
from shared.errors import ServiceError
from tests.schema_fault_support import interrupted_schema
from tests.support import StateFixture


class InterruptedSchemaTests(unittest.TestCase):
    def test_actual_termination_before_schema_commit_cannot_be_opened_as_an_installation(self):
        fixture = StateFixture()
        self.addCleanup(fixture.close)
        original = fixture.database.path.read_bytes()
        with interrupted_schema(fixture.database):
            with self.assertRaisesRegex(ServiceError, 'incompatible_schema'):
                Database(fixture.database.path, fixture.database.identity)
        self.assertEqual(fixture.database.path.read_bytes(), original)
        Database(fixture.database.path, fixture.database.identity)
