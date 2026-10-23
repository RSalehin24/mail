import os
import unittest

from backend.auth import Verification
from backend.budgets import BudgetReservation, Budgets
from backend.factors import FactorAttempt, Factors
from backend.retention import HISTORY_SECONDS, cleanup
from helper.journal import Journal
from shared.errors import ServiceError
from tests.auth_support import AuthFixture
from tests.helper_support import HelperFixture


class HousekeepingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = AuthFixture()
        self.addCleanup(self.fixture.close)

    def consume(self, kind, code):
        with self.fixture.database.transaction() as database:
            return Factors(self.fixture.keys).consume(database, (
                FactorAttempt(kind, code, 'fixture-challenge'), self.fixture.now,
            ))

    def test_cleanup_preserves_active_shared_budgets_until_the_exact_window_boundary(self):
        budgets = Budgets(self.fixture.database)
        started = self.fixture.now
        for bucket, count in (('auth', 15), ('send', 3)):
            for _ in range(count):
                budgets.reserve(BudgetReservation(bucket, 'retained-peer', started))
        for offset in (1, 899):
            cleanup(self.fixture.database, started + offset)
            for bucket in ('auth', 'send'):
                with self.assertRaises(ServiceError) as caught:
                    budgets.reserve(BudgetReservation(bucket, 'retained-peer', started + offset))
                self.assertEqual((caught.exception.status, caught.exception.retry_after),
                                 (429, 900 - offset))
        cleanup(self.fixture.database, started + 900)
        for bucket in ('auth', 'send'):
            budgets.reserve(BudgetReservation(bucket, 'retained-peer', started + 900))

    def test_cleanup_never_reopens_consumed_recovery_codes_or_the_totp_replay_watermark(self):
        code = self.fixture.code()
        self.assertTrue(self.consume('totp', code))
        recovery = self.fixture.codes[0]
        self.assertTrue(self.consume('recovery', recovery))
        cleanup(self.fixture.database, self.fixture.now)
        self.assertFalse(self.consume('totp', code))
        self.assertFalse(self.consume('recovery', recovery))
        step = int(self.fixture.now // 30)
        self.fixture.now += HISTORY_SECONDS + 1
        cleanup(self.fixture.database, self.fixture.now)
        with self.fixture.database.transaction() as database:
            watermark = database.execute('SELECT last_step FROM admin_auth').fetchone()[0]
            self.assertEqual(watermark, step)
            consumed = database.execute('SELECT consumed FROM recovery_codes WHERE verifier=?',
                (self.fixture.keys.digest(recovery, 'recovery'),)).fetchone()[0]
            self.assertEqual(consumed, 1)
        challenge = self.fixture.challenge('after-retention')
        with self.assertRaises(ServiceError) as caught:
            self.fixture.auth.verify(Verification(challenge, 'recovery', recovery,
                                      'after-retention'), 'login')
        self.assertEqual(caught.exception.status, 401)

    def test_current_global_and_per_mailbox_receipt_evidence_survives_old_history_cleanup(self):
        self.fixture.ready_relay()
        fixed = 'confirmed-fixture'
        mailbox = 'retained@rsalehin24.me'
        with self.fixture.database.transaction() as database:
            database.execute('INSERT INTO relay_tests VALUES(?,?,?,?,1,1,1,?)',
                ('sender-proof', 1, mailbox, 'rsalehin24@gmail.com', self.fixture.now))
            database.execute('INSERT INTO mailboxes(email,created,updated,status,outbound_test) '
                'VALUES(?,?,?,?,?)', (mailbox, self.fixture.now, self.fixture.now,
                                      'ready', 'sender-proof'))
            database.execute('INSERT INTO relay_tests VALUES(?,?,?,?,1,1,0,?)',
                ('unreferenced-old', 1, mailbox, 'rsalehin24@gmail.com', self.fixture.now))
        self.fixture.now += HISTORY_SECONDS + 1
        cleanup(self.fixture.database, self.fixture.now)
        with self.fixture.database.transaction() as database:
            retained = {row[0] for row in database.execute('SELECT id FROM relay_tests')}
            self.assertEqual(retained, {fixed, 'sender-proof'})
            from backend.relay_state import otp_ready

            self.assertTrue(otp_ready(database))
            self.assertEqual(database.execute('SELECT outbound_test FROM mailboxes')
                             .fetchone()[0], 'sender-proof')

    def test_journal_retention_keeps_every_unresolved_operation_and_active_recent_completion(self):
        host = HelperFixture()
        self.addCleanup(host.close)
        journal = Journal(host.journal.path, os.getuid())
        now = self.fixture.now
        identifiers = {}
        for state in ('pending', 'unknown', 'reconciliation_required', 'complete', 'failed',
                      'conflict'):
            request = host.request('create', state.replace('_', '-') + '@rsalehin24.me')
            identifiers[state] = request.request_id
            journal.begin(request, (now - HISTORY_SECONDS - 1, {}))
            journal.update(request.request_id, {'state': state})
        recent = host.request('create', 'recent@rsalehin24.me')
        journal.begin(recent, (now, {}))
        journal.update(recent.request_id, {'state': 'complete'})
        journal.retain(now)
        self.assertEqual({row['state'] for row in journal.unresolved()},
                         {'pending', 'unknown', 'reconciliation_required'})
        for state in ('complete', 'failed', 'conflict'):
            self.assertIsNone(journal.lookup(identifiers[state]))
        self.assertEqual(journal.lookup(recent.request_id)['state'], 'complete')
