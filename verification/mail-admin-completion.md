# Mail admin production state — 28 October 2026

This record supersedes older production-state summaries in the handover files.

## Forwarding and administration rollout — 28 October 2026

Current live version: `admin-af2d455d1922`.
Runtime image:
`sha256:254751d849537852efed0249a6e7a02cba3d822c91668bfbea278b626b3091bb`.
The runtime artifact source is
`af2d455d192273cfd5eefd0720ad573c9c713f01c6f8740518088c1e2c292b08`;
root `application_release.host_followup` separately records the verified backup
lock integration added for Cloudflare synchronization state.

Forwarding is live in the navigation and each mailbox's Configuration tab.
Settings → Security has the recently authenticated email-code switch, and Server
shows current independent-monitor verification/freshness. The administrator's
explicit delivery test was received and revision-confirmed; email-code login
remains off until the administrator enables it. Existing authenticator and
recovery-code access were preserved.

Cloudflare main-domain MX records remain unchanged. Nine verified local delivery
aliases feed the existing Maddy host through `delivery.rsalehin24.me`; the
inbound-only recipient rewrite keeps the canonical mailbox addresses. All nine
mailboxes now receive locally. `mail@` and `website@` also forward natively to
`rsalehin24@gmail.com`; sender, subject and body are preserved, without attachment
wrapping or administrator resending. The owner confirmed the forwarding test in
Gmail Spam. Existing Gmail routes were retained until this verification passed.
The temporary cloud@ forwarding rule was removed afterward.

New mailboxes are registered automatically by the root synchronization timer.
Each new local incoming destination needs one Cloudflare email-link confirmation
in its IMAP inbox; routing activates automatically after confirmation. The app
shows pending verification/synchronization and actual connected/failed/stale
states. External destinations have an explicit verification action. Removing a
forwarding rule retains local incoming mail and existing stored messages.

A local `website@` mailbox was added; its generated password was not retained.
Reset that password in the app before first IMAP client use. The eight original
mailbox credential/storage pairs, their password hashes and account rows, nine
protections, administrator authentication and root recovery policy/key remain.

The complete native suite aggregation now passes: 87 mail/recovery tests in 31
modules, 21 runtime tests in eight modules, and 14 certificate/ACME tests in seven
modules. Reports are exported privately to the invoking user; missing/inaccessible
reports cannot become passes. Both runtime and controller images are pinned for
suite lifetime. Checks also passed 531 unit and 93 API tests, the feature/worker
and backup-lock regressions, synthetic browser workflows and actual authenticated
HTTPS/non-root/Unix-helper browser workflows. Earlier failed runner attempts are
retained with their causes rather than relabeled as passes.

Production snapshot `0b7e10a132dec5c0426728d5075087d7dc941a690bb3553bcc9d590aa60c9ab2`
(cutoff `2026-10-28T11:46:29.321431+00:00`, before this rollout) passed isolated
startup and a real kernel reboot in disposable VM
`aa07a899-704b-4535-8c26-a2e242ffd78d`. Fresh HTTPS authentication succeeded after
reboot, all eight original mailbox credentials and message bodies matched, and
all eight Sent folders survived. Copied authentication was closed and replaced;
provider networking and timers were isolated. This verifies the identified
snapshot's isolated boot recovery, not public DNS/failover or all provider flows.
Production was never rebooted or restored by that test. The VM is stopped.

Detailed behavior and evidence: [forwarding rollout](forwarding-implementation-status.md),
`build/admin-followup/`, and its `ui-deployment-final.log`, `live-observation.json`,
`live-assets.json`, native complete reports, authenticated HTTPS observation,
`recovery-boot.log`, and `verified-backup.log`.
Immediate application rollback is retained in
`/var/lib/mail-admin-application-releases/admin-af2d455d192273cf`; the previous
Maddy container/configuration and Cloudflare rule observations are also retained.


Final encrypted off-host snapshot:
`be0a4755d29940b0c14563eb5b8b18c16d7dd1f9441cbb4b6ffa80b8abf5775e`,
cutoff `2026-10-28T13:28:09.730304+00:00`. Publication, extraction/integrity and
retention completed. The captured tree contains the exact final host code,
Cloudflare credentials/configuration, root forwarding rules, outbound queue and
all nine Sent folders. The capture now also takes the forwarding synchronization
lock before recovery/renewal/mail locks, so provider verification writes cannot
race the immutable copy; a real contention regression and native snapshot test
passed. All five backup/recovery/forwarding/monitoring timers are active and no
operation remains unresolved. See `verification/admin-followup-acceptance.json`.

