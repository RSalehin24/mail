import dataclasses
import sqlite3
import unittest
import uuid
from contextlib import closing
from unittest.mock import patch

from helper.journal import Journal
from helper.protocol import HelperRequest
from helper.service import HostHelper
from shared.errors import ServiceError
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import RealMaddyFixture, imap_login
from tests.mutation_crash_support import kill_at_boundary
from tests.restore_support import bodies, deliver


def boundaries(labels):
    return [('intent', None)] + [(point, label) for label in labels
                                for point in ('before_cli', 'after_cli', 'after_record')]


class MutationCrashTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RealMaddyFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.start()

    def helper(self):
        path = self.fixture.root / (str(uuid.uuid4()) + '.journal.db')
        return HostHelper(dataclasses.replace(self.fixture.helper.dependencies,
                                               journal=Journal.provision(path)))

    def request(self, operation, index):
        email = 'crash-' + operation + '-' + str(index) + '@rsalehin24.me'
        if operation != 'create':
            self.fixture.helper.handle(HelperRequest('create', str(uuid.uuid4()),
                                                     email, MAILBOX_PASSWORD))
            deliver(self.fixture, email, 'crash-fixture-message-' + str(index))
        password = (' changed crash fixture password '
                    if operation == 'password' else MAILBOX_PASSWORD)
        return HelperRequest(operation, str(uuid.uuid4()), email, password)

    def assert_no_replay(self, operation):
        helper, request = operation
        before = helper.journal.lookup(request.request_id)
        inventory = self.fixture.maddy.inventories()
        recovered = HostHelper(dataclasses.replace(helper.dependencies,
                                                   journal=Journal(helper.journal.path)))
        with patch.object(self.fixture.maddy, 'change', wraps=self.fixture.maddy.change) as change:
            outcome = recovered.handle(request)
        change.assert_not_called()
        self.assertEqual(self.fixture.maddy.inventories(), inventory)
        if before['state'] not in {'complete', 'failed'}:
            self.assertNotEqual(outcome['state'], 'complete')
            self.assertTrue(recovered.journal.unresolved())
            fresh = HelperRequest(request.operation, str(uuid.uuid4()), request.email,
                                  request.password)
            with self.assertRaises(ServiceError) as caught:
                recovered.handle(fresh)
            self.assertEqual(caught.exception.code, 'reconciliation_required')
        else:
            self.assertEqual(outcome['state'], before['state'])
        return outcome

    def assert_retained_reset_message(self, request, point):
        password = (MAILBOX_PASSWORD if point in {('intent', None),
                    ('before_cli', 'password-changed')} else request.password)
        with imap_login(self.fixture, request.email, password) as connection:
            self.assertIn(b'crash-fixture-message-', bodies(connection))

    def assert_crashes(self, operation, points):
        for index, point in enumerate(points):
            with self.subTest(boundary=point):
                helper, request = self.helper(), self.request(operation, index)
                kill_at_boundary(helper, request, point)
                self.assert_no_replay((helper, request))
                if operation == 'password':
                    self.assert_retained_reset_message(request, point)

    def test_sigkill_create_intent_each_cli_progress_observation_and_completion(self):
        points = boundaries(('credentials-created', 'storage-created'))
        self.assert_crashes('create', points + [('observed', None), ('terminal', None)])

    def test_sigkill_reset_preserves_durable_outcome_and_never_repeats_password_change(self):
        points = boundaries(('password-changed',))
        self.assert_crashes('password', points + [('observed', None), ('terminal', None)])

    def test_sigkill_delete_never_retries_removal_or_restores_access_automatically(self):
        # Capability checks have separate real root-file/API coverage. This fixture
        # authorizes only its own synthetic accounts for mutation crash injection.
        self.fixture.deletion.enabled = True
        points = boundaries(('credentials-removed', 'storage-removed'))
        self.assert_crashes('delete', points + [('observed', None), ('terminal', None)])

    def test_sigkill_credential_rollback_never_turns_unknown_completion_into_retry(self):
        with closing(sqlite3.connect(self.fixture.data / 'imapsql.db')) as connection:
            connection.execute('CREATE TRIGGER reject_crash_storage BEFORE INSERT ON users '
                               "BEGIN SELECT RAISE(ABORT,'fixture storage creation refused'); END")
            connection.commit()
        points = [(point, 'credentials-rolled-back')
                  for point in ('before_cli', 'after_cli', 'after_record')]
        self.assert_crashes('create', points + [('terminal', None)])
