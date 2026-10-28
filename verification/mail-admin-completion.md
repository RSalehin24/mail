# Mail admin production state — 27 October 2026

This record supersedes older production-state summaries in the handover files.

## Current deployment

- URL: https://mail.rsalehin24.me
- Version: `mail-admin-ui-5ab98ed4b639`
- Source: `5ab98ed4b6392eb2df4a14331f4979703db2ea46f7637fd78a0b33cbac9544ed`
- Runtime image: `sha256:429cf876ab1cdac44d02e28d451e5c6662b4bef3e281ad0ae1a176c2240c0b07`
- Deployed: `2026-10-27T13:33:47.577806+00:00`.
- All eight credential/storage pairs, administrator authentication, mailbox metadata,
  and the versioned root recovery policy were preserved. Nine protected addresses
  remain protected. Recoverable deletion is enabled with a seven-day default.
  Permanent deletion remains disabled.
- Rollback configuration, immutable previous image, previous host modules, policy
  qualification marker, container configuration and compatible database copy:
  `/var/lib/mail-admin-application-releases/5ab98ed4b6392eb2df4a14331f4979703db2ea46f7637fd78a0b33cbac9544ed`.

## Server disk cleanup — 27 October 2026

Confirmed unused release uploads, nine superseded staging directories, six
explicitly retired local snapshot captures, four obsolete image-archive copies,
two temporary upload archives and six superseded rollback containers/images were
removed. Old release evidence was archived off-server in the private
`build/server-tidy-historical-evidence.tar.gz` before staging cleanup. Historical
application records remain; their obsolete image bundles are deliberately retired.
The current release, immediate rollback release/container/image, original baseline
recovery sources, active mail/configuration/key files and the one verified R2
snapshot are retained. Unused temporary operator scripts and package downloads
were removed; archived system journals were reduced to 93 MiB.

Disk usage fell by **3.47 GiB**, to **4.77 GiB used** and
**23.16 GiB free** of 27.95 GiB. HTTPS, mail/dashboard containers,
helper and backup/recovery timers are healthy. Before/after mail, authentication,
configuration and protection fingerprints matched; eight account pairs and nine
protections remain unchanged. No production mailbox data or R2 snapshots were
deleted. Evidence: `build/server-tidy-result.json`, `build/server-tidy-final.json`,
`build/server-tidy-live-observation.json` and `build/server-tidy-summary.json`.

The renewal tool also removed its completed Certbot container through its native
state-aware cleanup. Three unused, empty qualification volumes were removed.
Evidence: `build/server-tidy-final-extras.json`.

## Direct protection actions and AM schedule

The form stays above the protected-mailbox table. It has one primary action:
Add protection for a new entry, or Save reason while editing. Both persist directly
after required authentication; there is no separate Save protections step. Every
row has visible outlined Edit reason and Remove protection buttons, stacked on
mobile. Removal requires explicit consequence acknowledgment and recent
authentication, saves directly, and removes only the deletion safeguard. Mailbox
contents and account credentials are not deleted. Cancellation preserves the
protection; failed saves preserve the form and display an error.

The backup overview displays `3:00 AM · Asia/Dhaka · 7-day retention`.
The previous clearer backup layout, copyable snapshot IDs, verification badges,
conditional Logs Actions column, friendly errors without diagnostic IDs, and
`Aug 10, 2026` date formatting remain in place.

Fresh checks passed 504 unit tests, 88 API tests, eight runtime tests, real Maddy
merge/worker/recovery HTTPS checks and authenticated HTTPS browser checks.
Browser checks covered direct add/edit/removal through refresh, cancellation,
acknowledgment, failed-save preservation and retry, and the AM schedule text.
Light/dark layouts were checked at 360, 768 and 1280 pixels. The 16-protection
backend limit is unchanged; the browser fixture leaves capacity for its add test.
No host backup/recovery code changed.

Evidence: `build/single-protection-release-update/`,
`build/single-protection-deployment.log`, `build/single-protection-live-observation.json`,
`build/single-protection-live-assets.json`, `build/single-protection-failure-checks.json`
and `build/single-protection-visuals-checks.json`.

## Settings and pagination

