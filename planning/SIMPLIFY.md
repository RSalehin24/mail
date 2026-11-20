# Simplification plan (D-019)

Goal: the dashboard does the work itself. Keep all features the owner uses (backup/restore UI, deleted-mailbox recovery, sending relay and history, server charts and DNS diagnostics, mailbox management, sign-in security). Remove the machinery around them. Each stage is a T3 change: restore point first, rollback plan, owner approval before anything on the VM changes. Measured 2026-11-20: dashboard app (`backend/`, `frontend/`, `shared/`) about 5,500 lines; `helper/` 2,600; `host/` 7,300 with 23 systemd units; `tools/` 7,200; `tests/` 24,000; `docs/` + `verification/` 9,500.

## Stage 1: remove machinery, keep every feature (repo only, no VM change)
- Qualification and evidence tooling (about 4,800 lines in `tools/`: `qualification_*`, `qualify_*`, `catalogue_*`, `prepare_release`, `run_qualification`, `run_host_qualification`, `run_remote`, `run_mock_host`, `run_local_mock`, `ssh_proxy`, `tools/vm`, and similar) plus the tests that only exercise it.
- Historical `verification/` (about 4,400 lines) and the reviewed-contract docs `docs/mail-admin-implementation/` (about 4,600 lines, with `make plan`), replaced by one short README and runbook.
- Keep: `run_tests.py`, `run_linux.py`, `fixture_images.py`, `maddy_fixture.py`, `fetch_restic.py`, `export_frontend.py`, `lock_dependencies.py`, browser checks, Dockerfile.
- Not in Stage 1: Cloudflare forwarding sync (needs Phase 2 first, D-001), sign-in factors (not asked).
- Check after each commit: lint, typecheck, reads, unit, integration, `make linux-check`.

## Stage 2: the app does the work (live mail path)
- Dashboard container runs the Maddy account commands itself with only Maddy's data directory mounted; no Docker socket and no root helper (`helper/` removed).
- Backups: one small nightly restic job; the UI keeps retry, list and restore by calling it directly; remove the worker queue, polling timers, staging area and verification ledgers.
- Result target: 3 to 4 timers instead of 23 units. Needs a spike first: confirm Maddy's account commands are safe against the running server, then a disposable-VM or fixture test before any live change.

### Stage 2 spike result (2026-11-20, local Docker fixture, no production contact)
A separate process can run `creds create|password|remove|list` and `imap-acct create|remove|list` against the shared data directory while the real Maddy server keeps serving IMAP: new accounts log in at once, a password change takes effect at once, a removed account stops working, and 12 creates during 4 clients logging in about 50 times a second produced no login failures. The helper's in-process seam (`HostHelper` takes a `maddy` object with `change()` and `inventories()`) means the backend can use the same logic with a local-subprocess Maddy instead of `docker exec`.

Findings that shape Stage 2 (all verified in the fixture except where marked):
1. **Ownership is the real blocker.** Production runs Maddy as root with `/opt/maddy/data` root-only (mode 600/700) and the dashboard as uid 10001 with a read-only root filesystem. The dashboard can only manage accounts if Maddy also runs as 10001 and the data directory belongs to 10001. Verified locally with Maddy as 10001:10001 (server listened on 993 and 25 as non-root). Production change needed (T3): run the Maddy container as 10001, `chown -R` the data directory, make the TLS generation files readable by 10001, and give `/run/maddy` a tmpfs owned by 10001.
2. **The command needs a writable runtime directory.** Without `/run/maddy` the CLI printed `mkdir /run/maddy: permission denied` and still exited 0. Keep the helper's rule: judge success from the output, not the exit code. The dashboard container needs a tmpfs at `/run/maddy`.
3. The dashboard image needs the Maddy binary (copy it from the same pinned `foxcpp/maddy:0.9.5` image), a read-write mount of the Maddy data directory, the Maddy config read-only, and the TLS directory read-only. It will then be able to read all mail: this is the accepted risk in D-019.
4. Not verified: the stock production Maddy image (the fixture runs the patched build), memory of the CLI inside the dashboard's 128 MiB limit, binding ports below 1024 as non-root on the VM's real Docker engine (worked in the local engine).

### Correction after reading the helper (2026-11-20)
The spike proves the Maddy account commands can run from the dashboard. It does not make the helper removable by a seam swap. `HostHelper` serves eleven operation families (mailbox create/password/delete/inventory, status/telemetry, backup_*, recovery_*, forwarding_*, submission_*, deletion guard, outcome lookup). They share one journal, one lock file used by root workers, and root-owned policy files that 33 places check with root-ownership and mode tests (`protected_json`, `st_uid`, 0o600). Backups and TLS renewal need Docker (to pause Maddy's writers and run certbot), which the dashboard must never get. So the realistic end state is not "no helper, 3 or 4 timers" but: the dashboard does the mail, status and forwarding work in-process, and one small root agent stays for the Docker-only jobs (backup capture, restore, TLS renewal). Moving mailbox operations alone does not help, because the journal and lock are shared with the other operations. The in-process route needs the ownership model rewritten (what "protected" means once the app user owns the files) and the three Docker suites re-run.

### Stage 2 design (D-022) and steps
Target: the dashboard runs `HostHelper` in-process (the tests' `DirectHelper` already wraps it that way). Root stays only for Docker jobs.
- **In the dashboard:** inventory, create, password, delete, status, forwarding, and the backup/recovery queues. Maddy commands run as a local subprocess (`LocalMaddy`, same `MaddyAccess` interface as `DockerMaddy`).
- **Root agent (existing workers, path-activated):** backup capture and restore, mailbox recovery imports, TLS renewal, and a new small "restart Maddy" request for relay (submission) changes, because a restart needs Docker.
- **Packaging (verified):** the stock `foxcpp/maddy:0.9.5` binary is built for Alpine/musl. Copying `/bin/maddy` (41 MB) plus `/lib/ld-musl-x86_64.so.1` (0.6 MB) into the Debian dashboard image runs `maddy version` correctly, so the Dockerfile can `COPY --from` the same pinned image.
- **Mount layout (found by the test):** the CLI parses `maddy.conf` and loads the certificate it names, so the dashboard container needs the Maddy data directory at `/data` (read-write), the config at `/data/maddy.conf` and the TLS generation directory at `/data/tls` (read-only), plus a tmpfs at `/run/maddy`. The dashboard already mounts the same TLS generation read-only for its own HTTPS.
- **Ownership:** state shared with the root workers (journal, queue databases, lock file) becomes owned by uid 10001; root can still use it. Checks that compare a file's owner to its directory's owner keep working; checks that hard-code root must be reviewed (33 places in `helper/`).
Steps (repo only, each green on all suites before the next): (1) `LocalMaddy` + tests; (2) a local host facade with the `HelperClient.call` interface, wired behind a setting; (3) app-owned state and lock paths; (4) queue-only backup/recovery and the restart-Maddy request; (5) remove the socket server, protocol and helper service; (6) Dockerfile and compose changes; (7) VM change plan (T3): Maddy as 10001, data ownership, new mounts, one compose project for both containers (today `maddy` and `mail-admin` are separate projects on separate networks; the Maddy compose file lives on the VM, outside the repo, so copy it into the repo first), restore point, rollback.

## Stage 3: certificates and alerts
- Check whether Maddy's built-in certificate renewal replaces the certbot machinery for this DNS setup (unverified).
- One small alert check for backup age and certificate expiry.

## Open risk accepted by the owner
A dashboard bug could reach mail data directly (no root helper boundary). Mitigation: login plus TOTP plus HTTPS, read-only container where possible, only the Maddy data directory mounted.
