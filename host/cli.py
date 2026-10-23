"""Root operational entry points. Installation/deployment is separately gated and approved."""

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

from host.alerts import Alerts, alert_transport
from host.backup_operations import BackupOperations
from host.baseline import baseline_backup
from host.certificates import verify_served
from host.context import HostContext
from host.cutover import Cutover
from host.cutover_host import RootCutoverHost
from host.image_tools import ImageTools
from host.legacy import LegacyRenewal
from host.monitor import Monitor
from host.preflight import provision_lock, qualify_preflight, require_approval, require_checkpoint
from host.recovery import Recovery
from host.release_files import ReleaseFiles, RollbackPackage
from host.rollout import (
    RolloutState,
    operational_guard,
    require_private_directory,
    rollout_directory,
    rollout_operation,
)
from shared.errors import ServiceError
from shared.files import protected_json
from shared.runtime_policy import RUNTIME_POLICY_NAME, RuntimePolicy


class OfflineConsumer:
    def __init__(self, context: HostContext) -> None:
        self.context = context
        self.tools = ImageTools(context.docker, context.deployment)
        self.policy = RuntimePolicy(
            Path("/etc/mail-admin/installation.json"),
            Path("/etc/mail-admin/secrets") / RUNTIME_POLICY_NAME,
        )

    def pause_runtime(self) -> None:
        self.policy.pause()

    def stop(self) -> None:
        self.context.docker.stop_for_maintenance("mail-admin")

    def snapshot_authentication(self) -> None:
        directory = Path("/var/lib/mail-admin-recovery") / str(time.time_ns())
        directory.mkdir(mode=0o700)
        arguments = self.tools.authentication_arguments("snapshot")
        insertion = arguments.index(self.context.deployment["dashboard_image_id"])
        arguments = (
            arguments[:insertion]
            + ("--mount", f"type=bind,src={directory},dst=/snapshot")
            + arguments[insertion:]
        )
        self.context.docker.run(arguments)

    def offline_utility(self, name: str) -> None:
        self.tools.offline_utility(name)

    def discard_recovery(self, name: str) -> None:
        self.tools.discard_recovery(name)

    def verify_recovery(self, name: str) -> None:
        self.tools.verify_recovery(name)

    def allow_runtime(self) -> None:
        self.policy.resume()

    def verify_running(self) -> None:
        self.policy.require_running()
        state = self.context.generations.read()
        record = state["consumers"]["mail-admin"]
        container = self.context.docker.inspect("mail-admin")
        generation = self.context.generations.paths.directory / state["desired_generation"]
        mounts = {mount["Destination"]: mount["Source"] for mount in container["Mounts"]}
        if (
            record["intent"] != "expected_running"
            or record["state"] != "active"
            or record["active_generation"] != state["desired_generation"]
            or not container["State"]["Running"]
            or container["Image"] != self.context.deployment["dashboard_image_id"]
            or mounts.get("/tls") != str(generation)
        ):
            raise ServiceError("dashboard_resume_unverified", 503)
        verify_served(
            ("127.0.0.1", self.context.deployment["consumers"]["mail-admin"]["tls_port"]),
            record["served_sha256"],
            self.context.docker.timeout,
        )


def run(action: str, context: HostContext) -> None:
    if action in {"renew", "dry-run", "activate"}:
        require_checkpoint("G05", context.deployment)
        if context.generations.read().get("coordination_migrated") is not True:
            raise ServiceError("matched_renewal_cutover_required", 409)
        renewal = context.renewal()
        if action == "activate":
            renewal.retry_activation()
        else:
            renewal.run(
                Path(context.deployment["certbot_live_directory"]),
                "dry-run" if action == "dry-run" else "renew",
            )
    elif action in {"backup", "backup-verify"}:
        require_checkpoint("G07", context.deployment)
        operation = BackupOperations(context.backup(), context.deployment)
        operation.run() if action == "backup" else operation.verify()
    elif action in {"monitor", "monitor-external"}:
        monitor = Monitor(context.alerts, (context.deployment, time.time))
        monitor.external() if action == "monitor-external" else monitor.host()
    elif action in {"tls-cutover", "rollback"}:
        cutover_action(action, context)
    else:
        recovery_action(action, context)


