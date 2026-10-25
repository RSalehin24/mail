import copy
import unittest

from tools.record_replacement_isolation import require_unavailable


class ReplacementIsolationTests(unittest.TestCase):
    def test_restarted_source_running_source_original_disk_or_stopped_target_cannot_qualify(self):
        stopped = {'StartedAt': 'before-outage', 'FinishedAt': 'outage', 'Running': False}
        outage = {'source_initial_stopped_state': stopped}
        source = {'Id': 'source', 'State': stopped,
                  'Mounts': [{'Type': 'volume', 'Name': 'original-disk'}]}
        target = {'Id': 'replacement', 'State': {'Running': True, 'OOMKilled': False},
                  'Mounts': [{'Type': 'volume', 'Name': 'empty-replacement-disk'}]}
        require_unavailable(outage, (source, target))
        changes = (({'State': {**stopped, 'Running': True}}, {}),
                   ({'State': {**stopped, 'StartedAt': 'later-restart'}}, {}),
                   ({}, {'Id': 'source'}), ({}, {'Mounts': source['Mounts']}),
                   ({}, {'State': {'Running': False, 'OOMKilled': False}}),
                   ({}, {'State': {'Running': True, 'OOMKilled': True}}))
        for source_change, target_change in changes:
            with (self.subTest(changes=(source_change, target_change)),
                  self.assertRaises(ValueError)):
                require_unavailable(outage, ({**copy.deepcopy(source), **source_change},
                                             {**copy.deepcopy(target), **target_change}))
