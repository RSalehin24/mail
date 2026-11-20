"""The dashboard runs the host operations itself, with the same code as the root helper."""

import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from helper.deletion import DeletionPaths
from helper.dependencies import HostLocations, build_dependencies
from helper.journal import Journal
from helper.maddy import LocalMaddy
from helper.maddy_restart import local_relay_actions
from helper.protocol import HelperRequest
from helper.service import HostHelper
from host.preflight import provision_lock
from shared.files import protected_json
from shared.validation import HELPER_PROTOCOL

# Container mount points; the compose file maps the host's Maddy and record files here.
MADDY_BINARY = Path("/usr/local/bin/maddy")
MADDY_DATA = Path("/data")
APPLICATION_DATA = Path("/app/data")
HOST_RECORDS = Path("/run/mail-admin-host")
DEFAULT_CLI_VERSION = "0.9.5"

# State and the mailbox lock are the host paths the root workers also use, bind-mounted
# into the container and owned by the dashboard's user (the deployment's state_owner_uid).
LOCAL_LOCATIONS = HostLocations(
    state=Path("/var/lib/mail-admin-helper"),
    lock=Path("/run/lock/mail-admin-maddy.lock"),
    owner=os.geteuid(),
    deletion=DeletionPaths(
        HOST_RECORDS / "deployment.json",
        HOST_RECORDS / "helper-policy.json",
        HOST_RECORDS / "backup-manifest.json",
    ),
)


class LocalHost:
    """Same call interface as the socket client; there is no lost response to look up."""

    def __init__(self, helper: HostHelper) -> None:
        self.helper = helper

    def call(self, request: dict[str, Any]) -> dict[str, Any]:
        return self.helper.handle(HelperRequest.parse({"protocol": HELPER_PROTOCOL, **request}))


def create_local_host(timeout: float, locations: HostLocations = LOCAL_LOCATIONS) -> LocalHost:
    record = protected_json(locations.deletion.deployment)
    deployment = {
        **record,
        "maddy_data_directory": str(MADDY_DATA),
        "backup_sources": {**record["backup_sources"], "dashboard": str(APPLICATION_DATA)},
    }
    maddy = LocalMaddy(
        MADDY_BINARY, MADDY_DATA / "maddy.conf", MADDY_DATA,
        str(record.get("maddy_cli_version", DEFAULT_CLI_VERSION)), timeout,
    )
    locations.state.mkdir(mode=0o700, exist_ok=True)
    provision_lock(locations.lock, locations.owner)
    if not (locations.state / "journal.db").exists():
        Journal.provision(locations.state / "journal.db")
    relay = local_relay_actions(maddy, locations.state, locations.owner)
    return LocalHost(HostHelper(build_dependencies(
        deployment, maddy, replace(locations, relay=relay))))
