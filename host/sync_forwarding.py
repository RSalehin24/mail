"""Owner command: apply the saved forwarding rules to Cloudflare now, run as root on the VM.

    sudo python3 -m host.sync_forwarding

The dashboard does this itself after every change; this is for after a restore or a Cloudflare fix.
"""

from pathlib import Path

from helper.cloudflare_forwarding import ForwardingApplier, ForwardingPaths
from helper.forwarding import ForwardingControl
from helper.maddy import DockerMaddy
from shared.files import protected_json, state_owner_uid

STATE = Path("/var/lib/mail-admin-helper")
DEPLOYMENT = Path("/etc/mail-admin/deployment.json")


def sync_forwarding() -> None:
    deployment = protected_json(DEPLOYMENT)
    # The record belongs to the state owner (the dashboard user), even when root writes it.
    control = ForwardingControl(
        STATE, Path(deployment["maddy_data_directory"]), state_owner_uid(deployment))
    ForwardingApplier(control, DockerMaddy(deployment), ForwardingPaths()).apply()


if __name__ == "__main__":
    sync_forwarding()
    print("Forwarding applied; see the dashboard's Forwarding page for the result.")
