# Status

Updated 2026-11-21 (after the UI, notification mail, OS update and cleanup work). Resume here; decisions are in [DECISIONS](DECISIONS.md) (D-001 to D-063), procedures in [RUNBOOK](RUNBOOK.md), the original plan in [PLAN](PLAN.md).

## Current state (production, healthy)
- One VM `rsalehin24-mail` (us-east1-c), STANDARD-tier ephemeral IP (now 35.211.40.80; the boot service `mail-admin-dns-update` fixes the `mail.rsalehin24.me` A record, proven by a real IP change). Firewall: 25, 587, 993, 443 and key-only SSH; $0.01 budget alert `zero-cost-guard`.
- One compose project in `/opt/mail-admin`: `maddy` and `mail-admin` (image `mail-admin:stage12`; `stage11` kept for rollback), both uid 10001 with all capabilities dropped. The dashboard runs the host operations in-process; no root helper.
- Maddy issues and renews its own Let's Encrypt certificate (DNS-01 through Cloudflare, store `/opt/maddy/data/acme`, valid until 2027-02-19); the dashboard reads the same files and restarts when they change. Certbot is gone.
- Root workers (systemd units, see RUNBOOK): backup 03:00 Asia/Dhaka (Restic to R2), backup-control, recovery, forwarding, monitors, Maddy restart, DNS update. The backup pause marker is `/var/lib/mail-admin-helper/consumers.json`.
- Backups: latest verified 2026-11-21T15:32 UTC with 9 accounts; a restore into a scratch folder and a blank-VM rebuild from a snapshot were rehearsed (RUNBOOK).
- Recovery proof re-attested (15 files bound), owner reviewed and installed.
- Dashboard (D-049 to D-060, [UI-NOTIFICATIONS](UI-NOTIFICATIONS.md)): Overview home at `/`, Mailboxes at `/mailboxes`, left navigation rail, light/dark theme with a System/Light/Dark dropdown (saved in the browser), minimal text. Alert, delivery-test and verification-code mails are HTML plus plain text with no JSON.
- Host: Ubuntu packages upgraded 2026-11-21, kernel 7.0.0-1018 running, previous kernel removed (D-062, D-063).
- Checks on main: lint, typecheck, reads, unit 417, integration 88, `make browser` (set `MAIL_ADMIN_CHROME` on Linux), `make linux-check` (Maddy suite had an unrelated SMTP-timeout flake once per run; passes on rerun).

## Verified by the owner and in the logs (2026-11-21)
- The owner checked the dashboard pages and confirmed mail works in a client; the server log shows a real inbound message accepted through Cloudflare routing and the message count rising from 153 to 155, with an empty outbound queue.
- After the UI and notification deploy the owner signed in and the new dashboard worked; the delivery test mail arrived and its receipt was confirmed; a temporary mailbox was created, deleted, restored and deleted again; the owner reported everything works as expected (D-054, D-061 to D-063).

## Done 2026-11-23 (D-070 to D-079)
- Password-only operations (D-070), recovery path loop fixed (D-074), then direct deletion (D-078, D-079): the dashboard archives, removes and records a mailbox in one request with Maddy running, no queue, no worker, no recovery proof. Image `mail-admin:stage16`; rollbacks `/root/cutover-20261121/rollback-v13.sh`, `-v14.sh`, `-v15.sh`, `-v16.sh`.
- Restoring a deleted mailbox: `sudo python3 -m host.restore_mailbox <record-id>` (RUNBOOK). Expiry: `mail-admin-recovery-expire.timer`, daily.
- Checks on main: lint, typecheck, reads, unit 413, integration 93, all 29 real-Maddy modules and the runtime suite, `make browser` (`MAIL_ADMIN_CHROME=/usr/bin/google-chrome-stable`).

## Also done 2026-11-23/24 (D-080 to D-085)
- Forwarding applied by the dashboard (no 30 s timer), orphan-key backup fix, incident restore (D-081), live backup capture (D-083), backup and restore as VM commands (D-084), one state database (D-085). Image `mail-admin:stage21` (D-087: relay reload in place, stale Server cards removed); rollbacks `/root/cutover-20261121/rollback-v13.sh` to `-v21.sh`.
- Checks on main: lint, typecheck, reads, unit 452, integration 94, all 27 real-Maddy modules and the runtime suite, `make browser` (`MAIL_ADMIN_CHROME=/usr/bin/google-chrome-stable`).
- Remaining scheduled units: backup (daily), recovery expiry (daily), monitor, independent monitor, TLS observation. Nothing polls.

## Still open for the owner
- (Done 2026-11-24, D-086) Deletion policy default is 7 days.
- Update mail clients with the five new passwords chosen during the restore.
- (Done 2026-11-24) All dashboard pages checked after the state-database migration.
- Frontend rewrite: owner, separate session (planning/FIGMA-BRIEF.md).

## Open (only things that depend on time)
1. Tonight's 03:00 Dhaka backup is the first scheduled run on the new code, kernel and Docker: check it is `verified` with 9 accounts (the journal says why a run failed).
2. Billing: look at Billing, then Reports, in a few days for any non-zero line; the $0.01 alert `zero-cost-guard` is set.
3. Maddy's first real certificate renewal is about two months out (certificate valid to 2027-02-19).
4. Left on purpose: the two backup staging folders the backup app tracks (its own retention); `backend/operator.py` (run inside the dashboard image by `host/image_tools.py` for offline recovery); `/root/cutover-20261121/{stage2.env,rollback-dns-update.sh,rollback-dashboard.sh}` (stage2.env is needed by every compose deploy) and the image `mail-admin:stage11` for the dashboard rollback.
5. Not tested live: the email-code sign-in mail and re-saving the relay (the Save button needs a changed field; dropped by the owner, D-055).

## Known limits
- Permanent deletion stays blocked by design (production never sets `restore_passed`).
- A real Docker restart, backup and IP change are proven in production only; the lost ACME fixtures are replaced by `tests/runtime/test_pause_resume.py`.
- A brief Maddy restart (about 10 seconds) was seen at two deploys without a cause found; backups and the relay restart worker restart it on purpose.
- From the workstation SSH needs `-o HostKeyAlias=35.211.79.14` (the known-hosts entry is keyed by the original IP).
