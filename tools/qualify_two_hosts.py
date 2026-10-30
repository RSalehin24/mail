"""Measure the actual isolated two-host deployment. Missing evidence never qualifies a release."""

import argparse
import asyncio
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from applications import VERSION  # noqa: E402
from applications.qualification import validate  # noqa: E402
from shared.errors import ServiceError  # noqa: E402
from tools.load_webmail import run as workload  # noqa: E402
from tools.run_tests import source_digest, timestamp  # noqa: E402
from tools.simulation_network import private_dns  # noqa: E402

# Run read-only monitors through existing SSH trust. Never install or alter services here.
MONITOR = r"""
import json,pathlib,subprocess,sys,time
kind=sys.argv[1]; duration=int(sys.argv[2])
root=pathlib.Path('/var/lib/mail-'+kind)
host=json.loads(pathlib.Path('/etc/mail-'+kind+'/host.json').read_text())
marker=json.loads((root/'installation.json').read_text())
if not host.get('qualification_only'):
 raise SystemExit('An isolated qualification installation is required')
def docker(*args):
 return json.loads(subprocess.check_output(['/usr/bin/docker',*args],text=True,timeout=10))
def memory():
 lines=pathlib.Path('/proc/meminfo').read_text().splitlines()
 return {line.split(':')[0]:int(line.split()[1])*1024 for line in lines}
def oom():
 values=dict(line.split() for line in pathlib.Path('/proc/vmstat').read_text().splitlines())
 return int(values['oom_kill'])
names=['mail-webmail']
if kind=='management': names=['mail-management','mail-management-integration','maddy']
initial={name:docker('inspect',name)[0] for name in names}
minimum=memory()['MemAvailable']; before=oom(); samples=0; cycles=set()
peaks={name:0 for name in names}; native_oom={name:False for name in names}
stop=time.monotonic()+duration
while time.monotonic()<stop:
 minimum=min(minimum,memory()['MemAvailable']); samples+=1
 for name,container in initial.items():
  candidates=list(pathlib.Path('/sys/fs/cgroup').glob('system.slice/docker-'+container['Id']+'.scope'))
  if not candidates: candidates=list(pathlib.Path('/sys/fs/cgroup').glob('docker/'+container['Id']))
  if not candidates: raise SystemExit('Cannot measure container cgroup independently')
  group=candidates[0]
  peaks[name]=max(peaks[name],int((group/'memory.peak').read_text()))
  events=dict(line.split() for line in (group/'memory.events').read_text().splitlines())
  native_oom[name]=native_oom[name] or int(events.get('oom_kill',0))>0
 if kind=='management':
  observation=subprocess.check_output(['/usr/bin/systemctl','show','mail-management-maintenance.service','--property=ExecMainStartTimestampMonotonic','--value'],text=True,timeout=3).strip()
  if observation and observation!='0': cycles.add(observation)
 time.sleep(1)
containers={}
for name,old in initial.items():
 current=docker('inspect',name)[0]
 if current['Id']!=old['Id']: raise SystemExit('Container replaced during measurement')
 containers[name]={'memory_limit_bytes':current['HostConfig']['Memory'],'peak_memory_bytes':peaks[name],
  'oom_killed':native_oom[name] or current['State']['OOMKilled'],
  'error_count':int(bool(current['State']['Error'])),'image':current['Image']}
print(json.dumps({'machine_id':pathlib.Path('/etc/machine-id').read_text().strip(),
 'memory_total_mib':memory()['MemTotal']/1048576,'available_memory_min_bytes':minimum,
 'oom_kills_before':before,'oom_kills_after':oom(),'containers':containers,'monitor_samples':samples,
 'installation_id':marker['installation_id'],'maintenance_cycles':len(cycles)}),flush=True)
"""


def ssh_target(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-]+@[a-zA-Z0-9.-]+", value):
        raise ValueError("Use a simple SSH user@hostname target with existing host-key trust")
    return value


