# Forwarding and administration rollout — October 28, 2026

The application changes are deployed as `admin-af2d455d1922`, runtime image
`sha256:254751d849537852efed0249a6e7a02cba3d822c91668bfbea278b626b3091bb`.
Forwarding is in the main navigation and each mailbox's Configuration tab.
Settings → Security includes the email verification-code preference. Server shows
configured, recently verified, failed and stale monitoring observations distinctly.

## Delivery behavior

Cloudflare remains the main domain's MX provider. `delivery.rsalehin24.me` has a
separate MX to the existing Maddy host; the inbound-only recipient rewrite maps
its addresses to the corresponding canonical IMAP mailbox. The original main MX,
SPF records and unrelated contact Worker were preserved.

All nine local destinations were verified by the owner. The root synchronization
worker manages literal rules for those mailboxes. Native `message.forward()` sends
a local copy and, where enabled, a copy to the external destination. It does not
read or rebuild MIME, change the sender/subject, wrap mail in an attachment, or
resend as the administrator. The Worker attempts both deliveries concurrently.
`mail@rsalehin24.me` and `website@rsalehin24.me` forward to
`rsalehin24@gmail.com` while retaining local copies. The temporary forwarding rule
for `cloud@` was removed; its normal local incoming delivery remains active.

The old Gmail rules were retained until local delivery and Gmail receipt passed.
The owner confirmed finding a forwarding test in Gmail's Spam folder after being
asked about the original test `cb799474-8431-41d9-b571-f229dd37d07b` and trace
`29f20a7b-b493-440f-b5db-11c36ec31d9f`; the exact received identifier was not specified.
Local receipt, sender, subject and body text were verified; the upstream Brevo
transport rendered plain text as HTML before Cloudflare received it. Native
forwarding does not control Gmail's folder selection. Mark the test Not spam.

`website@` was provisioned with a generated password that was not retained.
Reset its mailbox password in the application before configuring an IMAP client.
All eight original mailbox credential/storage pairs and nine protections remain.

## New mailboxes and forwarding changes

After mailbox creation, the root timer automatically registers its local delivery
address with Cloudflare. Cloudflare sends a verification email directly to that
mailbox through the delivery subdomain. Open it in an IMAP client and click its
link once. The timer then activates the exact incoming route. This one-time
confirmation is required for each new incoming destination; SMTP submission is
independent and uses the mailbox credentials. No per-mailbox Cloudflare dashboard
setup is required. Incoming delivery status is shown on Forwarding.

The main Forwarding page and per-mailbox controls use the same root-owned rule
store. Saved changes are revision-bound. The timer synchronizes roughly every
30 seconds; the UI distinguishes pending synchronization, local verification,
external-destination verification, connected, failed and stale states. Changes do
not claim success at the provider merely because a local rule was saved.
Disabling/removing forwarding stops external copies after synchronization while
retaining incoming local delivery. Existing mailbox messages are never replayed.

For a new external destination, save the rule and select Verify destination. The
owner of that address must confirm Cloudflare's email. Unverified external
addresses never receive forwarded mail. Verification requests are explicit and
record uncertain outcomes without blindly repeating external mutations.

## Authority, monitoring and email codes

Cloudflare credentials remain root-owned mode 0600 under `/etc/mail-admin/secrets`.
The non-root dashboard has no token mount. Recently authenticated API writes use
the restricted Unix helper. Root `host.cloudflare_forwarding` uses fixed account,
zone, Worker and domain scopes; redirects are rejected. Its systemd service/timer
are installed, and credentials/configuration/rules are covered by full backups.

The independent Google monitoring verifier remains deployed and active. The new
status display exposes its actual confirmation and freshness, including failed
verification instead of presenting old activation as current health.

The explicit administrator test `f867193c-f1b8-4291-bb32-074701ea4ba6` from
`mail@` to Gmail was received and confirmed against the current provider revision.
Email verification codes remain disabled until the administrator enables the new
Security switch with recent authentication. Authenticator and recovery codes
remain available. Provider changes invalidate delivery readiness again.

## Verification and recovery

Unit/API checks passed (531 unit, 93 API). Native modules cover SMTP/IMAP, inbound
rewriting, SMTP submission, credential/sender restrictions, queue persistence,
mailbox recovery, backups, HTTPS runtime and certificate lifecycle. The full
Maddy/runtime/ACME aggregation fixes were exercised with actual modules and retain
per-module reports; missing/unreadable results cannot become passes. Both runtime
and controller images are pinned for suite lifetime. Browser checks cover shared
forwarding edits, disabled/unverified states, email settings and SMTP secrecy.
Actual HTTPS/non-root/Unix-helper browser checks also passed. Earlier failures are
retained separately, including outdated browser assertions and an unpinned test
controller disappearing during an overlapping build.

Production snapshot
`0b7e10a132dec5c0426728d5075087d7dc941a690bb3553bcc9d590aa60c9ab2`
(cutoff `2026-10-28T11:46:29.321431+00:00`, before this rollout) was copied to the
exact disposable local VM `aa07a899-704b-4535-8c26-a2e242ffd78d`. All full-state
roles and saved artifact hashes were checked; copied authentication was closed
and replaced. Snapshot ownership and Docker manifest-to-configuration identity
mapping were explicitly preserved. The recovered Maddy/dashboard had networking
disabled, and provider/backup/monitoring timers were not installed in the VM.
After a real kernel reboot, both consumers and the helper resumed automatically;
fresh HTTPS authentication, all eight original mailbox credentials, all message
bodies, and all eight Sent folders passed. Production data/authentication were
never replaced by this exercise. This is an isolated boot test of the identified
snapshot, not a public DNS/failover test or a reboot of the production server.

Evidence is retained in `build/admin-followup/`, including deployment, live assets,
local/Gmail receipt, shared-rule synchronization, complete native module results,
authenticated HTTPS, recovery activation and kernel-boot observations. Immediate
application rollback is retained under
`/var/lib/mail-admin-application-releases/admin-af2d455d192273cf`.

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

## Forwarding page follow-up — 2026-10-28

Deployed `admin-9f0214958d38` (`sha256:7773bb71d16ec86fb69a565cadf5e777e18b6cab2dd3e6279b357a5a15353e63`). The page now separates Forwarding Rules and Incoming Delivery, preserves independent pagination while switching tabs, and displays connected/pending/failed/disabled states in green/amber/red/gray. Incoming Delivery paginates mailbox accounts, including newly created accounts that have not yet appeared in the provider observation.

A Verify delivery action appears for pending, unverified, stale, or failed connections. The recent-authenticated `/api/forwarding/verify-local` action accepts only an existing canonical mailbox and queues a check of its server-derived delivery alias. It reuses the protected provider worker; it does not expose credentials or accept arbitrary destinations. Users still confirm a new address through Cloudflare's verification email. An existing verification email is not resent. External destination verification can also be checked again after its email has been sent.

Validation: 16 forwarding unit/API/provider tests; Ruff; targeted mypy; frontend type/lint and production build; full synthetic browser flow with new pagination/tab persistence/verification actions, success/error colors, and desktop/mobile layout checks. Browser assets match the deployed runtime. Deployment retained the previous runtime and root helper with rollback records under `/var/lib/mail-admin-application-releases/admin-9f0214958d3839cd`. Live observation confirms nine connected and protected mailboxes, the two existing Gmail forwards, and no unresolved helper operations. Recovery artifacts were refreshed for this runtime and helper; this follow-up does not claim a new completed offsite snapshot or boot recovery.
