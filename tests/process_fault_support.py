"""Kill an actual fixture process only after observing its requested boundary."""

import multiprocessing
import signal


def kill_at_observed_boundary(action, operation, boundary):
    context = multiprocessing.get_context('fork')
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=action, args=(child, operation))
    process.start()
    child.close()
    try:
        if not parent.poll(35):
            raise RuntimeError('Fixture process did not reach the selected crash boundary')
        try:
            observed = parent.recv()
        except EOFError as error:
            raise RuntimeError('Fixture exited before the selected crash boundary') from error
        if observed != boundary:
            raise RuntimeError('Fixture process did not report the selected crash boundary')
        process.kill()
        process.join(timeout=5)
        if process.exitcode != -signal.SIGKILL:
            raise RuntimeError('Fixture SIGKILL was not observed')
    finally:
        parent.close()
        if process.is_alive():
            process.kill()
        process.join(timeout=5)
        process.close()

