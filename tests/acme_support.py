"""Real Docker writer with synthetic ACME storage; never performs network acquisition."""

import json
import time
from pathlib import Path

from host.docker import Docker
from shared.errors import ServiceError

WRITER = """import json,os,sys,time
from pathlib import Path
root=Path('/etc/letsencrypt')
def observe():
 values={name:Path('/sys/fs/cgroup',name).read_text() for name in
         ('memory.max','memory.peak','memory.events')}
 temporary=root/'observation.tmp'
 temporary.write_text(json.dumps(values))
 with temporary.open('rb') as reader: os.fsync(reader.fileno())
 temporary.replace(root/'observation.json')
observe()
(root/'started').write_text('synthetic ACME writer')
while not (root/'release').exists():
 with (root/'heartbeat').open('w') as writer:
  writer.write(str(time.monotonic()))
  writer.flush()
  os.fsync(writer.fileno())
 time.sleep(0.05)
observe()
sys.exit(int((root/'exit').read_text()) if (root/'exit').exists() else 0)
"""


class AcmeFixtureDocker:
    def __init__(self, fixture, image):
        self.fixture, self.image = fixture, image
        self.docker = Docker(fixture.docker, timeout=15)
        self.attach_timeout = 15
        self.created = set()
        self.storage = fixture.root / "acme"
        self.library = fixture.root / "acme-library"
        self.storage.mkdir(mode=0o700)
        self.library.mkdir(mode=0o700)

    def run(self, arguments):
        if arguments[0] == "create":
            arguments = self.create_arguments(arguments)
            identifier = self.docker.run(arguments)
            self.created.add(identifier)
            return identifier
        if arguments[:2] == ("start", "--attach"):
            self.require_owned(arguments[2])
            return Docker(self.fixture.docker, self.attach_timeout).run(arguments)
        if arguments[0] == "rm":
            self.require_owned(arguments[1])
            result = self.docker.run(arguments)
            self.created.remove(arguments[1])
            return result
        if arguments[:2] == ("container", "ls"):
            return self.docker.run(arguments)
        raise AssertionError("Unexpected fixture Docker command")

    def create_arguments(self, arguments):
        position = arguments.index(self.image)
        prefix = arguments[:position]
        port = prefix.index("-p")
        prefix = prefix[:port] + prefix[port + 2:]
        substitutions = {
            "type=bind,src=/opt/maddy/letsencrypt,dst=/etc/letsencrypt":
                f"type=bind,src={self.fixture.daemon_path(self.storage)},dst=/etc/letsencrypt",
            "type=bind,src=/opt/maddy/letsencrypt_lib,dst=/var/lib/letsencrypt":
                f"type=bind,src={self.fixture.daemon_path(self.library)},dst=/var/lib/letsencrypt",
        }
        prefix = tuple(substitutions.get(value, value) for value in prefix)
        if any("/opt/maddy" in value for value in prefix):
            raise AssertionError("Production path in isolated writer fixture")
        return prefix + (
            "--platform", "linux/amd64", "--network", "none", "--user", "0:0",
            "--entrypoint", "python",
            self.image, "-c", WRITER,
        )

    def inspect(self, identifier):
        self.require_owned(identifier)
        return self.docker.inspect(identifier)

    def require_owned(self, identifier):
        if identifier not in self.created:
            raise AssertionError("Refusing non-fixture container")

    def wait_for(self, path):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if path.is_file():
                return
            time.sleep(0.05)
        raise AssertionError("Synthetic ACME writer did not reach its boundary")

    def release(self, identifier):
        (self.storage / "release").touch()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.inspect(identifier)["State"]["Status"] == "exited":
                return
            time.sleep(0.1)
        raise AssertionError("Synthetic ACME writer did not exit")

    def close(self):
        for identifier in tuple(self.created):
            record = self.inspect(identifier)
            name = record["Name"].removeprefix("/")
            if record["Config"]["Labels"].get("mail-admin.certbot-run") != name:
                raise ServiceError("fixture_identity_changed", 503)
            self.docker.run(("rm", "--force", identifier))
            self.created.remove(identifier)

    def observations(self):
        self.wait_for(self.storage / "observation.json")
        record = json.loads((self.storage / "observation.json").read_text())
        result = {
            "scope": "synthetic writer, not actual Certbot or whole-host qualification", **record,
        }
        name = self.fixture.container.removeprefix("mail-admin-fixture-")
        (Path("build") / ("acme-writer-" + name + ".json")).write_text(json.dumps(result) + "\n")
        return record
