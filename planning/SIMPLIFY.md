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

## Stage 3: certificates and alerts
- Check whether Maddy's built-in certificate renewal replaces the certbot machinery for this DNS setup (unverified).
- One small alert check for backup age and certificate expiry.

## Open risk accepted by the owner
A dashboard bug could reach mail data directly (no root helper boundary). Mitigation: login plus TOTP plus HTTPS, read-only container where possible, only the Maddy data directory mounted.
