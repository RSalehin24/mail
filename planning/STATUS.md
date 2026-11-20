# Status

Updated 2026-11-20. Resume here; do not re-derive earlier decisions ([DECISIONS](DECISIONS.md)). The plan is [PLAN](PLAN.md).

## Current stage

**Planning complete; Phase 0 not started.** Done today: switched the VM to the Standard network tier and restarted it; updated the Cloudflare A record for `mail.rsalehin24.me` to 35.211.79.14; set up SSH access; wrote the plan and the Claude Code setup (`CLAUDE.md`, `AGENTS.md`, `.claude/`).

## Next action

1. Phase 0 is done. Backup check done (see below). Update done (see below). Next: owner decides on deploying the Python 3.15 image and the timer fix together (T2/T3, needs restore point and approval), then Phase 1 (see PLAN).
2. Confirm the 2026-11-20 dashboard backup (started 9:46 PM Dhaka) finished, and read why the 9:13 PM boot-time run failed.
3. Delete `~/.cf_token` and revoke the temporary Cloudflare token (owner action).

## Phase 0 baseline (2026-11-20, Python 3.14, D-007 to D-015)
All suites pass locally: lint, typecheck, reads, unit (537), integration (94), build, browser, Maddy (31 modules), runtime (8), ACME (7). Fixes made to stale tests/fixtures, assertions unchanged: SMTP keyword case (D-009), inventory cache wait (D-014). The Maddy fixture image is a local rebuild with a different executable hash than the approved one, pinned by owner decision (D-013); weaker evidence than the original. Not run: `make plan`, qualification suite, any production check. Python 3.14 was used instead of 3.11.

## Update to Python 3.15 and latest libraries (2026-11-20, D-016, D-017)
Repo and image only, not deployed. Python 3.15.2 base image (digest-pinned), 18 Python and 3 npm packages updated within their majors; locks regenerated with the documented commands. Needed two fixes: PyOTP 2.12.1 keyword-only `name` (real runtime break, caught by mypy) and a 160-bit test TOTP secret; ruff dropped 16 unneeded annotation quotes. Passing: lint, typecheck, reads, unit and integration (local `.venv` is Python 3.14), `make build`, `make browser`, `make linux-check` (Maddy, runtime, ACME, unit and integration inside the 3.15 image). Not done: deploy; Debian bookworm base images and the Node, Docker CLI and Maddy pins are unchanged.

## Timer CPU measurement (2026-11-20 17:54 UTC, read-only)
Starts in the last hour: recovery 932 (timer: 2 s after each run), backup-control 845 (3 s), forwarding 111 (30 s), tls-observation 29 (2 min), monitor 12 (5 min), independent-monitor 4 (15 min). Last-run CPU per start is about 0.3-0.4 s each, so these timers cost roughly 675 CPU-s per hour (about 19% of one core, estimate from last-run values); recovery and backup-control are about 90% of it. CPU pressure avg300 is 11%; memory 468 of 953 MB used; no swap.

## Simplification (D-019)
Owner chose all three stages and keeps all four features. Plan: [SIMPLIFY](SIMPLIFY.md). Stage 1 (repo-only removal of qualification/evidence tooling and historical docs) awaits owner approval of the deletion list.

## Stage 2 step 1 done (repo only, D-022)
`helper/maddy.py` now has a shared `MaddyCli` base and `LocalMaddy` (runs the pinned Maddy binary directly with `-config`). Tests: `tests/unit/test_local_maddy.py` (stub binary) and `tests/maddy/test_local_maddy.py` (real binary against the real server, passing). Findings: the stock Maddy binary runs on the Debian image with the musl loader copied beside it; the CLI loads the TLS files named in the config, so the dashboard needs `/data`, `/data/maddy.conf`, `/data/tls` and a `/run/maddy` tmpfs. The Makefile is now `.NOTPARALLEL` because `make -j` ran the Docker suites at once and they replaced each other's test image. Next: step 2 in SIMPLIFY.md, a local host facade with the `HelperClient.call` interface behind a setting.