async def monitor(
    host: str, kind: str, duration: int, ssh_config: str | None = None
) -> dict[str, Any]:
    command = "sudo -n /usr/bin/python3 -c " + shlex.quote(MONITOR)
    command += " " + shlex.quote(kind) + " " + str(duration)
    arguments = ["/usr/bin/ssh"]
    if ssh_config:
        configuration = Path(ssh_config)
        if configuration.stat().st_mode & 0o077:
            raise ValueError("Use a private SSH configuration")
        arguments += ["-F", str(configuration)]
    process = await asyncio.create_subprocess_exec(
        *arguments,
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        ssh_target(host),
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), duration + 30)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError(kind + " monitor failed: " + stderr.decode()[-1000:])
    result = json.loads(stdout)
    if not isinstance(result, dict):
        raise ValueError("Invalid host observation")
    return result


async def qualify(config: dict[str, Any], destination: Path, cases: dict[str, Any]) -> None:
    if config.get("scope") != "isolated-two-host-mail-qualification":
        raise ValueError("An explicit isolated qualification configuration is required")
    if len(config.get("accounts", [])) != 10 or config.get("messages_per_mailbox", 0) < 1000:
        raise ValueError("Ten synthetic mailboxes with at least 1000 messages each are required")
    if config["management_ssh"] == config["webmail_ssh"]:
        raise ValueError("Two independent servers are required")
    duration = max(120, int(config.get("duration_seconds", 120)))
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    report: dict[str, Any] = {
        "version": VERSION,
        "scope": config["scope"],
        "source_sha256": source_digest(),
        "started_at_utc": timestamp(),
        "decision": "blocked",
        "hosts": {},
        "simulation": config.get("simulation") is True,
    }
    monitors = {
        kind: asyncio.create_task(
            monitor(config[kind + "_ssh"], kind, duration + 90, config.get("ssh_config"))
        )
        for kind in ("management", "webmail")
    }
    try:
        await workload(config, duration, destination / "workload.json", 10, 10)
        outcomes = await asyncio.gather(*monitors.values(), return_exceptions=True)
        for kind, outcome in zip(monitors, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                report.setdefault("monitor_errors", {})[kind] = str(outcome)
            else:
                report["hosts"][kind] = outcome
        load = json.loads((destination / "workload.json").read_text())
        report.update(
            users=10,
            max_active_users=10,
            messages=load["active_mailbox_seed_messages"],
            duration_seconds=duration,
            attachment_bytes=10 * 1024**2,
            unexpected_errors=len(load["errors"]),
            timings=load["timings"],
            maintenance_cycles=report["hosts"].get("management", {}).get("maintenance_cycles", 0),
            cases=cases.get("cases", {})
            if (
                cases.get("source_sha256") == report["source_sha256"]
                and cases.get("installation_ids")
                == {kind: host["installation_id"] for kind, host in report["hosts"].items()}
                and cases.get("images")
                == {
                    kind: {name: value["image"] for name, value in host["containers"].items()}
                    for kind, host in report["hosts"].items()
                }
            )
            else {},
        )
        try:
            validate(report)
            report["decision"] = "qualified"
        except ServiceError:
            report["decision"] = (
                "blocked: inspect measured host memory, OOMs, latency, errors and cases"
            )
    finally:
        for task in monitors.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*monitors.values(), return_exceptions=True)
        report["completed_at_utc"] = timestamp()
        (destination / "qualification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(report["decision"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--cases", type=Path, help="Matching lifecycle case evidence; absent cases block release"
    )
    args = parser.parse_args()
    if args.config.stat().st_mode & 0o077:
        parser.error("Keep synthetic credentials in a mode-0600 configuration")
    os.umask(0o077)
    config = json.loads(args.config.read_text())
    cases = json.loads(args.cases.read_text()) if args.cases else {}
    with private_dns(config):
        asyncio.run(qualify(config, args.output, cases))


if __name__ == "__main__":
    main()
