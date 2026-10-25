"""Record and verify the existing workstation browser binaries without downloading replacements."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

INPUTS = Path(__file__).with_name('browser-fixture.json')


def pinned_inputs():
    return json.loads(INPUTS.read_bytes())


def executable_identity(path):
    with path.open('rb') as reader:
        return {'path': str(path), 'sha256': hashlib.file_digest(reader, 'sha256').hexdigest()}


def version(path):
    result = subprocess.run(  # noqa: S603 -- binaries verified before execution.
        (str(path), '--version'), capture_output=True, text=True, timeout=10, check=True,
    )
    return result.stdout.strip()


def runner_inputs():
    expected = pinned_inputs()
    node = shutil.which('node')
    if node is None:
        raise RuntimeError('The pinned existing browser driver is unavailable')
    paths = {'node': Path(node).resolve(),
             'chrome': Path(os.environ.get('MAIL_ADMIN_CHROME', expected['chrome']['path']))}
    paths['chrome_framework'] = (paths['chrome'].parents[1] / 'Frameworks'
        / 'Google Chrome Framework.framework/Versions/Current/Google Chrome Framework').resolve()
    observed = {role: executable_identity(path) for role, path in paths.items()}
    files = {role: {key: value for key, value in entry.items() if key != 'version'}
             for role, entry in expected.items()}
    if observed != files:
        raise RuntimeError('Browser or driver files differ from the frozen runner inputs')
    observed['node']['version'] = version(paths['node'])
    observed['chrome']['version'] = version(paths['chrome'])
    if observed != expected:
        raise RuntimeError('Browser or driver binaries differ from the frozen runner inputs')
    return observed
