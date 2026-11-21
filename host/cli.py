"""Root operational entry points. Installation/deployment is separately gated and approved."""

import argparse
import os
import sys
import time
from pathlib import Path

from host.alerts import Alerts, alert_transport
from host.backup_operations import BackupOperations
from host.certificates import served_certificate
from host.context import HostContext
from host.image_tools import ImageTools
from host.monitor import Monitor
from host.recovery import Recovery
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

    def start(self) -> None:
        self.context.docker.run(("start", "mail-admin"))

    def verify_running(self) -> None:
        self.policy.require_running()
        container = self.context.docker.inspect("mail-admin")
        if (
            not container["State"]["Running"]
            or container["Image"] != self.context.deployment["dashboard_image_id"]
        ):
            raise ServiceError("dashboard_resume_unverified", 503)
        port = self.context.deployment["consumers"]["mail-admin"]["tls_port"]
        served_certificate(("127.0.0.1", port))


def run(action: str, context: HostContext) -> None:
    if action in {"backup", "backup-verify"}:
        operation = BackupOperations(context.backup(), context.deployment)
        if action == 'backup' and Path('/var/lib/mail-admin-helper/backup-control.db').is_file():
            from host.backup_tracking import tracked_backup
            tracked_backup(context)
        else:
            operation.run() if action == "backup" else operation.verify()
    elif action in {"monitor", "monitor-external"}:
        monitor = Monitor(context.alerts, (context.deployment, time.time),
                          Path("/var/lib/mail-admin-alerts"))
        monitor.external() if action == "monitor-external" else monitor.host()
    else:
        recovery_action(action, context)


def recovery_action(action: str, context: HostContext) -> None:
    operation = Recovery(
        context.coordination,
        (
            context.intents,
            OfflineConsumer(context),
            Path("/var/lib/mail-admin-recovery/outcome.json"),
        ),
    )
    if action == "recover":
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ServiceError("private_terminal_required", 403)
        operation.run()
        return
    operation.resume(OfflineConsumer(context).start)


def external_monitor() -> int:
    configuration = protected_json(Path("/etc/mail-admin/external-monitor.json"))
    alerts = Alerts(
        Path("/var/lib/mail-admin-alerts/external.json"),
        (alert_transport(configuration["alert_configuration"]), time.time),
    )
    Monitor(alerts, (configuration, time.time),
            Path("/var/lib/mail-admin-alerts")).external()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
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
    context = HostContext.load()
    try:
        run(arguments.action, context)
        context.alerts.observe(arguments.action, "recovered")
    except (OSError, ServiceError) as error:
        # The alert only says the action failed; the journal needs the reason.
        print(f"{arguments.action} failed: {type(error).__name__}: {error}", file=sys.stderr)
        context.alerts.observe(arguments.action, "failed_or_deferred")
        context.alerts.flush()
        return 1
    context.alerts.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
