import unittest

from shared.errors import ServiceError
from shared.files import state_owner_uid

DASHBOARD_UID = 10001


class StateOwnerTests(unittest.TestCase):
    def test_owner_defaults_to_root_so_existing_deployments_are_unchanged(self):
        self.assertEqual(state_owner_uid({}), 0)

    def test_a_configured_uid_is_used(self):
        self.assertEqual(state_owner_uid({"state_owner_uid": DASHBOARD_UID}), DASHBOARD_UID)

    def test_malformed_owners_are_rejected(self):
        for value in (-1, "10001", 1.5, True, None):
            with self.subTest(value=value), self.assertRaises(ServiceError) as caught:
                state_owner_uid({"state_owner_uid": value})
            self.assertEqual(caught.exception.code, "invalid_state_owner")
