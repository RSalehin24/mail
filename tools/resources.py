"""Synthetic fixture limits, separate from unresolved production resource inputs."""

import shutil
from dataclasses import dataclass
from pathlib import Path

MADDY_MEMORY_MIB = 96
MADDY_RUNTIME_TMPFS_BYTES = 1024 * 1024
WEB_MEMORY_MIB = 128
RESTORE_MEMORY_MIB = 128
ACME_WRITER_MEMORY_MIB = 64
HOST_RESERVE_MIB = 128
FIXTURE_DISK_RESERVE_BYTES = 2 * 1024**3


def require_fixture_disk(directory: Path) -> None:
    if shutil.disk_usage(directory).free < FIXTURE_DISK_RESERVE_BYTES:
        raise RuntimeError('At least 2 GiB of free disk space is required before fixture work')


@dataclass(frozen=True)
class FixtureBudget:
    controller_mib: int
    companions_mib: int
    reserve_mib: int = HOST_RESERVE_MIB

    @property
    def minimum_available_bytes(self) -> int:
        return (self.controller_mib + self.companions_mib + self.reserve_mib) * 1024 * 1024


def fixture_budget(suite: str) -> FixtureBudget:
    if suite in {'acme', 'qualification'}:
        return FixtureBudget(192, 128 + 64 + 64 + MADDY_MEMORY_MIB + WEB_MEMORY_MIB)
    if suite == "runtime":
        return FixtureBudget(128, WEB_MEMORY_MIB + MADDY_MEMORY_MIB)
    if suite == "maddy":
        companions = max(
            2 * MADDY_MEMORY_MIB, MADDY_MEMORY_MIB + ACME_WRITER_MEMORY_MIB, RESTORE_MEMORY_MIB,
        )
        return FixtureBudget(192, companions)
    if suite in {"unit", "integration"}:
        return FixtureBudget(192, max(MADDY_MEMORY_MIB, RESTORE_MEMORY_MIB))
    raise ValueError("Unknown isolated test suite")
