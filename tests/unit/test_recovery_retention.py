import unittest

from helper.retention import RecoveryPolicy, recovery_expired
from shared.errors import ServiceError


class RecoveryRetentionTests(unittest.TestCase):
    def policy(self, **changes):
        return RecoveryPolicy.parse({
            "schema_version": 1, "revision": 1, "enabled": True,
            "permanent_deletion_enabled": False, "default_retention": "7_days",
            **changes,
        })

    def test_exact_retention_and_expiry_boundary(self):
        for choice, days in (("7_days", 7), ("30_days", 30)):
            deadline = self.policy().deadline(choice, 1000)
            self.assertEqual(deadline, 1000 + days * 86400)
            self.assertFalse(recovery_expired(deadline, deadline - 1))
            self.assertTrue(recovery_expired(deadline, deadline))

    def test_forever_never_expires(self):
        deadline = self.policy().deadline("forever", 1000)
        self.assertIsNone(deadline)
        self.assertFalse(recovery_expired(deadline, 2**63))

    def test_disabled_policy_refuses_every_retention(self):
        for choice in ("7_days", "30_days", "forever"):
            with self.assertRaises(ServiceError):
                self.policy(enabled=False).deadline(choice, 1000)

    def test_invalid_policy_and_browser_retention_refused(self):
        for changes in ({"revision": True}, {"schema_version": 2},
                        {"enabled": "true"}, {"default_retention": "365_days"}):
            with self.assertRaises(ServiceError):
                self.policy(**changes)
        with self.assertRaises(ServiceError):
            self.policy().deadline("365_days", 1000)

    def test_dont_retain_requires_permanent_deletion_and_has_no_recovery_window(self):
        with self.assertRaisesRegex(ServiceError, 'permanent_deletion_disabled'):
            self.policy().deadline('none', 1000)
        with self.assertRaises(ServiceError):
            self.policy(default_retention='none')
        policy = self.policy(permanent_deletion_enabled=True, default_retention='none')
        self.assertEqual(policy.deadline('none', 1000), 1000)
