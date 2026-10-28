"""Explicit synthetic recovery policy for application/browser permission fixtures."""

import dataclasses
import hashlib
import os
from pathlib import Path

from helper.backup_control import BackupControl
from helper.recovery import QUALIFIED_FILES, RecoveryControl
from helper.recovery_records import RecoveryRecords
from shared.files import atomic_json


def configure_recovery(fixture):
    root = fixture.root / 'recovery-control'
    root.mkdir(mode=0o700)
    records = RecoveryRecords(root / 'recovery.db')
    policy = {'schema_version': 1, 'revision': 1, 'enabled': True,
              'permanent_deletion_enabled': False, 'default_retention': '7_days',
              'protected': {'mail@rsalehin24.me': 'Administrator verification emails'}}
    atomic_json(root / 'recovery-policy.json', policy)
    qualify_fixture(root)
    control = RecoveryControl(root, records, os.getuid())
    fixture.host.helper.dependencies = dataclasses.replace(
        fixture.host.helper.dependencies, recovery=control, backups=BackupControl(root),
    )
    return control


def qualify_fixture(directory):
    root = Path(__file__).resolve().parents[1]
    atomic_json(directory / 'recovery-qualified.json', {
        'schema_version': 1, 'qualified': True, 'scope': 'synthetic isolated tests only',
        'files': {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                  for name in QUALIFIED_FILES},
    })
