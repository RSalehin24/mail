"""Approved immutable SMTP correction for synthetic fixtures; production stays separately pinned."""

import json
import subprocess

MADDY_FIXTURE_IMAGE = 'sha256:11c98cb08dc68166b4238527b00088e0d8be175e9fb9528c210b45600d43bfaa'
MADDY_FIXTURE_CONFIGURATION = (
    'sha256:3e711e7fa1295e4e16e7170e1900ee555d192b9a1147f0934aefbbff758b1f6c'
)
MADDY_FIXTURE_IDENTITIES = {MADDY_FIXTURE_IMAGE, MADDY_FIXTURE_CONFIGURATION}


def fixture_reference():
    # Containerd retains the OCI index; Docker's classic store exposes its configuration ID.
    for reference in (MADDY_FIXTURE_IMAGE, MADDY_FIXTURE_CONFIGURATION):
        result = subprocess.run(  # noqa: S603 -- fixed approved immutable image identities.
            ('docker', 'image', 'inspect', reference),  # noqa: S607 -- existing fixture Docker CLI.
            capture_output=True, text=True,
            timeout=30, check=False,
        )
        if result.returncode:
            continue
        image = json.loads(result.stdout)[0]
        if (image['Id'] not in MADDY_FIXTURE_IDENTITIES
                or (image['Os'], image['Architecture']) != ('linux', 'amd64')):
            raise RuntimeError('The isolated Maddy image differs from the approved build')
        return image['Id']
    raise RuntimeError('The approved isolated Maddy image must be loaded before fixture work')
