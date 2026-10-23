import unittest

from tests.recovery_objective_support import qualification_objectives


class RecoveryObjectiveTests(unittest.TestCase):
    def test_selected_limits_include_the_exact_boundary_and_reject_each_excess(self):
        limits = {'recovery_point_seconds': 93600, 'recovery_time_seconds': 14400}
        self.assertEqual(qualification_objectives(limits, (0, 93600, 108000))['result'], 'pass')
        self.assertEqual(qualification_objectives(limits, (0, 93601, 108000))['result'], 'fail')
        self.assertEqual(qualification_objectives(limits, (0, 93600, 108001))['result'], 'fail')

    def test_unknown_future_or_nonfinite_times_never_establish_recovery(self):
        limits = {'recovery_point_seconds': 93600, 'recovery_time_seconds': 14400}
        for timeline in ((10, 9, 11), (10, 11, 10), (0, 1, float('nan')),
                         (0, 1, float('inf')), (False, 1, 2), (None, 1, 2)):
            with self.subTest(timeline=timeline), self.assertRaises(RuntimeError):
                qualification_objectives(limits, timeline)
