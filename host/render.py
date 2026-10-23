"""Render operator-selected unit inputs without installing or starting services."""

import argparse
import re
from pathlib import Path

from shared.errors import ServiceError
from shared.files import protected_json


def rendered_files(
    configuration: dict[str, object], sources: dict[str, str]
) -> dict[str, bytes]:
    inputs = configuration["inputs"]
    if not isinstance(inputs, dict) or type(inputs.get("service_gid")) is not int:
        raise ServiceError("service_group_required", 400)
    calendar = inputs.get("backup_schedule", "")
    if not isinstance(calendar, str) or re.fullmatch(r"[A-Za-z0-9*,:. /+_-]+", calendar) is None:
        raise ServiceError("backup_calendar_required", 400)
    resources = configuration.get("backup_resources")
    if not isinstance(resources, dict):
        raise ServiceError("backup_resource_limits_required", 400)
    memory, cpu = resources.get("memory_limit_bytes"), resources.get("cpu_quota_percent")
    if type(memory) is not int or memory <= 0 or type(cpu) is not int or not 0 < cpu <= 100:
        raise ServiceError("backup_resource_limits_required", 400)
    rendered = {}
    for name, content in sources.items():
        content = content.replace("@SERVICE_GID@", str(inputs["service_gid"]))
        content = content.replace("@BACKUP_CALENDAR@", calendar)
        content = content.replace("@BACKUP_MEMORY_LIMIT@", str(memory))
        content = content.replace("@BACKUP_CPU_PERCENT@", str(cpu))
        rendered[name.removesuffix(".in")] = content.encode()
    return rendered


def render(configuration: dict[str, object], destination: Path) -> None:
    sources = {
        source.name: source.read_text()
        for source in (Path(__file__).parent / "systemd").iterdir()
    }
    rendered = rendered_files(configuration, sources)
    destination.mkdir(mode=0o700, exist_ok=False)
    for name, value in rendered.items():
        (destination / name).write_bytes(value)
    script = Path(__file__).with_name("renew-cert.sh")
    (destination / script.name).write_bytes(script.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("deployment", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    render(protected_json(arguments.deployment), arguments.output)


if __name__ == "__main__":
    main()