Settings has Security, Outgoing mail provider, and Backup & Recovery tabs.
Deleted Mailboxes has Configure, Protected Mailboxes, and Deleted Mailboxes;
the mailbox list opens by default. Backup & Recovery opens Backups, with complete
operation history under Logs. Tab selections survive refresh through the URL.
Legacy Settings deletion/protection links redirect to the new destinations.
The Server page was not changed.

Numbered pagination is implemented for the mailbox dashboard, protected addresses,
deleted mailboxes, backup
snapshots, backup operations, administrator delivery history, and individual
mailbox delivery history. Controls provide first/previous/next/last, ellipses,
two boundary pages, one adjacent page, and 5/10/20/50 rows (default 10). Pages clamp
after records change. History endpoints expose older retained revisions instead
of paginating only an already-truncated result. Fixed security cards, retention
options and configuration fields do not need pagination.

Don’t Retain means permanent mailbox deletion without a mailbox recovery copy.
It requires Allow permanent deletion, recent authentication, typed address
confirmation and consequence acknowledgment. Selecting it as the default also
requires consequence acknowledgment; an incompatible root policy is rejected.
Independent historical host backups remain subject to their own retention.

## Backup and restore behavior

- Authenticated `/api/backups` lists the root-observed encrypted repository catalog
  with pagination and original observation times. `/api/backups/operations` queues
  strictly validated, durable jobs; operation-status routes survive disconnects.
- Manual and scheduled backups have durable operation records. Failed backup jobs
  offer Retry. Uncertain completion first offers Reconcile, which verifies command
  exit/repository state without replaying a mutation, then allows a fresh retry.
- Each snapshot offers Restore mail data. Verification extracts and integrity-checks
  the exact snapshot privately before live mutation. Restore merges missing mail
  and folders for ALL backed-up mailboxes, preserving newer mail and current
  passwords. Every missing address requires a new password before submission.
- A verified safety backup precedes import. Message dates, flags, subscriptions,
  mailbox metadata for recreated addresses and mailbox generations are preserved.
  An attached-database transaction commits storage, credentials, metadata,
  generations and an import marker together. Durable body plans constrain rollback
  cleanup to the exact operation’s own unreferenced files. Reconciliation recognizes
  committed imports without replay or duplicate messages.
- Restore pauses Maddy, not the dashboard, during bounded import. Admission rejects
  excessive space/work before stopping mail: at most 32 backed-up mailboxes, 2,000
  combined body objects and 32 MiB of combined live/source body and live database
  work; one GiB remains reserved. The import alarm is 480 seconds. Verified full
  snapshot extraction has a separate repository/storage budget. These conservative
  limits intentionally refuse larger restores through this UI.
- Private extracted preflights expire after one day unless an unresolved restore
  needs them. Recovery copies, keys and root policy remain included in host backup
  sources. Active requested snapshots are protected against normal repository pruning.
- `mail-admin-backup-control.timer` processes jobs; recovery, backup, monitoring and
  TLS-observation timers remain enabled. Root authority and operation locks are retained.

## Current-source verification and retention

The daily-retention release passed Ruff, strict mypy (103 source files), frontend
lint/build and bounded-read checks, 504 unit tests and 88 API tests. Matching-source
native tests cover daily replacement with an unverified candidate, recovery pins,
interrupted pruning and restoration from retained deduplicated data; native
Restic recovery and process reconciliation, Maddy merge/reconciliation, mailbox
recovery workers, HTTPS recovery and eight runtime checks also passed.
Authenticated browser checks used actual HTTPS and the Unix helper transport.
Additional browser checks verified legacy redirects, tab persistence, row-specific
restore/reconciliation and expired restore refusal, pagination ellipses/filtering,
and light/dark layouts at 360, 768 and 1280 pixels. Live static assets match.

The policy retains the latest verified snapshot per Asia/Dhaka calendar day
within the existing seven-day window. Extra manual/safety snapshots remain pinned
when required by unresolved recovery. A verified post-restore backup precedes
retiring safety copies. Backup creation remains successful if cleanup or catalog
refresh subsequently fails; that separate status appears in Logs. Verification,
cleanup decisions and retired baseline/rollback IDs are durable root-owned records
under the configured Restic state directory's `retention/` subdirectory. Historical
qualification evidence is preserved.

Current evidence: `build/daily-final-release-update/`,
`build/daily-final-deployment.log`, `build/daily-final-live-assets.json`,
`build/daily-row-actions-checks.json`, `build/daily-visuals-checks.json`, and
`build/https-browser-d90de8fd-fc0b-429c-9894-526c1ddc04f2/observation.json`.