def cutover_action(action: str, context: HostContext) -> None:
    require_approval(context.deployment, "cutover" if action == "tls-cutover" else "rollback")
    require_checkpoint("G05", context.deployment)
    qualify_preflight(context.deployment)
    configuration = context.deployment["cutover"]
    targets = (
        Path("/opt/maddy/renew-cert.sh"),
        Path("/etc/systemd/system/maddy-cert-renew.service"),
        Path("/etc/systemd/system/maddy-cert-renew.timer"),
        Path(context.deployment["consumers"]["maddy"]["compose_file"]),
    )
    files = ReleaseFiles(configuration["files"], (0, targets))
    directory = rollout_directory(context.deployment)
    operation = Cutover(
        context.coordination,
        (
            RootCutoverHost(context, files),
            RollbackPackage(
                directory / "rollback-set", files, context.docker
            ),
            directory / "cutover.json",
        ),
    )
    with rollout_operation(context.deployment):
        RolloutState(context.deployment, (directory.parent, 0)).require_selected()
        operation.run() if action == "tls-cutover" else operation.rollback()


def recovery_action(action: str, context: HostContext) -> None:
    require_approval(context.deployment, "recovery")
    operation = Recovery(
        context.coordination,
        (
            context.generations,
            OfflineConsumer(context),
            Path("/var/lib/mail-admin-recovery/outcome.json"),
        ),
    )
    if action == "recover":
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ServiceError("private_terminal_required", 403)
        operation.run()
        return
    operation.resume(context.renewal().retry_activation)


def baseline_action(action: str) -> int:
    deployment = protected_json(Path("/etc/mail-admin/deployment.json"))
    qualify_preflight(deployment)
    if action == "preflight":
        return 0
    guard = operational_guard(deployment)
    phase = {"maintenance-entry": "maintenance", "baseline-backup": "baseline",
             "legacy-reconcile": "legacy-reconciliation"}[action]
    approval = guard.approval(phase)
    guard.checkpoint("G03" if action == "baseline-backup" else "G02")
    directory = rollout_directory(deployment)
    if action == "maintenance-entry":
        prepare_maintenance(directory)
    with rollout_operation(deployment):
        state = RolloutState(deployment, (directory.parent, 0))
        if action == "maintenance-entry":
            state.select()
        else:
            state.require_selected()
        baseline_operation(action, (deployment, approval, directory))
    return 0


def baseline_operation(action: str, records: tuple[dict[str, Any], dict[str, Any], Path]) -> None:
    deployment, approval, directory = records
    if action == "legacy-reconcile":
        LegacyRenewal(directory / "legacy.json", 30).reconcile_manual()
    elif action == "baseline-backup":
        BackupOperations(baseline_backup(deployment), deployment).baseline()
    else:
        LegacyRenewal(directory / "legacy.json", 30).enter(approval)


def prepare_maintenance(directory: Path) -> None:
    for path in (directory.parent, directory, Path("/var/lib/mail-admin-backup/staging")):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        require_private_directory(path, 0)
    for name in ("renewal", "maddy", "backup"):
        provision_lock(Path("/run/lock/mail-admin-" + name + ".lock"))
    provision_lock(directory.parent / "operation.lock")
    provision_lock(directory / "cutover.lock")


def external_monitor() -> int:
    configuration = protected_json(Path("/etc/mail-admin/external-monitor.json"))
    alerts = Alerts(
        Path("/var/lib/mail-admin-alerts/external.json"),
        (alert_transport(configuration["alert_configuration"]), time.time),
    )
    Monitor(alerts, (configuration, time.time)).external()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "preflight",
            "maintenance-entry",
            "legacy-reconcile",
            "baseline-backup",
            "tls-cutover",
            "rollback",
            "renew",
            "dry-run",
            "activate",
            "backup",
            "backup-verify",
            "monitor",
            "monitor-external",
            "recover",
            "recover-resume",
        ),
    )
    arguments = parser.parse_args()
    if os.geteuid() != 0:
        raise ServiceError("root_required", 403)
    if arguments.action == "monitor-external":
        return external_monitor()
    if arguments.action in {
        "preflight", "maintenance-entry", "baseline-backup", "legacy-reconcile"
    }:
        return baseline_action(arguments.action)
    context = HostContext.load()
    try:
        run(arguments.action, context)
        context.alerts.observe(arguments.action, "recovered")
    except (OSError, ServiceError):
        context.alerts.observe(arguments.action, "failed_or_deferred")
        context.alerts.flush()
        return 1
    context.alerts.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
