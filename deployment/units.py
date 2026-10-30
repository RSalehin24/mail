"""Fixed service definitions; the Webmail server receives no privileged mail helper."""

import subprocess
from pathlib import Path
from typing import Any


def units(host: dict[str, Any], config: Path) -> dict[str, str]:
    name = "mail-" + host["kind"]
    python = host["release"] + "/.venv/bin/python"
    header = "[Unit]\nAfter=docker.service network-online.target\nRequires=docker.service\n"
    result = {
        name + ".service": header
        + f"""
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/docker compose -f {config}/compose.json up -d
ExecStop=/usr/bin/docker compose -f {config}/compose.json stop
[Install]
WantedBy=multi-user.target
""",
        name + "-maintenance.service": header
        + f"""
[Service]
Type=oneshot
WorkingDirectory={host["release"]}
ExecStart={python} -m applications.host reconcile {config}/host.json
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
""",
        name + "-maintenance.timer": f"""[Unit]
Description=Reconcile {name} configuration and maintenance
[Timer]
OnBootSec=20s
OnUnitInactiveSec=30s
Unit={name}-maintenance.service
[Install]
WantedBy=timers.target
""",
    }
    result[name + "-backup.service"] = (
        header
        + f"""
[Service]
Type=oneshot
WorkingDirectory={host["release"]}
ExecStart={python} -m deployment.recovery backup {config}/host.json
UMask=0077
MemoryMax={host.get("backup_memory_mib", host["memory_mib"])}M
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
"""
    )
    result[name + "-backup.timer"] = f"""[Timer]
OnCalendar=*-*-* 03:00:00 Asia/Dhaka
Persistent=yes
Unit={name}-backup.service
[Install]
WantedBy=timers.target
"""
    if host["kind"] == "management":
        result[name + "-helper.service"] = (
            header
            + f"""
[Service]
Type=simple
WorkingDirectory={host["release"]}
ExecStart={python} -m applications.host serve {config}/host.json
RuntimeDirectory=mail-management-helper
RuntimeDirectoryMode=0750
Group={host["gid"]}
UMask=0077
Restart=on-failure
RestartSec=3s
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
[Install]
WantedBy=multi-user.target
"""
        )
    return result


def install_units(host: dict[str, Any], config: Path) -> None:
    rendered = units(host, config)
    for filename, content in rendered.items():
        path = Path("/etc/systemd/system") / filename
        if path.exists():
            raise ValueError("Unknown service definition: " + filename)
        path.write_text(content)
        path.chmod(0o644)
    subprocess.run(("/usr/bin/systemctl", "daemon-reload"), check=True, timeout=30)
    name = "mail-" + host["kind"]
    selected = [name + ".service", name + "-maintenance.timer", name + "-backup.timer"]
    if host["kind"] == "management":
        selected.insert(0, name + "-helper.service")
    subprocess.run(  # noqa: S603 -- fixed generated service names.
        ("/usr/bin/systemctl", "enable", "--now", *selected), check=True, timeout=60
    )
