# Plan — simplify, harden and redesign Mail Admin

Written 2026-11-20. Decisions are in [DECISIONS](DECISIONS.md); progress in [STATUS](STATUS.md). The live system serves nine mailboxes; every phase ends with a working, rollback-able state.

## Goals
1. **Level 1** — safer, cheaper, lower-effort operations (firewall, automatic DNS on IP change, quieter timers, patching, cost guard).
2. **Level 2** — mail goes straight to Maddy; Cloudflare forwarding is removed; `mail@` and `website@` are forwarded to Gmail by Maddy.
3. **UI/UX** — the best interface this app can have (D-002: as far as it can go).
4. **Backend** — Rust only where measurements justify it (D-002); default is to keep Python.
5. **Claude Code setup** — done in this change (`CLAUDE.md`, `AGENTS.md`, `.claude/`).

## Release tiers (D-003)
| Tier | Scope | Gate |
|---|---|---|
| T0 | docs, config in repo | none |
| T1 | UI only | lint, typecheck, reads, unit, `make browser`; deploy with rollback kept |
| T2 | backend / API | T1 + integration + runtime tests |
| T3 | helper, host, DNS, mail path | T2 + Maddy/ACME suites where still valid, fresh restore point, written rollback |

Old tests may not work (owner, D-003). Phase 0 finds out. A test that fails because behavior changed on purpose is updated in the same change and logged; a test that fails for any other reason is fixed at the cause. Nothing is skipped or deleted to get green.

## Phase 0 — Baseline (no live change)
- Install the toolchain locally (`.venv` from `requirements-dev.lock`, `npm ci`); needs approval for the installs.
- Run `make lint typecheck reads unit integration plan`; record pass/fail per suite in STATUS. Known failures to expect: pinned Chrome checksum, native aggregation `PermissionError` (audit findings 2 and 3).
- Capture the live baseline: memory and CPU of `mail-admin`/`maddy`, timer cadence, 7-day egress, current backup list, DNS export (already saved; no secrets).
- Confirm today's backup run finished and the boot-time failure was only a startup race.
- **Exit:** a short table of which checks work, and the measured numbers Phase 4 needs.

## Phase 1 — Level 1 operations (T3 where it touches the VM)
1. **Firewall:** delete `default-allow-rdp`; limit `default-allow-ssh` to the owner's IP or confirm fail2ban (ask which). Verify SSH and mail ports after.
2. **Automatic DNS on IP change:** a small boot-time host service (stdlib only) updates the Cloudflare A record for `mail.rsalehin24.me` from the instance metadata IP. Uses a new token scoped to DNS edit on this zone, stored root-only on the VM. Test with a stop/start.
3. **Timer cadence (largest efficiency win):** measured 2026-11-20 at ~11% CPU pressure on an almost idle VM; the backup-control, recovery, forwarding and monitor timers start a Python process every few seconds (backup-control used ~5 CPU s per start). Done in the repo 2026-11-20 (D-018): path units start the backup-control and recovery workers when a request is written, their timers are 5-minute fallbacks, and the idle catalog refresh is hourly instead of every 5 minutes. Not deployed; re-measure after deploy.
3a. **Trim the host:** review and, with approval, disable `snapd` (~20 MB) and `google_osconfig` (~16 MB) after checking dependencies; add a 1 GiB swap file as out-of-memory protection (already described in `docs/mail-admin-implementation/design/architecture.md`).
3b. **Serve the dashboard through Cloudflare** on its own proxied hostname (for example `admin.rsalehin24.me`) so static assets come from the cache and save egress; mail stays on `mail.rsalehin24.me`, unproxied. Needs a DNS, certificate and CSRF/origin check review (T3).
4. **Patching and cost:** confirm unattended security upgrades, set a $0.01 billing alert, check egress after the next full billing day, document the Standard-tier rule.
5. **Docs:** update the old IP in README/plan docs (not baseline/observation records); write a one-page runbook (deploy, restore, rotate secrets, what each timer does).
- **Exit:** firewall tight, a stop/start needs no manual DNS edit, runbook committed.

