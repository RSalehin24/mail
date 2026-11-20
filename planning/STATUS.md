# Status

Updated 2026-11-20. Resume here; do not re-derive earlier decisions ([DECISIONS](DECISIONS.md)). The plan is [PLAN](PLAN.md).

## Current stage

**Planning complete; Phase 0 not started.** Done today: switched the VM to the Standard network tier and restarted it; updated the Cloudflare A record for `mail.rsalehin24.me` to 35.211.79.14; set up SSH access; wrote the plan and the Claude Code setup (`CLAUDE.md`, `AGENTS.md`, `.claude/`).

## Next action

1. Phase 0 is done. Next: confirm today's backup and the 9:13 PM boot-time failure (read-only), then Phase 1 (see PLAN).
2. Confirm the 2026-11-20 dashboard backup (started 9:46 PM Dhaka) finished, and read why the 9:13 PM boot-time run failed.
3. Delete `~/.cf_token` and revoke the temporary Cloudflare token (owner action).

## Phase 0 baseline (2026-11-20, Python 3.14, D-007 to D-015)
All suites pass locally: lint, typecheck, reads, unit (537), integration (94), build, browser, Maddy (31 modules), runtime (8), ACME (7). Fixes made to stale tests/fixtures, assertions unchanged: SMTP keyword case (D-009), inventory cache wait (D-014). The Maddy fixture image is a local rebuild with a different executable hash than the approved one, pinned by owner decision (D-013); weaker evidence than the original. Not run: `make plan`, qualification suite, any production check. Python 3.14 was used instead of 3.11.

## Open items for the owner
- Billing: set a $0.01 budget alert; check the Billing report in a few days.
- Phase 1 needs: SSH restriction choice (own IP vs fail2ban), a new Cloudflare token scoped to DNS edit for the boot-time update.

## Blockers
None.

## Not verified
Old test suites (not run on this machine yet); whether unattended upgrades and fail2ban are on; the backup result for today; Cloudflare forwarding targets; full-server restore of the current snapshot.
