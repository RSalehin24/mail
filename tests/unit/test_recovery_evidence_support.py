import unittest
from types import SimpleNamespace

from tests.recovery_evidence_support import ReplacementEvidence
from tools.run_tests import source_digest


class RecoveryEvidenceBindingTests(unittest.TestCase):
    def test_development_foreign_artifacts_and_changed_source_never_fill_recovery_acceptance(self):
        reader = ReplacementEvidence.__new__(ReplacementEvidence)
        reader.bundle = SimpleNamespace(identity={'artifact_set_sha256': 'a' * 64,
            'specification_sha256': 'b' * 64, 'runner_revision': 'c' * 64})
        scope = 'frozen-disposable-replacement-recovery'
        original = {**reader.bundle.identity, 'scope': scope, 'source_sha256': source_digest()}
        self.assertEqual(reader.bound(original, scope), original)
        changes = ({'scope': 'development; not acceptance'},
                   {'artifact_set_sha256': 'd' * 64}, {'runner_revision': 'e' * 64},
                   {'source_sha256': 'f' * 64})
        for changed in changes:
            with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                reader.bound({**original, **changed}, scope)
