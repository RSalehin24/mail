"""Root recovery entry point independent of the failed dashboard and original server."""

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from host.docker import Docker
from host.image_tools import ImageTools
from host.locks import HostLock, LockPolicy
from host.preflight import provision_lock
from host.restic_process import ResticProcess
from host.restic_repository import ResticRepository
from host.restore import Restore
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json


def prepare_state(configuration: dict[str, Any]) -> None:
    for key in ("state_directory", "staging_directory"):
        path = Path(configuration["backup_restic"][key])
        if not path.is_absolute() or path.is_symlink():
            raise ServiceError("unsafe_restic_path", 503)
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.stat().st_uid != 0 or path.stat().st_mode & 0o777 != 0o700:
            raise ServiceError("unsafe_restic_state", 503)
    state = Path(configuration["backup_restic"]["state_directory"])
    provision_lock(state / "command.lock")
    provision_lock(state / "operator.lock")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    actions.add_parser("initialize")
    actions.add_parser("reconcile")
    restore = actions.add_parser("restore")
    restore.add_argument("snapshot")
    restore.add_argument("destination", type=Path)
    prepare = actions.add_parser("prepare")
    prepare.add_argument("destination", type=Path)
    recover = actions.add_parser("recover")
    recover.add_argument("destination", type=Path)
    arguments = parser.parse_args()
    if os.geteuid() != 0:
        raise ServiceError("root_required", 403)
    configuration = protected_json(arguments.profile)
    prepare_state(configuration)
    process = ResticProcess(configuration)
    repository = ResticRepository(process, time.time)
    with HostLock(process.paths.state / "operator.lock", LockPolicy(1)):
        if arguments.action == "initialize":
            identifier = repository.initialize()
            configuration["backup_restic"]["repository_id"] = identifier
            atomic_json(arguments.profile, configuration)
            print(json.dumps({"state": "initialized", "repository_id": identifier}))
        elif arguments.action == "reconcile":
            process.reconcile()
            print(json.dumps({"state": "reconciled", "mutation_replay": False}))
        elif arguments.action == "list":
            points = [{"snapshot": row.identifier, "cutoff": row.cutoff}
                      for row in repository.snapshots()]
            print(json.dumps({"snapshots": points}))
        elif arguments.action in {"prepare", "recover"}:
            destination = arguments.destination.absolute()
            deployment = protected_json(destination / "operator/deployment.json")
            tools = ImageTools(Docker(), deployment)
            operation = Restore(repository)
            action = (operation.prepare_authentication if arguments.action == "prepare"
                      else operation.recover_authentication)
            print(json.dumps(action(destination, tools)))
        else:
            if not re.fullmatch(r"[0-9a-f]{64}", arguments.snapshot):
                raise ServiceError("exact_snapshot_id_required", 400)
            destination = arguments.destination.absolute()
            outcome = Restore(repository).stage(
                repository.reference(arguments.snapshot), destination,
            )
            print(json.dumps(outcome))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ServiceError as error:
        print(json.dumps({"error": error.code}))
        raise SystemExit(1) from None
    except OSError as error:
        print(json.dumps({"error": "recovery_io_failed", "errno": error.errno}))
        raise SystemExit(1) from None
