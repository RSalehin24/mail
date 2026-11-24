"""The dashboard runs the host operations itself, with the same code as the root helper."""

import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol

from helper.cloudflare_forwarding import ForwardingPaths
from helper.deletion import DeletionPaths
from helper.dependencies import HostLocations, build_dependencies
from helper.journal import Journal
from helper.local_relay import local_relay_actions
from helper.maddy import LocalMaddy
from helper.protocol import HelperRequest
from helper.service import HostHelper
from helper.state_database import STATE_FILE
from helper.status import HostStatusPaths
from host.preflight import provision_lock
from shared.errors import ServiceError
from shared.files import protected_json
from shared.validation import HELPER_PROTOCOL

# Container mount points; the compose file maps the host's Maddy and record files here.
MADDY_BINARY = Path("/usr/local/bin/maddy")
MADDY_DATA = Path("/data")
APPLICATION_DATA = Path("/app/data")
# Root writes these records and workers replace them atomically, so the compose file mounts their
# directories (a single-file mount would keep the old inode) read-only.
ETC_RECORDS = Path("/run/mail-admin-host/etc")
BACKUP_MANIFEST = Path("/var/lib/mail-admin-backup/manifest.json")
DEFAULT_CLI_VERSION = "0.9.5"
# The recovery worker is started by a path unit watching this file. It must not watch state.db:
# the worker opens that database on every run, so it would start itself again forever.
RECOVERY_REQUEST = "recovery.request.json"

# State and the mailbox lock are the host paths the root workers also use, bind-mounted
# into the container and owned by the dashboard's user (the deployment's state_owner_uid).
LOCAL_LOCATIONS = HostLocations(
    state=Path("/var/lib/mail-admin-helper"),
    lock=Path("/run/lock/mail-admin-maddy.lock"),
    owner=os.geteuid(),
    deletion=DeletionPaths(
        ETC_RECORDS / "deployment.json", ETC_RECORDS / "helper-policy.json", BACKUP_MANIFEST
    ),
    # The alert and certificate observations sit at their host paths; only these two differ.
    status=HostStatusPaths(maddy_data=MADDY_DATA, deployment=ETC_RECORDS / "deployment.json"),
    in_process=True,
    forwarding=ForwardingPaths(
        ETC_RECORDS / "cloudflare-forwarding.json",
        Path("/run/secrets/mail-admin/cloudflare-forwarding.token"),
    ),
)


class HelperAccess(Protocol):
    def call(self, request: dict[str, Any]) -> dict[str, Any]: ...


class LocalHost:
    """Same call interface as the socket client; there is no lost response to look up."""

    def __init__(self, helper: HostHelper) -> None:
        self.helper = helper

    def call(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.helper.handle(HelperRequest.parse({"protocol": HELPER_PROTOCOL, **request}))
        except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
            # What the socket server used to turn into a safe 503: a host failure is never a 500.
            raise ServiceError("helper_unavailable", 503) from error


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
    if not (locations.state / STATE_FILE).exists():
        Journal.provision(locations.state / STATE_FILE)
    relay = local_relay_actions(maddy)
    return LocalHost(
        HostHelper(build_dependencies(deployment, maddy, replace(locations, relay=relay)))
    )