## Stage 2 spike done (see SIMPLIFY.md)
The app can run the Maddy account commands itself if Maddy runs as 10001 and owns/shares the data directory. Needed on the VM later (T3, approval + restore point): Maddy container as 10001:10001, chown of `/opt/maddy/data`, TLS files readable, `/run/maddy` tmpfs owned by 10001. Next in the repo: let the backend run the helper logic in-process with a local-subprocess Maddy. Spike test kept outside the repo (scratchpad `test_spike_direct_cli.py`).

## Stage 1 progress (repo only, D-020/D-021)
Removed about 9,300 lines: qualification/evidence tools, mock-host/VM tooling, the `qualification` suite of `run_linux.py`, 25 test and support files that only exercised them, five historical `verification/` files; README rewritten. Passing: lint, typecheck, reads, unit (424), integration (94), `make plan`, `make browser`, `make linux-check` runtime/ACME/Maddy except one intermittent failure: `tests/runtime/test_api_crashes.py::test_sigkill_https_during_authorized_fixture_delete_never_repeats_removal` failed once (state `reconciliation_required`, expected `complete`) in a full run; it then passed 3 of 3 alone and in a full runtime rerun. Cause unconfirmed; the test kills a process mid-delete, so timing under load is suspected. Still to do in Stage 1: nothing blocking; `docs/mail-admin-implementation/` and the run_remote runner stay until Stage 2 (host code loads the contract files at runtime).

## Timer fix prepared in the repo (D-018, not deployed)
New `host/systemd/mail-admin-backup-control.path` and `mail-admin-recovery.path` start each worker when its request database (`backup-control.db`, `recovery.db`) is closed after a write. Their timers are now 5-minute fallbacks (was 3 s and 2 s) and the idle catalog refresh is hourly (`CATALOG_REFRESH_SECONDS`, was 300 s). Expected: about 24 idle starts per hour instead of about 1,800, requests still start at once. Tests added: `tests/unit/test_systemd_units.py` and the hourly refresh test in `tests/unit/test_backup_control.py`; the restore installer fixture accepts `.path` units. Passing: lint, typecheck, unit, integration, and `make linux-check` (48 modules, Maddy, runtime and ACME included). Not verified: that `PathChanged` fires on the helper's SQLite writes on the real VM (check with a disposable test after deploy), and a self-retrigger loop is expected to stop after one idle run per job. Deploy needs: copy two units, `systemctl daemon-reload`, `systemctl enable --now` the two `.path` units, restart the two timers; rollback: disable the `.path` units and restore the old timer files (3 s and 2 s).

## Backup check (2026-11-20, read-only)
- The 9:46 PM Dhaka backup (15:47 UTC) succeeded: verified, all 9 mailboxes, snapshot `0e9c0c45`, recovered alert sent. `restore_passed` is false: no restore test has run.
- The 9:13 PM run (15:13 UTC, right after the reboot) exited 1 after 22 s with no reason in the journal: `host/cli.py` turns any `OSError`/`ServiceError` into an alert and exit 1 without logging it. In the same 15:13-15:14 window `mail-admin-backup-control` and `mail-admin-forwarding` also failed, so a boot-time race (Docker, network or Maddy not ready) is the likely cause. Not proven.
- `mail-admin-backup.service` still shows `failed` in `systemctl --failed` from that run. The control and forwarding timers have run cleanly since. The next scheduled backup is 03:00 Dhaka.
- Gap: failures are not logged. Fix candidate for a later phase: log the error class to the journal; order the units after Maddy.
- SSH: `known_hosts` holds only the IP `35.211.79.14`, so use `-o HostKeyAlias=35.211.79.14` (DNS and key match).

## Open items for the owner
- Billing: set a $0.01 budget alert; check the Billing report in a few days.
- Phase 1 needs: SSH restriction choice (own IP vs fail2ban), a new Cloudflare token scoped to DNS edit for the boot-time update.

## Blockers
None.

## Not verified
Old test suites (not run on this machine yet); whether unattended upgrades and fail2ban are on; the backup result for today; Cloudflare forwarding targets; full-server restore of the current snapshot.
