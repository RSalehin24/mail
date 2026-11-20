"""Root-only helper service, using root-controlled operator records."""

import os
from pathlib import Path

from helper.dependencies import DEPLOYMENT_RECORD, build_dependencies
from helper.maddy import DockerMaddy
from helper.server import HelperServer, SocketPolicy
from helper.service import HostHelper
from shared.errors import ServiceError
from shared.files import protected_json
from shared.validation import HELPER_PROTOCOL, SCHEMA_VERSION


def main() -> None:
    if os.geteuid() != 0:
        raise ServiceError("root_required", 403)
    deployment = protected_json(DEPLOYMENT_RECORD)
    if (
        deployment.get("helper_protocol") != HELPER_PROTOCOL
        or deployment.get("schema_version") != SCHEMA_VERSION
    ):
        raise ServiceError("incompatible_deployment", 503)
    inputs = deployment["inputs"]
    dependencies = build_dependencies(deployment, DockerMaddy(deployment))
    policy = SocketPolicy(
        inputs["service_uid"], inputs["service_gid"], inputs["operation_timeout_seconds"]
    )
    with HelperServer(
        Path("/run/mail-admin-helper/control.sock"), (HostHelper(dependencies), policy)
    ) as server:
        server.serve_forever(poll_interval=0.25)


if __name__ == "__main__":
    main()