## Phase 2 — Level 2: direct mail to Maddy (T3)
Spike first, change later.
1. **Inbound hardening check:** port 25 reachable from outside, TLS on `mail.rsalehin24.me`, SPF/DKIM/DMARC checks, message-size and rate limits, per-address behavior for all nine mailboxes. Confirm `postmaster` resolves (known gap in the baseline).
2. **Forwarding by Maddy to Gmail (D-001). Main risk:** a forwarded message re-sent through Brevo keeps the original From, so Gmail sees SPF and DKIM that do not align with that domain and may reject or spam-folder it (more so for senders with strict DMARC). Spike two designs on a test address:
   - rewrite the envelope sender and set `From: "<original> via rsalehin24.me" <mail@rsalehin24.me>` with `Reply-To` set to the original (recommended starting point);
   - or SRS-style envelope rewrite only.
   Decide from the spike with the owner; build it in `shared/` Maddy config generation and `helper/forwarding.py`, with tests.
3. **Dry run:** create a test mailbox on a test hostname; send from Gmail, Outlook and a strict-DMARC sender; verify delivery, headers, spam placement and forwarding.
4. **Cutover:** record MX/TTL, lower TTL a day ahead, restore point first, replace root MX with `mail.rsalehin24.me` priority 10, disable (do not delete) Cloudflare Email Routing rules. Verify with external senders to all nine addresses.
5. **Rollback:** restore the recorded MX and re-enable the routing rules; check both receivers for mail accepted during the switch.
6. **Cleanup (after 14 days stable, separate approval):** remove Cloudflare forwarding code (`host/cloudflare_*`, `helper/forwarding.py` Cloudflare parts, `backend/routes_forwarding.py`, the sync timer and UI), drop Cloudflare credentials from backups, update tests and docs.
- **Exit:** all nine addresses receive directly; `mail@` and `website@` reach Gmail without landing in spam; rollback never needed or proven.

## Phase 3 — UI/UX redesign (T1, vertical slices) — done 2026-11-21, see UI-NOTIFICATIONS.md and D-049 to D-060
Frontend is React 19 + MUI 9, about 1,900 lines. New dependencies only by approval (D-002 allows "as far as it can go", not unreviewed packages).
1. **Audit:** screenshots at 360/768/1280 px, light and dark; list friction points, jargon, dead ends, empty/error/loading states.
2. **Design system:** one MUI theme (type scale, spacing, colors, density, dark mode), shared components in `components.tsx`, consistent status language (OK / needs attention / failing) with plain wording.
3. **Information architecture:** a home screen that answers "is everything OK?" (mail receiving, sending, backups, certificate, disk/RAM); mailbox pages with a client-setup guide; settings split by task; recovery actions with clear consequences and confirmations.
4. **Flows:** sign-in and 2FA, create mailbox (password handling, client config), forwarding, backup/restore, relay test, diagnostics.
5. **Quality:** keyboard and screen-reader support, axe checks in the browser suite, mobile-first layouts, no layout shift, fast first paint.
6. Ship one page at a time through T1; update `make browser` flows with each slice.
- **Exit:** every page redesigned, browser suite green at three widths, no regression in recent-authentication and recovery safeguards.

## Phase 4 — Backend in Rust: decision gate (T2/T3)
Default is **not** to rewrite. Measured 2026-11-20: dashboard 51 MiB of its 128 MiB limit, 0.4% CPU; Maddy 6.5 MiB; VM 458 of 952 MB used, 494 MB free. A Rust port would save roughly 35 MB, so it is not justified today. Open the gate only if Phase 0 shows memory pressure on the 953 MiB VM, unacceptable latency, or a maintenance problem Python cannot fix. If opened: ask for approval of the Rust toolchain and each crate, port one route group at a time behind the same API contract (`docs/mail-admin-implementation/requirements/contracts.json`), keep the root helper protocol unchanged until last, and never change the security boundary (no Docker access, read-only container, Unix-socket helper).

## Phase 5 — Release and wrap-up
Each phase deploys through its tier with the previous release retained for rollback. At the end: refresh the backup and run a restore drill on a disposable VM; update the runbook; archive the old evidence; set the status file to "operations".

## Order and effort (rough)
Phase 0: 1 session. Phase 1: 2 sessions. Phase 2: 3–4 sessions plus a 14-day soak. Phase 3: the largest (6+ sessions). Phase 4: only if the gate opens. Phases 1 and 3 can proceed in parallel; Phase 2 should not overlap a UI deploy.

## Risks
- Forwarding through Brevo breaks DMARC alignment (Phase 2 spike).
- Exposing port 25 to spam on 953 MiB RAM; measure first, add limits.
- Old qualification tests no longer match; re-baselining must not hide real regressions.
- DNS or MX mistakes interrupt live mail; always record, lower TTL, and keep the rollback.
- Free-tier drift: backups to R2 and Brevo traffic count against the 1 GB egress; recheck after changes.
