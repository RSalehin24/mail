# Plan: a straightforward, secure Mail Admin (2026-11-23)

Replaces [PLAN-V2](PLAN-V2.md). Owner direction (D-073): no queue, nothing bundled, everything is a direct action or a scheduled job, and security stays.

## 1. What is wrong today (measured 2026-11-23)
- About 33,000 lines of product code (backend 3.3k, helper 2.7k, host 5.8k, shared 0.6k, frontend 2.1k, tools 2.6k) plus 18,000 lines of tests, for 9 mailboxes and one administrator.
- 21 systemd units, 7 state files (`journal.db`, `recovery.db`, `backup-control.db`, `forwarding.db`, `consumers.json`, locks, a hash proof) split between a root world and a dashboard world that talk through files.
- Delete, restore and remove go: dashboard, queue row, path unit, root worker, Maddy stopped for about 20 s, archive, delete, Maddy started. One global lock (`mail-admin-maddy.lock`) is held for all of it, so every other action gets "Maintenance busy".
- Backups and relay changes also stop or restart Maddy. Account actions wait behind recovery jobs. The recovery proof pins 15 file hashes, so a one-line fix needs a 30-minute real-Maddy suite and a manual install.
- Path-triggered workers can start themselves forever (found 2026-11-23; fixed for recovery in D-074, backup-control unchecked).

Root cause: everything was designed as "the web app must never be able to do anything dangerous, so a root worker does it". That moved the danger into a large root-side system that is itself hard to keep correct.

## 2. Target design
```
Browser --HTTPS--> mail-admin container (unprivileged, read-only, no caps, no Docker socket)
                      |-- Maddy CLI + data (shared volume, same uid)   direct, synchronous
                      |-- one SQLite database (accounts metadata, deleted-mailbox records, policy, audit)
                      '-- hands nothing to anyone; no request files, no waiting
Maddy container (unchanged)
Root side, scheduled only (cron or systemd timer), nothing else runs:
  backup (daily 03:00 Dhaka, Restic to R2)   monitor (alerts)   DNS update (boot)
Owner commands on the VM over SSH (rare, run by hand, no daemon): back up now, restore a backup,
  restore a deleted mailbox
```
Rules:
1. A user action is one request that finishes and returns the result. No job table, no polling, no "queued".
2. Reads never take a lock. A write takes one short per-operation lock (seconds, not a 20 s pause).
3. Maddy is never stopped for deletion, archiving or backups (see Phase 1 spike; fallback below).
4. Anything scheduled is a cron or systemd timer entry. There are no path units, no workers waiting for requests and no polling (D-076). Rare root tasks are commands the owner runs over SSH.
5. One source of truth: one database file, no separate recovery, journal, forwarding and control databases.
6. No hash attestation of code. Safety comes from tests in CI, immutable image tags and rollback scripts.

## 3. Security that stays (invariants, tested in every phase)
| Invariant | How |
|---|---|
| Dashboard container unprivileged | uid 10001, all caps dropped, read-only root, no-new-privileges, no Docker socket, memory limit |
| Login and settings need password + code; operations need the password | D-070, as deployed |
| Destructive actions need typed confirmation and the password | unchanged |
| Secrets (Brevo, R2, Cloudflare, backup keys) never inside the dashboard | stay in root-owned files; dashboard only reads status |
| Deleted-mailbox archives cannot be read by a hijacked web app | archives are encrypted to a public key held by the dashboard; the private key stays with root/owner and is used only for restore (Phase 2). Needs no new dependency: `cryptography` is already locked |
| Backups encrypted off-site, daily, verified | Restic to R2, unchanged |
| Audit of every sensitive action | one audit table, shown in the dashboard |
| Cost $0, STANDARD tier IP, egress under 1 GB, e2-micro RAM | fewer processes lowers RAM; no new services |
What a dashboard compromise could do today vs after: today it can queue recovery jobs and read job data; after, it can delete or create mailboxes (it could already create) and request a backup, but still cannot read archives, secrets, backups or touch Docker or the host.

## 4. Versions
Every version: own deploy, restore point first, rollback script, tests added before code is removed, docs and `DECISIONS.md` updated the same day, owner approval before each live change.

### v0 Baseline (done or in progress)
Done: D-070 password-only operations, D-074 recovery loop fix. To do first: check `mail-admin-backup-control.path` for the same self-trigger (T3 read-only check, fix like D-074 if needed).

