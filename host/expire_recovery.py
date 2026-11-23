"""Scheduled job: delete recovery archives whose retention has ended. Run daily by a timer."""

import time
from pathlib import Path

from helper.recovery_records import RecoveryRecords
from helper.state_database import STATE_FILE
from host.mailbox_archive import MailboxArchives
from shared.files import protected_json, state_owner_uid

STATE = Path("/var/lib/mail-admin-helper")
DEPLOYMENT = Path("/etc/mail-admin/deployment.json")


def expire(state: Path, owner: int) -> int:
    records = RecoveryRecords(state / STATE_FILE)
    archives = MailboxArchives(state / "recovery-points", state / "recovery.key", owner)
    expired = records.expired(int(time.time()))
    for record in expired:
        _remove(records, archives, record)
    return len(expired)


def _remove(records: RecoveryRecords, archives: MailboxArchives, record: dict[str, object]) -> None:
    identifier, email = str(record["id"]), str(record["email"])
    with records.transaction() as connection:
        connection.execute(
            "INSERT INTO recovery_audit(created,action,request_id,email,outcome) VALUES(?,?,?,?,?)",
            (int(time.time()), "recovery_point_removal", identifier, email, "intent_recorded"),
        )
    records.transition(identifier, str(record["state"]), "removing", int(time.time()))
    archives.remove(str(record["snapshot"]))
    records.transition(identifier, "removing", "removed", int(time.time()))
    with records.transaction() as connection:
        connection.execute(
            "INSERT INTO recovery_audit(created,action,request_id,email,outcome) VALUES(?,?,?,?,?)",
            (int(time.time()), "recovery_point_removal", identifier, email, "complete"),
        )


def main() -> None:
    count = expire(STATE, state_owner_uid(protected_json(DEPLOYMENT)))
    print(f"Removed {count} expired recovery archive(s)")


if __name__ == "__main__":
    main()
