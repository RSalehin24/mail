import multiprocessing
import signal
import unittest

from tests.process_fault_support import kill_at_observed_boundary


def observed_child(channel, boundary):
    channel.send(boundary)
    signal.pause()


def exited_child(channel, boundary):
    channel.close()


class ProcessFaultSupportTests(unittest.TestCase):
    def assert_no_children_left(self, operation):
        before = {child.pid for child in multiprocessing.active_children()}
        try:
            operation()
        finally:
            self.assertEqual({child.pid for child in multiprocessing.active_children()}, before)

    def test_real_sigkill_requires_the_exact_observed_barrier_and_closes_its_child(self):
        boundary = ('staged', 'fullchain.pem')
        self.assert_no_children_left(lambda: kill_at_observed_boundary(
            observed_child, boundary, boundary))

    def test_wrong_barrier_fails_and_cannot_leave_a_paused_process(self):
        with self.assertRaisesRegex(RuntimeError, 'selected crash boundary'):
            self.assert_no_children_left(lambda: kill_at_observed_boundary(
                observed_child, ('unexpected', None), ('published', None)))

    def test_child_exit_cannot_be_mistaken_for_the_requested_fault(self):
        with self.assertRaisesRegex(RuntimeError, 'selected crash boundary'):
            self.assert_no_children_left(lambda: kill_at_observed_boundary(
                exited_child, ('unexpected', None), ('published', None)))
