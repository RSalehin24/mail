"""Observe deployed artifacts before reporting their qualified archive identities."""

import hashlib
import json
import re
import stat

from host.certificates import Generations, verify_served
from host.docker import Docker
from host.preflight import _configuration_binding
from shared.files import protected_bytes, protected_json
from tools.qualification_archive import ArtifactArchive, ImageIdentity
from tools.qualification_bundle import QualificationBundle
from tools.qualification_installation import InstalledTree, installation_manifest


def running_dashboard_identity(docker: Docker, expected: ImageIdentity) -> dict:
    container = docker.inspect("mail-admin")
    if not container["State"]["Running"]:
        raise ValueError("Qualified dashboard must actually be running")
    images = json.loads(docker.run(("image", "inspect", "--platform", "linux/amd64",
                                   container["Image"])))
    if len(images) != 1 or images[0]["Id"] not in expected.allowed_ids:
        raise ValueError("Running dashboard differs from the qualified image archive")
    image = images[0]
    if image["Architecture"] != "amd64" or image["Os"] != "linux" or (
        image["Config"]["User"] != "10001:10001"
        or container["Config"]["User"] != "10001:10001"
    ):
        raise ValueError("Running dashboard platform or user differs from qualification")
    return dict(container)


def verify_activation(generations: Generations, containers: dict[str, dict]) -> None:
    state = generations.read()
    for name, container in containers.items():
        record = state["consumers"][name]
        generation = record["active_generation"]
        directory, certificate = _active_generation(generations, generation)
        mounts = {entry["Destination"]: entry["Source"] for entry in container["Mounts"]}
        if (
            record["intent"] != "expected_running" or record["state"] != "active"
            or generation != state["desired_generation"]
            or certificate["served_sha256"] != record["served_sha256"]
            or mounts.get("/tls" if name == "mail-admin" else "/data/tls") != str(directory)
        ):
            raise ValueError("Running TLS mounts differ from the validated activation record")
        verify_served(("127.0.0.1", 443 if name == "mail-admin" else 993),
                      certificate["served_sha256"], 5)


def _active_generation(generations: Generations, identifier: str) -> tuple:
    if not isinstance(identifier, str) or re.fullmatch(r"[0-9a-f]{64}", identifier) is None:
        raise ValueError("Invalid active TLS generation identity")
    directory = generations.paths.directory / identifier
    metadata = directory.lstat()
    if (directory.resolve() != directory or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != generations.policy.owner
        or metadata.st_gid != generations.policy.group or stat.S_IMODE(metadata.st_mode) != 0o750):
        raise ValueError("Unsafe active TLS generation directory")
    pair = [protected_bytes(directory / name, generations.policy.owner)
            for name in ("fullchain.pem", "privkey.pem")]
    if hashlib.sha256(pair[0] + b"\0" + pair[1]).hexdigest() != identifier:
        raise ValueError("Active TLS pair differs from its generation identity")
    return directory, generations.validator.validate(directory)


class DeploymentObservation:
    def __init__(
        self, bundle: QualificationBundle,
        dependencies: tuple[Docker, InstalledTree, Generations],
    ) -> None:
        self.bundle = bundle
        self.docker, self.tree, self.generations = dependencies

    def observe(self) -> dict[str, str]:
        self.bundle.verify_artifacts()
        root, evidence = self.bundle.context["root"], self.bundle.context["evidence"]
        effective = protected_json(self.tree.root / "etc/mail-admin/deployment.json")
        _configuration_binding(effective, (root, evidence))
        with ArtifactArchive(self.bundle.artifacts["host_bundle"]) as archive:
            manifest = installation_manifest(archive, effective)
        self.tree.verify(manifest)
        dashboard = running_dashboard_identity(self.docker, self.bundle.image)
        maddy = self.docker.inspect("maddy")
        if not maddy["State"]["Running"] or maddy["Image"] != effective["inputs"]["maddy_image_id"]:
            raise ValueError("Running Maddy image differs from the qualified configuration")
        verify_activation(self.generations, {"mail-admin": dashboard, "maddy": maddy})
        self.bundle.verify_artifacts()
        return {**{record["id"]: record["sha256"] for record in evidence["artifacts"]
                   if record["id"] != "qualification_suite"}, "maddy_image_id": maddy["Image"]}
