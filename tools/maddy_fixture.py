"""Approved immutable SMTP correction for synthetic fixtures; production stays separately pinned."""

import json
import subprocess

MADDY_FIXTURE_IMAGE = 'sha256:11c98cb08dc68166b4238527b00088e0d8be175e9fb9528c210b45600d43bfaa'
MADDY_FIXTURE_CONFIGURATION = (
    'sha256:3e711e7fa1295e4e16e7170e1900ee555d192b9a1147f0934aefbbff758b1f6c'
)
LINUX_MADDY_FIXTURE_IMAGE = (
    'sha256:c332b48a8a4cc614c314561d5769cf2e5d2bd91c66f9790826abd132141f8489'
)
LINUX_MADDY_FIXTURE_CONFIGURATION = (
    'sha256:c0e7acee7b21b4af2bd84dfb622a22de5eec6f51206f9178bcbe310216196904'
)
MADDY_FIXTURE_REFERENCES = (
    MADDY_FIXTURE_IMAGE, MADDY_FIXTURE_CONFIGURATION,
    LINUX_MADDY_FIXTURE_IMAGE, LINUX_MADDY_FIXTURE_CONFIGURATION,
)
MADDY_FIXTURE_IDENTITIES = set(MADDY_FIXTURE_REFERENCES)


def fixture_reference():
    # Containerd retains the OCI index; Docker's classic store exposes its configuration ID.
    for reference in MADDY_FIXTURE_REFERENCES:
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
