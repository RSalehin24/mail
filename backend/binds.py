"""Validate required public-runtime bind types before loading workers or serving HTTP."""

from pathlib import Path

from shared.errors import ServiceError

DIRECTORIES = ('app/data', 'run/mail-admin-helper', 'run/secrets/mail-admin', 'tls')


def require_runtime_sources(root: Path = Path('/')) -> None:
    for relative in DIRECTORIES:
        path = root / relative
        if path.is_symlink() or not path.is_dir():
            raise ServiceError('runtime_bind_type_mismatch', 503)
    marker = root / 'run/mail-admin-installation.json'
    if marker.is_symlink() or not marker.is_file():
        raise ServiceError('runtime_bind_type_mismatch', 503)