### v1 Spike: prove the simple delete (scratch data only, no production change)
- Prove on a copy of the Maddy data that an SQLite online backup of `imapsql.db` plus a copy of one account's message files is consistent while Maddy writes, and that mail arriving during the copy is either in the archive or not delivered to a deleted account (refuse delivery first, or recheck after).
- Check whether Maddy reloads its configuration without a container restart (for relay changes) and whether `maddy imap-acct remove` works live.
- Output: a decision with the method and its test, or the fallback: a pause of a few seconds around only the final delete step.

### v2 Direct delete, restore and remove (T3)
- The dashboard archives (public-key encrypted), removes the account with the Maddy CLI and records it in the one database, in one request with a visible result. Restoring a deleted mailbox is a command the owner runs on the VM (private key stays root-only); the dashboard lists deleted mailboxes and shows the command to run.
- Remove: recovery worker, queue table, recovery path and timer units, `recovery-qualified.json`, the "maintenance busy" lock for reads, the 5-minute fallback.
- Account create and password change no longer wait on recovery work.
- Gate: unit, integration and real-Maddy runtime tests; five deletes back to back; delete, restore and verify a mailbox with its messages; mail flowing during a delete; security checklist from section 3; restore point; rollback script.

### v3 Backups without downtime (T3)
- Backup captures a consistent copy with the v1 method; no Maddy stop. The daily run is a cron or timer entry. "Back up now" and "restore a backup" become VM commands run by the owner; the dashboard only shows the latest backup and its status, and the Back up now and Restore buttons are removed. No path units.
- Remove: Maddy pause code, `consumers.json`, the maintenance-intent state machine.
- Gate: a real backup verified with 9 accounts, a restore into a scratch folder, one scheduled night watched.

### v4 Relay and forwarding without bundling (T2/T3)
- Relay change reloads or restarts Maddy in one direct step (the worker `maddy-restart` is removed if reload works).
- Forwarding sync becomes a plain timer job with the database as its only state; remove `forwarding.db` and its request-queue pieces.

### v5 One database and fewer units (T3)
- Migrate `journal.db`, `recovery.db`, `backup-control.db` and `forwarding.db` content that is still needed into `admin_data.db` with a tested migration and a restore point; delete the rest.
- Target units: backup timer and service, monitor timer, DNS-update boot service, plus the existing tmpfiles and journald settings (about 5 instead of 21). No `.path` units.
- Single status page entry per job, and the dashboard warns when any unit failed (v1.1 item).

### v6 Delete dead code, shrink tests (T0/T2)
- Remove `helper/` pieces and `host/` modules nothing uses, `tools/record_recovery_qualification.py` and related proof code, and tests that only covered removed paths (each removal logged). No test is weakened for code that stays. Target: product code roughly halved; update README, RUNBOOK and STATUS.

### v7 New frontend (T1, parallel from now)
- Owner designs in Figma from [FIGMA-BRIEF](FIGMA-BRIEF.md), then React and plain CSS replace React and MUI. The delete flow shows one result, never "queued", and the old "confirm your password" error in the delete modal goes away.
- Gate: lint, typecheck, unit, `make browser` (needs Chrome, now available), phone/tablet/desktop, light and dark, bundle size compared with today.

Order: v0, v1, v2, v3, v4, v5, v6; v7 in parallel. v2 to v5 depend on v1.

## 5. Risks and answers
- Archive consistency while Maddy writes: decided in v1 before any code is written; fallback is a few-seconds pause only at the last step.
- Dashboard writing archives: public-key encryption keeps archives unreadable to it.
- Removing the proof and root worker loses a check: replaced by CI tests, immutable image tags, restore points and rollback scripts; the owner reviews the v2 design before deploy.
- Mail downtime during a deploy: every deploy is UI/backend-first, Maddy is never restarted by a deploy.
- Data loss during DB consolidation (v5): migration is copy-then-verify, old files kept until a night's backup is verified.

## 6. Decisions the owner will be asked, one version at a time
1. v1: if the live copy cannot be made safe, accept a few-seconds Maddy pause around only the final delete?
2. v2: keep the 7-day recovery archive for every delete (default), or add "delete without a recovery copy"?
3. v2: who may hold the archive private key (root-only file on the VM, or also kept offline by the owner)? Decided D-076: it stays root-only and is never readable by the web app.
4. v5: how long to keep the old databases after migration?
5. v6: which retired tests may be removed.
