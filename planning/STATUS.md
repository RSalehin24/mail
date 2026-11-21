# Status

Updated 2026-11-21 (final, after the owner's dashboard and mail check). Resume here; decisions are in [DECISIONS](DECISIONS.md) (D-001 to D-047), procedures in [RUNBOOK](RUNBOOK.md), the original plan in [PLAN](PLAN.md).

## Current state (production, healthy)
- One VM `rsalehin24-mail` (us-east1-c), STANDARD-tier ephemeral IP (now 35.211.40.80; the boot service `mail-admin-dns-update` fixes the `mail.rsalehin24.me` A record, proven by a real IP change). Firewall: 25, 587, 993, 443 and key-only SSH; $0.01 budget alert `zero-cost-guard`.
- One compose project in `/opt/mail-admin`: `maddy` and `mail-admin` (image `mail-admin:stage9`), both uid 10001 with all capabilities dropped. The dashboard runs the host operations in-process; no root helper.
- Maddy issues and renews its own Let's Encrypt certificate (DNS-01 through Cloudflare, store `/opt/maddy/data/acme`, valid until 2027-02-19); the dashboard reads the same files and restarts when they change. Certbot is gone.
- Root workers (systemd units, see RUNBOOK): backup 03:00 Asia/Dhaka (Restic to R2), backup-control, recovery, forwarding, monitors, Maddy restart, DNS update. The backup pause marker is `/var/lib/mail-admin-helper/consumers.json`.
- Backups: latest verified 2026-11-21T14:05 UTC with 9 accounts; a restore into a scratch folder and a blank-VM rebuild from a snapshot were rehearsed (RUNBOOK).
- Recovery proof re-attested (15 files bound), owner reviewed and installed.
- Checks on main: lint, typecheck, unit 410, integration 88, `make linux-check` (Maddy and runtime suites).

## Verified by the owner and in the logs (2026-11-21)
- The owner checked the dashboard pages and confirmed mail works in a client; the server log shows a real inbound message accepted through Cloudflare routing and the message count rising from 153 to 155, with an empty outbound queue.

## Open (only things that depend on time or on the owner)
1. Tonight's 03:00 Dhaka backup is the first scheduled run on the final code: check it is `verified` with 9 accounts (the journal now says why a run failed).
2. Billing: look at Billing, then Reports, in a few days for any non-zero line; the $0.01 alert `zero-cost-guard` is set.
3. Maddy's first real certificate renewal is about two months out.
4. Optional live checks that need the owner's TOTP or approval: create, delete and restore a temporary mailbox (deleted-mailbox recovery), re-save the relay settings, send one test alert email.
5. Left on purpose: kernel 7.0.0-1016 as a fallback; old backup staging folders under the backup app's own retention; `backend/operator.py` (run inside the dashboard image by `host/image_tools.py` for offline recovery); `/root/cutover-20261121/{refactor,rollback-dns-update.sh,stage2.env}`.

## Known limits
- Permanent deletion stays blocked by design (production never sets `restore_passed`).
- A real Docker restart, backup and IP change are proven in production only; the lost ACME fixtures are replaced by `tests/runtime/test_pause_resume.py`.
- A brief Maddy restart (about 10 seconds) was seen at two deploys without a cause found; backups and the relay restart worker restart it on purpose.
- From the workstation SSH needs `-o HostKeyAlias=35.211.79.14` (the known-hosts entry is keyed by the original IP).
