import os
import sys
import unittest

from helper.process import Command, execute
from shared.errors import ServiceError
from tests.support import StateFixture


class ProcessBoundsTests(unittest.TestCase):
    def test_protocol_stdout_is_separate_from_diagnostics_under_the_same_combined_cap(self):
        program = "import sys;print('{\"ok\":true}');sys.stderr.write('diagnostic\\n')"
        result = execute(Command((sys.executable, '-c', program), timeout=5,
                                 merge_stderr=False))
        self.assertEqual(result.output, '{"ok":true}\n')
        self.assertEqual(result.error_output, 'diagnostic\n')
        with self.assertRaises(ServiceError) as caught:
            execute(Command((sys.executable, '-c',
                             "import sys;sys.stdout.write('x'*32768);"
                             "sys.stderr.write('y'*32769)"), timeout=5, merge_stderr=False))
        self.assertEqual(caught.exception.__cause__.code, 'command_output_limit')

    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)

    def test_real_process_accepts_exactly_64_kib_and_rejects_larger_output(self):
        result = execute(Command((sys.executable, '-c',
                                  "import sys;sys.stdout.write('x'*65536)"), timeout=5))
        self.assertEqual((result.code, len(result.output)), (0, 65536))
        with self.assertRaises(ServiceError) as caught:
            execute(Command((sys.executable, '-c',
                             "import sys;sys.stdout.write('x'*65537)"), timeout=5))
        self.assertEqual(caught.exception.code, 'command_completion_unknown')
        self.assertEqual(caught.exception.__cause__.code, 'command_output_limit')

    def test_real_slow_process_is_killed_and_completion_is_never_invented(self):
        record = self.fixture.root / 'child.pid'
        program = ("import os,sys,time\n"
                   "with open(sys.argv[1],'w') as stream:stream.write(str(os.getpid()))\n"
                   "time.sleep(30)\n")
        with self.assertRaises(ServiceError) as caught:
            execute(Command((sys.executable, '-c', program, str(record)), timeout=1))
        self.assertEqual(caught.exception.code, 'command_completion_unknown')
        self.assertEqual(caught.exception.__cause__.code, 'command_deadline')
        with self.assertRaises(ProcessLookupError):
            os.kill(int(record.read_text()), 0)

    def test_actual_nonzero_exit_is_preserved_without_inheriting_parent_secrets(self):
        from unittest.mock import patch

        program = ("import os,sys;print(os.environ.get('FIXTURE_PRIVATE_VALUE','absent'));"
                   "sys.exit(7)")
        with patch.dict('os.environ', {'FIXTURE_PRIVATE_VALUE': 'private-fixture-marker'}):
            result = execute(Command((sys.executable, '-c', program), timeout=5))
        self.assertEqual(result.code, 7)
        self.assertEqual(result.output.strip(), 'absent')
