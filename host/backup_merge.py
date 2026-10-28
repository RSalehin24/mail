"""Transactional additive import for the pinned Maddy schema, preserving live mail."""

import hashlib
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from host.mailbox_store import _insert, body_path, connect, validate
from shared.errors import ServiceError


def signature(row: sqlite3.Row, bodies: Path) -> tuple[str, Any]:
    key = row["extBodyKey"]
    if key is None:
        raise ServiceError("inline_message_restore_unsupported", 503)
    with body_path(bodies, key).open("rb") as reader:
        return hashlib.file_digest(reader, "sha256").hexdigest(), row["date"]


def merge_rows(
    live: sqlite3.Connection,
    live_bodies: Path,
    source: Path,
    source_bodies: Path,
    keys: dict[str, str],
) -> int:
    """Deduplicate by per-folder body/date multiplicity, retaining genuine duplicates."""
    added = 0
    validate(live)
    with connect(source) as backup:
        validate(backup)
        for account in backup.execute("SELECT * FROM users ORDER BY username"):
            user = live.execute(
                "SELECT * FROM users WHERE username=?", (account["username"],)
            ).fetchone()
            if user is None:
                row = dict(account)
                row.pop("id")
                row["inboxId"] = 0
                userid = _insert(live, "users", row)
            else:
                userid = user["id"]
            folder_ids = {}
            for folder in backup.execute(
                "SELECT * FROM mboxes WHERE uid=? ORDER BY id", (account["id"],)
            ):
                current = live.execute(
                    "SELECT * FROM mboxes WHERE uid=? AND name=?", (userid, folder["name"])
                ).fetchone()
                if current is None:
                    row = dict(folder)
                    row.pop("id")
                    row.update(uid=userid, uidnext=1, msgsCount=0)
                    folderid = _insert(live, "mboxes", row)
                    nextid = 1
                else:
                    folderid, nextid = current["id"], current["uidnext"]
                folder_ids[folder["id"]] = folderid
                available = Counter(
                    signature(row, live_bodies)
                    for row in live.execute("SELECT * FROM msgs WHERE mboxId=?", (folderid,))
                )
                for message in backup.execute(
                    "SELECT * FROM msgs WHERE mboxId=? ORDER BY msgId", (folder["id"],)
                ):
                    identity = signature(message, source_bodies)
                    if available[identity]:
                        available[identity] -= 1
                        continue
                    row = dict(message)
                    oldkey = row["extBodyKey"]
                    newkey = keys[oldkey]
                    stored = live.execute(
                        "SELECT uid FROM extKeys WHERE id=?", (newkey,)
                    ).fetchone()
                    if stored is None:
                        _insert(live, "extKeys", {"id": newkey, "uid": userid, "refs": 1})
                    elif stored["uid"] != userid:
                        raise ServiceError("mailbox_import_body_conflict", 409)
                    else:
                        live.execute("UPDATE extKeys SET refs=refs+1 WHERE id=?", (newkey,))
                    row.update(mboxId=folderid, msgId=nextid, extBodyKey=newkey)
                    _insert(live, "msgs", row)
                    for flag in backup.execute(
                        "SELECT flag FROM flags WHERE mboxId=? AND msgId=?",
                        (folder["id"], message["msgId"]),
                    ):
                        _insert(
                            live, "flags", {"mboxId": folderid, "msgId": nextid, "flag": flag[0]}
                        )
                    nextid += 1
                    added += 1
                live.execute(
                    "UPDATE mboxes SET uidnext=?,msgsCount=(SELECT count(*) FROM msgs "
                    "WHERE mboxId=?) WHERE id=?",
                    (nextid, folderid, folderid),
                )
            if user is None:
                if account["inboxId"] not in folder_ids:
                    raise ServiceError("invalid_mailbox_import", 503)
                live.execute(
                    "UPDATE users SET inboxId=? WHERE id=?",
                    (folder_ids[account["inboxId"]], userid),
                )
        validate(live)
    return added
