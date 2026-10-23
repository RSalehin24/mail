"""Terminate a real helper process only after an observed durable mutation boundary."""

import multiprocessing
import signal
from unittest.mock import patch


def kill_at_boundary(helper, request, boundary):
    context = multiprocessing.get_context('fork')
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_run, args=(child, (helper, request, boundary)))
    process.start()
    child.close()
    try:
        if not parent.poll(35):
            raise RuntimeError('Helper did not reach the selected crash boundary')
        if parent.recv() != boundary:
            raise RuntimeError('Helper exited before the selected crash boundary')
        process.kill()
        process.join(timeout=5)
        if process.exitcode != -signal.SIGKILL:
            raise RuntimeError('Helper SIGKILL was not observed')
    finally:
        parent.close()
        if process.is_alive():
            process.kill()
        process.join(timeout=5)
        process.close()


def _run(channel, operation):
    helper, request, boundary = operation
    observer = CrashObserver(channel, boundary)
    begin, update, change = helper.journal.begin, helper.journal.update, helper.maddy.change

    def begin_observed(*arguments):
        record = begin(*arguments)
        observer.reached(('intent', None))
        return record

    def update_observed(identifier, values):
        record = update(identifier, values)
        observer.journaled(values)
        return record

    def change_observed(arguments, password=''):
        response = change(arguments, password)
        observer.executed(request.operation, arguments)
        return response

    with (patch.object(helper.journal, 'begin', begin_observed),
          patch.object(helper.journal, 'update', update_observed),
          patch.object(helper.maddy, 'change', change_observed)):
        helper.handle(request)
    channel.send(('unexpected_completion', None))


class CrashObserver:
    def __init__(self, channel, boundary):
        self.channel, self.boundary = channel, boundary

    def reached(self, boundary):
        if self.boundary == boundary:
            self.channel.send(boundary)
            signal.pause()
            raise RuntimeError('Selected crash boundary unexpectedly resumed')

    def journaled(self, values):
        if values.get('phase'):
            self.reached(('before_cli', values['phase']))
        if values.get('steps'):
            self.reached(('after_record', values['steps'][-1]['step']))
        if 'postconditions' in values:
            self.reached(('observed', None))
        if values.get('state') in {'complete', 'failed'}:
            self.reached(('terminal', None))

    def executed(self, operation, arguments):
        labels = {('creds', 'create'): 'credentials-created',
                  ('creds', 'password'): 'password-changed',
                  ('imap-acct', 'create'): 'storage-created',
                  ('imap-acct', 'remove'): 'storage-removed',
                  ('creds', 'remove'): ('credentials-removed' if operation == 'delete'
                                        else 'credentials-rolled-back')}
        if arguments[:2] in labels:
            self.reached(('after_cli', labels[arguments[:2]]))