## SMTP submission rollout — 28 October 2026

Authenticated SMTP submission is deployed on `mail.rsalehin24.me:587` with required
STARTTLS. Clients use their full mailbox address and existing mailbox password for
both SMTP and IMAP 993/TLS. Envelope, From and Sender identities are restricted to
the authenticated mailbox. Maddy's durable outbound queue relays to Brevo on
587 with certificate-verified STARTTLS. Provider secrets are root-owned mode 0600;
recently authenticated provider saves synchronize through the restricted helper,
with revision-based interruption reconciliation and a durable rollback include.

Runtime image: `sha256:10c1823276b8dac8d0269a4c6c44a6dff40d2e73745dca73d9e817745842edfd`.
The isolated submission release source is
`4d881d8b3ee5499064b16a7514dda5cbb39f739d6a707d64f14afe7af807f9c6`;
additional verified host changes extend startup verification to 30 seconds and
make provider-sync cleanup durable. Their hashes are recorded in the root
`application_release.host_followup` record. Unrelated uncommitted forwarding and
security work in the workstation was preserved and excluded from this rollout.

Clients must enable saving sent messages to the account's server IMAP Sent folder.
Maddy does not create a second copy. Sent records submission to the local queue;
it does not prove recipient delivery. Connection instructions no longer reveal
or copy the Brevo key. See [client and recovery details](smtp-submission.md).

Public 587 STARTTLS, rejection before authentication, certificate trust and existing
25/993 services passed. A single explicit live test, identifier
`673fcce5-a900-4904-b8d7-ef9c77e063fc`, was submitted by temporary mailbox
`submission-check-20261028@rsalehin24.me` to `rsalehin24@gmail.com`. Brevo accepted
it on the first attempt and the recipient explicitly confirmed receipt. Exactly
one client-created Sent copy was visible from a second public IMAP session. The
temporary mailbox was removed. All eight original credential/storage pairs and
credential hashes were preserved; nine protections, recovery policy and key were
preserved. No unresolved helper operation or provider synchronization remains.

Queue data is covered by the complete Maddy directory backup. `maddy_message_paths`
remains `['messages']`, because mailbox recovery interprets it as the IMAP body
store, not as a list of all backup directories. Recovery artifacts include the
current Maddy/dashboard images, host code, helper unit and restricted include.
Full-server boot recovery is not established by this rollout.

Verification includes 523 workstation unit tests and 92 API tests before the
small final host-only fixes, 508 isolated-release unit and 90 API checks, six
real Maddy 0.9.6 submission/relay/Sent checks, eight mail/recovery checks, nine
HTTPS runtime/recovery checks, browser checks, and fresh startup, synchronization
and real mail-data recovery regression checks after the host fixes. The controlled
relay exercised success, 451 queue persistence across restart, permanent rejection,
missing STARTTLS and certificate failure; the Sent flow includes offline restoration.

Rollback containers `maddy-submission-rollback` and `mail-admin-submission-rollback`,
previous configuration, host files, compatible dashboard database and quiesced mail
safety copy are retained under
`/var/lib/mail-admin-application-releases/submission-4d881d8b3ee54990-r4`.
Deployment preflights and the failed short-readiness backup remain in the evidence;
the latter was reconciled without replaying a mail-data mutation before retry.

Final encrypted off-host snapshot
`0b7e10a132dec5c0426728d5075087d7dc941a690bb3553bcc9d590aa60c9ab2`
(cutoff `2026-10-28T11:46:29.321431+00:00`) passed restore/integrity verification.
Its captured tree includes the queue, all eight Sent folders and the exact final
helper code. Both TLS consumers and all three backup/recovery timers are active;
no backup operation remains queued, running or awaiting reconciliation.

Evidence: `build/submission/`, including `deployment-r4.log`, `public-services.json`,
`live-result.json`, `live-relay.json`, `live-cleanup.log`, `live-provider-sync.log`,
`live-observation-final.json`, `final-recovery-regressions.log` and
`verified-final-backup.log`. The Google Cloud rule is
`mail-admin-submission-587`; existing 25/993 rules and Cloudflare routing were preserved.

## Deployment before the submission rollout

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