## Current production retention acceptance

Initial cleanup freshly verified the newest existing snapshot and removed all six
older snapshots through native Restic forget/prune. A subsequent verified backup
successfully used the audited retired-reference record and replaced the earlier
same-day snapshot. The refreshed catalog contains exactly one snapshot:
`e7664950c48694270688a37bc69cb93820d62c24112d6035d7260570ccbaf5e2`.
Cleanup is complete, all eight credential/storage pairs and nine protected
addresses are preserved, recovery qualification matches the installed source,
and all three backup/recovery timers are active. The schedule remains
`*-*-* 03:00:00 Asia/Dhaka`. All three recorded operations remain in Logs, including
the cleanup record whose original snapshot has since expired.

Evidence: `build/daily-live-verification.log`,
`build/daily-final-observation.json`, and `build/daily-retention-evidence.json`.
The last file includes an independent copy of the audited retirement record;
retain it with recovery materials if rebuilding the root repository state.
Production messages were not restored, merged or deleted during this rollout.

## Historical Settings release verification

- Ruff, strict mypy (101 source files), frontend lint/types and request-scheduler
  checks passed; 501 unit tests and 88 API tests passed.
- Eight runtime container tests passed on the final image. All eight runtime
  modules (21 tests) also passed before the final frontend-only dialog correction.
  Initial host report
  aggregation encountered root-owned artifact permissions; ownership was corrected
  and unchanged module reports were validated and aggregated.
- Actual Maddy merge tests passed for all-mailbox restoration, newer-mail retention,
  existing passwords, missing-mailbox replacement passwords, folders/flags, repeat
  restoration without duplicates, transaction interruption, own-file rollback and
  committed-response-loss reconciliation.
- Existing encrypted mailbox deletion/restore worker and actual HTTPS recovery tests
  passed against this exact source. Actual native Restic restore and surviving-process
  reconciliation tests passed as well.
- Browser checks passed on loopback and actual HTTPS/Unix-helper infrastructure.
  Forty-two additional screenshots cover all four Settings tabs, Server, Deleted
  and the deletion dialog in light/dark at 360/768/1280 pixels. Layout overflow
  assertions passed and the screenshots were visually inspected.
- Live HTML, JavaScript, CSS, favicon and web manifest match the qualified build.
  Authentication remains required for inventory, recovery, telemetry and backup APIs.

Evidence: `build/settings-final-check.log`, `build/settings-runtime-all.json`,
`build/settings-final-merge-real.log`, `build/settings-final-recovery-worker.log`,
`build/settings-final-recovery-https.log`, `build/settings-final-restic-restore.log`,
`build/settings-final-restic-reconciliation.log`, `build/settings-visual-checks.json`,
`build/settings-final-live-assets.json`, `build/settings-final-deployment.log`, and
`build/https-browser-b0b51879-8429-488b-a15e-f2497243866e/observation.json`.

## Historical production backup evidence and remaining limits

The new catalog observed six existing snapshots after activation. Fresh backup
operation `ecf07d98-8534-44da-a220-2468664e27bc` completed successfully, with cutoff
`2026-10-27T11:20:43.559080Z`. Exact snapshot
`d9f8fe16845ddd2e264a7e9499edef4b552bef300efa8e57c2fc1320f7637384` passed
encrypted off-host publication and restored-snapshot integrity verification; the
catalog then contained seven snapshots. This backup predates the final frontend-only
restore-dialog warning correction; mailbox recovery/root import code is identical.
Current immutable runtime image and matching release evidence were included in the
host backup source directory for subsequent captures. Evidence:
`build/settings-production-backup.json` and `build/settings-final-live-verification.log`.
The actual production Python 3.14.4 / SQLite 3.46.1 runtime also passed atomic
kill/commit and encrypted roundtrip/tamper-refusal checks:
`build/settings-production-host-selftest.json`.
Production mailbox contents were not rolled back or merged during deployment.
The live restore functionality was exercised against isolated real-mail fixtures.

A booted full-server recovery of the latest production snapshot and a subsequent
production reboot have not been recorded here. Successful encrypted snapshot
integrity verification, isolated fixture recovery and live mail-data restore
preflight must not be described as a successful full-server boot recovery.
