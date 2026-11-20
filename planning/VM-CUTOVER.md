# VM cutover runbook for Stage 2 (step 7; T3). NOT APPROVED. Nothing here has been run.

Goal: run Maddy and the dashboard as the same unprivileged user (uid/gid 10001, the existing `mail-admin` user) in one compose project, with the dashboard doing the mailbox work itself and no root helper. Mail is down for a few minutes during step 7.

## BLOCKER found during the cutover (2026-11-21, nothing live was changed)
`/var/lib/mail-admin-helper/recovery-qualified.json` (scope: "Qualified recoverable deletion and additive all-mailbox backup restore; permanent deletion remains disabled") binds the SHA-256 of 33 code files, and `require_qualified()` (`helper/recovery.py`) refuses recovery with `recovery_qualification_changed` if any differs. The recovery policy is enabled in production. Nine bound files differ from this repo on purpose: `helper/__main__.py` (deleted in step 5), `helper/protocol.py`, `helper/retention.py`, `helper/submission.py`, `host/backup_control.py`, `host/cli.py`, `host/context.py`, `host/daily_retention.py`, `host/systemd/mail-admin-forwarding.service`. Deploying the new code without resolving this would disable deleted-mailbox recovery. The qualification run that issued the proof (guest-VM tooling) was deleted in Stage 1 (D-020/D-021), so the proof cannot be regenerated the old way. Staged on the VM and harmless: restore point `/root/cutover-20261121` (configs, library tarball, rollback script), image `mail-admin:stage2` loaded, old image tagged `mail-admin:pre-stage2`, one throwaway container run (port check printed 0).

## Measured on the VM, read-only, 2026-11-21
- Docker 29.6.0, Compose v5.1.4. Disk: 21 GB free of 28 GB. Memory: about 463 of 952 MB used, no swap.
- Containers: `maddy` (image `ghcr.io/foxcpp/maddy@sha256:e1074e92...`, runs as root, 96 MiB limit, 0.5 CPU) and `mail-admin` (image `mail-admin:candidate` 84ed5d2de72e, uid 10001, 128 MiB limit, /tmp tmpfs 8 MiB). Two compose projects: `/opt/maddy/docker-compose.yml` and `/opt/mail-admin/docker-compose.yml`. No `.env` files; variables were exported at deploy time.
- Dashboard variables now: UID 10001, GID 10001, MEMORY_LIMIT 128m, TMPFS_LIMIT 8m, HASH_MEMORY_KIB 8192, HASH_PARALLELISM 1, HTTP_CONCURRENCY 8, OPERATION_TIMEOUT_SECONDS 30, EMAIL_OTP_ENABLED false, PERMANENT_DELETION_ENABLED false; TLS directory `/opt/maddy/tls/generations/3100633f...4535` (root:10001, 0750; certificate files 0640, readable by 10001).
- Deployment record `/etc/mail-admin/deployment.json` (root 0600): `maddy_cli_version` is already `0.9.6`; `service_uid`/`service_gid` 10001; `lock_wait_seconds` 1; `operation_timeout_seconds` 30; permanent deletion disabled; top-level `state_owner_uid` is absent. `helper-policy.json` is root 0600.
- Units: `mail-admin-helper.service` and `mail-admin-paths.service` enabled; seven `mail-admin-*.timer` enabled; no `.path` units yet.
- Ownership: `/opt/maddy/data` root:root 0700; `/var/lib/mail-admin-helper` root:root 0700; `/run/lock/mail-admin-maddy.lock` root 0600 (recreated at boot by tmpfiles).
- Maddy config listens on 25, 587, 993 and 143.

## Approvals to ask for first (AskUserQuestion), none given yet
1. The maintenance window and the cutover (mail stops for a few minutes; sending servers retry for days).
2. Loading the new image on the VM (inbound only; no egress cost).
3. A fresh backup to R2 as the restore point (a live backup run).
4. One throwaway container run to check non-root port binding (see preflight 3).
5. Each live mail test (send and receive one test message).

## Preflight (read-only, then one approved container run)
1. `systemctl --failed`; backup age in `/var/lib/mail-admin-backup/manifest.json` (needs a snapshot from the last day); `docker ps`.
2. On the workstation: `make build`; the image `mail-admin:candidate` is what ships. Check `docker image ls` for its size (about 340 MB) against 21 GB free.
3. Approved test only: `docker run --rm --cap-drop ALL --user 10001:10001 alpine cat /proc/sys/net/ipv4/ip_unprivileged_port_start` should print 0 so Maddy can bind 25, 587, 993 and 143 as 10001 with all capabilities dropped. If it prints more than 0, stop: add `sysctls: net.ipv4.ip_unprivileged_port_start=0` to the Maddy service and re-test.

