"""Validate required public-runtime bind types before loading workers or serving HTTP."""

from pathlib import Path

from shared.errors import ServiceError

MARKER = 'run/mail-admin-installation.json'
DIRECTORIES = ('app/data', 'run/mail-admin-helper', 'run/secrets/mail-admin', 'tls')
FILES = (MARKER,)
# With LOCAL_HOST the dashboard works on Maddy's files itself instead of reaching a helper socket.
LOCAL_DIRECTORIES = (
    'app/data', 'run/secrets/mail-admin', 'tls', 'data', 'data/tls', 'var/lib/mail-admin-helper',
)
LOCAL_FILES = (
    MARKER, 'data/maddy.conf', 'run/lock/mail-admin-maddy.lock',
    'run/mail-admin-host/deployment.json', 'run/mail-admin-host/helper-policy.json',
    'run/mail-admin-host/backup-manifest.json',
)


def require_runtime_sources(root: Path = Path('/'), local_host: bool = False) -> None:
    directories, files = (LOCAL_DIRECTORIES, LOCAL_FILES) if local_host else (DIRECTORIES, FILES)
    for relative in directories:
        path = root / relative
        if path.is_symlink() or not path.is_dir():
            raise ServiceError('runtime_bind_type_mismatch', 503)
    for relative in files:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ServiceError('runtime_bind_type_mismatch', 503)
