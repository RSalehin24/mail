"""Explicit synthetic recovery policy for application/browser permission fixtures."""

import dataclasses
import os

from helper.backup_control import BackupControl
from helper.recovery import RecoveryControl
from helper.recovery_records import RecoveryRecords
from shared.files import atomic_json


class RecordingDeletion:
    """Stands in for the real DirectDeletion: records the request, keeps the idempotency rule."""

    def __init__(self, records):
        self.records, self.requests = records, []

    def delete(self, request, policy, now):
        self.records.begin(request.identifier, request.email, policy, request.retention, now,
                           '', '')
        with self.records.transaction() as connection:
            connection.execute("UPDATE recovery_records SET state='deleted' WHERE id=?",
                               (request.identifier,))
        self.requests.append(request)
        return {'request_id': request.identifier, 'state': 'complete', 'error': ''}


def configure_recovery(fixture):
    root = fixture.root / 'recovery-control'
    root.mkdir(mode=0o700)
    records = RecoveryRecords(root / 'recovery.db')
    policy = {'schema_version': 1, 'revision': 1, 'enabled': True,
              'permanent_deletion_enabled': False, 'default_retention': '7_days',
              'protected': {'mail@rsalehin24.me': 'Administrator verification emails'}}
    atomic_json(root / 'recovery-policy.json', policy)
    control = RecoveryControl(root, records, os.getuid(), deletion=RecordingDeletion(records))
    fixture.host.helper.dependencies = dataclasses.replace(
        fixture.host.helper.dependencies, recovery=control, backups=BackupControl(root),
    )
    return control
