"""Validate required public-runtime bind types before loading workers or serving HTTP."""

from pathlib import Path

from shared.errors import ServiceError

MARKER = 'run/mail-admin-installation.json'
# The dashboard works on Maddy's files itself, so these mounts are all it needs from the host.
DIRECTORIES = (
    'app/data', 'run/secrets/mail-admin', 'data', 'var/lib/mail-admin-helper',
    'run/mail-admin-host/etc', 'var/lib/mail-admin-backup', 'var/lib/mail-admin-alerts',
    'var/lib/mail-admin-certificates',
)
FILES = (
    MARKER, 'data/maddy.conf', 'run/lock/mail-admin-maddy.lock',
    'run/mail-admin-host/etc/deployment.json', 'run/mail-admin-host/etc/helper-policy.json',
)


def require_runtime_sources(root: Path = Path('/'), certificates: tuple[Path, ...] = ()) -> None:
    """Check the mounts; `certificates` are the absolute paths of the HTTPS certificate and key."""
    for relative in DIRECTORIES:
        path = root / relative
        if path.is_symlink() or not path.is_dir():
            raise ServiceError('runtime_bind_type_mismatch', 503)
    required = [root / relative for relative in FILES]
    required += [root / certificate.relative_to('/') for certificate in certificates]
    for path in required:
        if path.is_symlink() or not path.is_file():
            raise ServiceError('runtime_bind_type_mismatch', 503)