## Restore point (no GCP snapshots: cost goal $0)
1. `sudo install -d -m 0700 /root/cutover-2026-11-21` and copy into it: `/opt/maddy/docker-compose.yml`, `/opt/mail-admin/docker-compose.yml`, `/opt/maddy/config`, `/etc/mail-admin` (including secrets), `/etc/systemd/system/mail-admin-*`, and `docker image ls` / `docker inspect maddy mail-admin` output. Keep the old image tags (`mail-admin:rollback-3c20fb3b5d95`, `mail-admin:candidate`).
2. `sudo systemctl start mail-admin-backup.service`, then confirm `manifest.json` has a new `cutoff`, `verified: true` and all 9 mailboxes. Do not proceed without this.

## Cutover (maintenance window)
Only the owner-approved commands, shown first, one block at a time.
1. Ship: `docker save mail-admin:candidate | gzip | ssh deployer@... 'gunzip | docker load'`. Copy only the new `docker-compose.yml`, the new unit files (`host/systemd/`, including `mail-admin-maddy-restart.*`, `mail-admin-backup-control.path`, `mail-admin-recovery.path` and the rendered tmpfiles) and nothing else. Never the repository, `docs/`, `planning/`, `verification/`, `tests/` or `tools/`.
2. Stop writers: `cd /opt/mail-admin && docker compose stop`; `cd /opt/maddy && docker compose stop`; `systemctl stop mail-admin-helper.service` and all `mail-admin-*.timer`.
3. Ownership: `chown -R 10001:10001 /opt/maddy/data /var/lib/mail-admin-helper`; `chgrp 10001` and `chmod 0640` on `/etc/mail-admin/deployment.json`, `helper-policy.json` and `/var/lib/mail-admin-backup/manifest.json` (owner stays root: the deletion guard requires it); add `"state_owner_uid": 10001` to the top level of `deployment.json` (not under `inputs`); render the tmpfiles and units again so `/run/lock/mail-admin-{renewal,maddy,backup}.lock` are created for 10001; `systemd-tmpfiles --create`; `systemctl daemon-reload`.
4. One compose project: remove the old two (`docker compose down` in each directory; keep their files in the restore directory), then in `/opt/mail-admin` export the variables listed above and run `docker compose config` to review, then `docker compose up -d`. Maddy starts first; the dashboard mounts the same data directory.
5. Units: `systemctl disable --now mail-admin-helper.service && systemctl mask mail-admin-helper.service`; enable the `.path` units and the timers again; `systemctl enable --now mail-admin-maddy-restart.path mail-admin-backup-control.path mail-admin-recovery.path`. Remove `/run/mail-admin-helper`.

## Verify (decide within 15 minutes)
- Maddy logs `server started`; ports 25, 587, 993 listening; `docker top maddy` shows uid 10001; `docker inspect` shows capabilities dropped.
- Dashboard: login with TOTP over HTTPS; Mailboxes page lists all 9; create and delete a temporary mailbox; change its password; Server page shows live charts; Backups page lists snapshots; Settings relay page loads (do not change relay settings; the restart path is exercised later in a planned change).
- IMAP login for one existing mailbox; send and receive one test message (ask first).
- Memory: `docker stats --no-stream` for both containers during a mailbox create; the dashboard must stay below its 128 MiB limit (the Maddy command now runs inside its cgroup). If it is killed or near the limit, raise the limit to 192m and re-test.
- `systemctl --failed` empty; next 03:00 Dhaka backup succeeds; `journalctl -u 'mail-admin-*'` clean.

## Rollback
Stop the new project (`docker compose down`); `chown -R 0:0 /opt/maddy/data /var/lib/mail-admin-helper`; restore `/etc/mail-admin/*` modes and remove `state_owner_uid`; restore both compose files and the old unit files from `/root/cutover-2026-11-21`; remove the `.path` units; `systemctl daemon-reload`; unmask and enable `mail-admin-helper.service`; start Maddy then the dashboard from their old compose files using the kept `mail-admin:rollback-3c20fb3b5d95` image; re-run the verification. If mail data looks damaged, restore from the fresh backup with the existing restore tooling (ask first).

## Not verified before the cutover
Production Maddy under uid 10001 with all capabilities dropped on this engine (preflight 3 covers ports); memory of the Maddy command inside the dashboard's 128 MiB; the permanent-deletion path against a real backup manifest; the relay change through the restart request file and the root worker on the real Docker (do it in a separate approved change after the cutover); the 587 listener under the new user; whether any other unit or script on the VM refers to `/run/mail-admin-helper` or `mail-admin-helper.service` (grep before the cutover).

## What changed in the repo
Nothing in the repo needs to change for the cutover itself; all of it is verified locally in the Docker fixture (STATUS.md).
