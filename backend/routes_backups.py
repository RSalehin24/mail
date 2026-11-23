"""Authenticated, read-only backup catalog and log. Backing up and restoring are VM commands."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request

from backend.authorization import authenticated
from backend.routes_recovery import call

router = APIRouter(prefix="/api/backups", dependencies=[Depends(authenticated)])


@router.get("")
async def listing(
    request: Request,
    page: int = 1,
    page_size: int = 10,
    kind: Literal["snapshots", "operations"] = "snapshots",
) -> dict[str, Any]:
    return await call(request, "backup_list", {"page": page, "page_size": page_size, "kind": kind})
