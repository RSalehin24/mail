# Lightweight webmail and private profiles

Webmail is implemented behind `MAIL_ADMIN_WEBMAIL_ENABLED=false` by default.
No production application, mailbox, credentials, timer or feature flag is changed
by the development fixtures. The existing administrator login still requires its
second factor. Mailbox sessions cannot use administrator routes, and administrator
sessions cannot use `/api/webmail/*` or `/api/profile/*`.

## Application behavior

The common login form accepts the existing administrator identity or an explicitly
enabled mailbox address. Mailbox authentication uses certificate-verified IMAPS.
The session response contains `role` and `user`. Each mailbox has an opaque stable
application ID bound to its INBOX UIDVALIDITY. A successfully deleted and recreated
account receives a new identity and cannot inherit profiles, labels, recovery codes
or sessions. Account lifecycle changes exclude concurrent mailbox I/O and invalidate
sessions before and after the helper call. Failed deletion preserves the profile
and authenticator. An unexpected INBOX generation change blocks login until root
reconciliation; it cannot authorize resetting the user's authenticator. Password
resets invalidate web sessions even when the helper result is uncertain.

Inbox, Sent, Drafts, Trash, Junk, Archive and custom folders use IMAP directly.
Lists contain 25 or 50 headers, flags and message references. Opening a message
sets Seen after fetching its displayable body. Lists, search and authenticated
attachment downloads use PEEK and do not mark messages read. References contain
folder, UIDVALIDITY and UID; sequence numbers are never mutation identifiers.
Selection is limited to the current page, and bulk requests accept at most 100
explicit references. Per-message outcomes distinguish completed, failed and
unattempted actions. The client clears selection before another attempt.

Labels use stable `wml_<opaque ID>` IMAP keywords. Only their names and colors live
in per-user SQLite metadata. Removing a label preserves all messages and makes its
old keyword inert. Label views span folders. Search supports sender, To/Cc/Bcc,
subject, body, internal-date range, unread and label filters. Searches omit Trash
and Junk unless selected; the label shortcut explicitly includes those folders.
Cross-folder results have a documented folder-then-newest-UID order. Search
retrieves UID sets and the requested page's headers, never entire mailbox bodies.
An HTTP search response completes or returns an explicit timeout within 10 seconds.
Folder discovery is capped at 100 selectable folders and search UID responses at
2 MiB; exceeding either returns an error.

Compose supports new mail, reply, reply-all, forward, attachments and server drafts.
Drafts save explicitly or every 30 seconds while the dirty composer is visible.
Failed saves stop automatic retries until a successful explicit save. Attachment
references follow the newly saved draft so repeated autosaves retain attachments.
From always identifies the session's mailbox. Display name, reply-to, plain-text
signature and theme are editable in Profile. The address is read-only. Profile
also supplies IMAP/SMTP instructions and security controls. Current password and
an enabled authenticator/recovery factor are required for security changes; all
web sessions are revoked after password or completed factor changes. Mail-client
authentication remains ordinary mailbox-password authentication.

Credentials are encrypted with a fresh process-only AES-GCM key in a vault until
session expiry. Only token verifiers and session metadata reach SQLite. Idle expiry is 15
minutes, absolute expiry 12 hours, and a sweep runs every minute. Logout, expiry,
account changes and a restart discard reusable credentials. Neither browser
storage nor logs receive mailbox credentials. Profiles never include administrator
notes, provider credentials or another user's metadata.

## Maddy 0.9.6 compatibility and sending safety

Native tests verified that Maddy 0.9.6 advertises MOVE and permanent keywords, but
does **not** advertise UIDPLUS, return APPENDUID/COPYUID, or support UID EXPUNGE.
APPEND is resolved through a unique server-generated header. MOVE is resolved
through a temporary keyword, removed once the destination UID is known. Native
second-client checks verify label/flag persistence. The server also combines two
negative search flags incorrectly; webmail intersects separate UNDELETED and
UNSEEN UID searches. Unicode filters use UTF-8 IMAP literals.

Sending records preparation, submitting, accepted, saving Sent, Sent saved and
draft cleanup separately. Replayed operation IDs only observe their recorded
result. An uncertain SMTP response never causes automatic resending. A confirmed
submission copies the frozen server draft to Sent with its operation keyword.
The Bcc header is retained in the private copy and removed from SMTP DATA. Sent
retry only searches/copies/cleans drafts; it never submits SMTP again. A lost COPY
acknowledgement is reconciled by its keyword. If no copy can be proven after an
uncertain COPY, the operation stays uncertain instead of risking a duplicate.
Restarted or stale in-progress operations become explicitly uncertain.
Submission means Maddy accepted the message, not that a recipient received it.

Because selective EXPUNGE is unavailable, superseded/sent drafts move to Trash.
Confirmed permanent deletion is queued, hidden with Deleted, and removed through
hourly maintenance. Broad EXPUNGE is never used. The UI explains queued deletion.
Maddy's native removal implementation is in its
[pinned CLI source](https://github.com/foxcpp/maddy/blob/v0.9.6/internal/cli/ctl/imap.go).

## Resource and HTML controls

Three dedicated webmail worker threads have a bounded queue of nine waiting
operations and a two-second admission wait. Administrator I/O and hashing retain
their own pools and HTTP capacity. Expensive MIME encoding/submission is serialized.
Compose requests wait in their own bounded queue before taking a shared I/O slot,
so waiting sends cannot occupy every mail worker. Idle time is tracked in memory
and persisted once per minute; session existence, identity and revocation are
still checked on every request. Search's ten-second deadline includes dependency
execution and waiting for I/O.

At most ten idle IMAP connections are retained, in addition to three admitted
active operations. Each connection belongs to one opaque web session and has a
per-session mutex; it is never lent to another user. Connections expire after
two idle minutes and close on logout, expiry, account changes and process exit.
The transport clears its authentication buffers before retaining a connection.
Visible mail views refresh every 60 seconds and stop polling hidden tabs.

Uploads stream through authenticated octet-stream routes. Incoming attachment
sections are fetched in 64 KiB chunks and spooled for authenticated downloads.
Combined outgoing attachments are limited to 10 MiB, encoded messages to 16 MiB.
The server enforces both limits independently of the browser. Temporary files use
an owner-only `.webmail-spool` directory beside the application database, avoiding
the existing 8 MiB RAM-backed `/tmp`. Temporary uploads total at most 32 MiB, with
a 64 MiB combined reservation budget for uploads and encoded/decoded downloads.
Interrupted uploads remove their files. Expiry, logout, shutdown and restart
remove temporary uploads; interrupted download results expire after one hour.
Successful draft saves and durable send preparation release their consumed uploads.

Email HTML passes a conservative allowlist sanitizer, loses CSS, scripts, forms,
embeds, SVG and event handlers, and renders in a sandboxed iframe with its own
restrictive CSP. Remote image URLs are omitted until the user explicitly requests
them. Downloads use attachment disposition, safe filenames and octet-stream content.

## Root Trash worker, backup and recovery

`host.webmail_retention` runs hourly through the supplied root systemd service and
timer. Webmail records Trash entry time immediately after a resolved move. The
worker reads a consistent read-only native store snapshot, so external-client
moves start retention when first observed, even with every user logged out.
Message delivery/header dates never determine retention.

Only due or explicitly confirmed deletion requests invoke removal. The worker
takes the existing renewal/Maddy maintenance locks and refuses unresolved helper
or certificate work. The stock CLI cannot atomically check UIDVALIDITY, so the
worker briefly quiesces the pinned mail writer **only when removals are due**,
then revalidates native account ID, INBOX generation, folder UIDVALIDITY and UID
before each restricted native `imap-msgs remove --uid --yes`. It never writes
directly to live SQLite. Up to 100 removals are processed per hourly run. Mail
clients may disconnect during that maintenance window. Observing entry timestamps
does not stop Maddy. Resume verifies the existing consumer's expected certificate
and restores its recorded intent.

Profiles, factor configuration, labels, session-support metadata, Trash clocks,
confirmed deletion requests and sending records share `admin_data.db`, already
included in consistent full-state backups. Deployment/environment and application
keys use the existing operator backup sources. Temporary spool files are not
needed for recovery and are removed when the runtime shuts down for capture.
Restoration invalidates web sessions, clears pending permanent deletions, marks
interrupted sends uncertain and disables cleanup. Root reconciliation validates
remaining native identities and restarts Trash clocks before enabling cleanup.

For an unexpected INBOX identity change, stop the dashboard and inspect the native
account before reconciliation. Use `python3 -m host.webmail_retention --reconcile
--retain-profile address` only when the same person still owns the account; this
preserves their profile and authenticator. Use `--new-user address` instead when
an address was recreated outside the managed account lifecycle. Both options
require the dashboard stopped, revoke sessions and validate the native account.

## Installation and gated rollout

1. Keep `MAIL_ADMIN_WEBMAIL_ENABLED=false`. Capture the existing consistent backup
   and stop the dashboard through the normal operator maintenance procedure.
2. Run the matched image's root one-off `python -m backend.operator webmail-migrate`
   with the existing database/installation/key mounts and webmail disabled. The
   additive migration is transactional and idempotent; public startup never
   creates missing webmail schema. Existing administrator schema version stays 1.
3. Install the matched host sources and
   `host/systemd/mail-admin-webmail-retention.{service,timer}` using the existing
   root deployment procedure. Its write paths assume `/opt/mail-admin/data`; adapt
   that unit to the selected protected deployment path when different. Run
   `python3 -m host.webmail_retention --reconcile`, then enable/start the timer.
   Public production startup refuses enabled webmail until schema, allowlist and
   reconciled retention configuration exist.
4. Complete the security, protocol, browser, maximum-attachment and sustained
   capacity checks against the selected artifacts and server budgets. Keep webmail
   disabled when any acceptance criterion fails. A capacity test fixture never
   approves a production deployment.
5. For an eligible pilot, set `MAIL_ADMIN_WEBMAIL_ALLOWED_MAILBOXES` to one test
   address and `MAIL_ADMIN_WEBMAIL_ENABLED=true` in the reviewed deployment's
   environment. Install the matched application through the existing deployment
   procedure. Expand the comma-separated allowlist only after pilot checks pass.
   Remove the flag to disable webmail; do not remove persisted user metadata.

Do not store permanent mailbox passwords for maintenance. Administrator password
reset changes the mailbox password and revokes web sessions; optional webmail
authenticator settings remain protected by their recovery codes.

## Verification records

- `build/webmail-unit.log`: unit suite, including streaming interruption, encoded
  message admission, bounded queue/cancellation and hostile content tests.
- `build/webmail-integration.log`: existing API suite and mailbox roles, profile
  isolation, two-step/recovery, password/session, reuse and attachment controls.
- `build/webmail-maddy-tests.log`: pinned 0.9.6 native webmail behavior, independent
  IMAP observation, sending uncertainty, Trash and offline removal.
- `build/webmail-browser.json`: responsive browser workflows against a real Maddy
  fixture. The browser connection is loopback HTTP; IMAP/SMTP use verified TLS.
- [Measured capacity evidence](webmail-capacity-acceptance.json) records failed
  qualification and the upgrade test target.

The capacity fixture uses the existing host, a separate application and
the pinned Maddy image, initially with the production 128 MiB application and
96 MiB/0.5 CPU Maddy limits. It contains ten
synthetic mailboxes with 1,001 messages each. Each mailbox has 100 different
body/envelope templates expanded through native IMAP COPY and one HTML message
with an attachment; all UIDs are distinct. The workload warms ten authenticated
sessions, then exercises lists, opens, searches, labels, uploads/downloads,
one send per user (including a 10 MiB attachment), administrator inventory reads
and a separate Trash observer. Administrator inventory in this fixture is
synthetic; it does not call the production helper. The observer runs without
mailbox passwords. No messages are sent outside the synthetic local domain.

The fixtures are synthetic and isolated. They do not establish a booted restore
of the complete current production server, independent penetration testing, or
recipient delivery. Existing production services remain unchanged during this work.

## Measured result and deployment decision

**Keep webmail disabled. The current server did not pass the capacity gate.**
The host reports 952 MiB RAM, no swap and an `e2-micro` machine class. Both final
workloads used ten warmed sessions, at least 10,010 distinct message UIDs and
120 seconds of mixed activity, including attachment transfers and sending.

| Isolated test budget | Listing p95 | Opening p95 | Administrator p95 | Result |
| --- | ---: | ---: | ---: | --- |
| App 128 MiB; Maddy 96 MiB / 0.5 CPU | 6.55 s | 3.01 s | 4.60 s | Application OOM-killed |
| App 256 MiB; Maddy 96 MiB / 1 CPU | 5.81 s | 4.40 s | 1.76 s | Maddy OOM-killed; host memory reserve failed |

The larger-budget test reached approximately **92.9 MiB available host memory**,
below the required 128 MiB. A guard stopped only its synthetic application when
the reserve fell below 96 MiB. Queued operations, connection failures and failed
sends mean neither run qualifies, regardless of individual timings. These timings
include failures/timeouts and the workstation's SSH transport. Each final Trash
observer completed 48 cycles; due native removal was exercised separately.

The tests show that the 128 MiB application and 96 MiB Maddy budgets are
insufficient for this workload. Require a deployment with more capacity before
the ten-user rollout and repeat the complete qualification. A practical next test target is **at least
2 GiB host RAM with a sustained CPU allocation**, evaluating an application
budget of 256 MiB and Maddy budget of 192 MiB. This is a proposed qualification
target, not a measured sufficient minimum; CPU sizing also remains unqualified.
The isolated test services coexisted with production, so these measurements do
not establish an exact minimum for a replacement deployment.

Functional verification passed 547 unit tests, 103 API tests, the native Maddy
webmail suite, five browser workflow groups and the existing administrator browser
checks. The native suite also exercises a 10 MiB outgoing attachment with a larger
local test budget; that functional result does not qualify the failed server
budgets. Ruff, mypy (125 files), frontend lint/build/read checks and image building
passed. The review image is `mail-admin:webmail-review`; its backend source and
frontend assets match the measured/browser-tested files. Raw logs and image hashes
are under `build/webmail-*`.

All eight remote synthetic containers, their private data tree and the local
webmail browser fixture were removed. Production container IDs, images and start
times stayed unchanged. The production HTTPS health endpoint returned 200 and
unauthenticated session inspection returned 401 after cleanup. No production
feature flag or timer was enabled.

## Five-user follow-up

A separate [five-user measurement](webmail-five-user-acceptance.json) also failed
the capacity gate. It kept ten synthetic mailboxes and 10,010 seed UIDs, with five
active mailboxes containing 5,005 of those messages. Each run lasted 120 seconds
after warmup and included a 10 MiB outgoing attachment, four 1 MiB sends, searches,
downloads, synthetic administrator reads and concurrent Trash observation.

| Isolated test budget | Listing p95 | Opening p95 | Result |
| --- | ---: | ---: | --- |
| App 128 MiB; Maddy 96 MiB / 0.5 CPU | 1.22 s | 1.20 s | Application OOM-killed early |
| App 256 MiB; Maddy 128 MiB / 1 CPU | 5.92 s | 4.95 s | Maddy OOM-killed; host reserve fell to 113.5 MiB |

The first run's good latency covered only the period before the application
failed. Raising its memory limit avoided that OOM but exposed Maddy's memory
limit and host reserve pressure; a guard stopped the synthetic application.
Neither result qualifies five full-feature users. Keep webmail disabled and
retain the proposed 2 GiB host as the next qualification target. These isolated
tests coexisted with production and do not establish an exact minimum for a
replacement deployment. Raw results are under `build/webmail-five-*`.

## One-user follow-up

The [one-user measurement](webmail-one-user-acceptance.json) confirms that one
user **passed the tested ordinary workload**, with a 1 MiB outgoing attachment,
on the current 1 GB-class server. It does not qualify the complete 10 MiB release.
Ten synthetic mailboxes contained 10,010 seed UIDs; only one mailbox, containing
1,001 seed messages, was active. Lists, opens, searches, labels, downloads,
uploading and local submission ran alongside synthetic administrator inventory
and root Trash observation.

| One-user test budget | Outgoing attachment | Result |
| --- | ---: | --- |
| App 128 MiB; Maddy 96 MiB / 0.5 CPU | 1 MiB | Ordinary workload passed, zero request/state errors and no OOM |
| App 128 MiB; Maddy 96 MiB / 0.5 CPU | 10 MiB | Application OOM-killed during preparation |
| App 192 MiB; Maddy 128 MiB / 1 CPU | 10 MiB | Maddy OOM-killed |
| App 192 MiB; Maddy 192 MiB / 1 CPU, GOGC 50, GOMEMLIMIT 96 MiB | 10 MiB | Host reserve fell to 119.9 MiB; guard stopped synthetic app; listing/download timeouts |

The ordinary run measured 121.7 seconds, with folder listing p95 **1.53 seconds**,
opening p95 **1.16 seconds**, synthetic administrator p95 **0.84 seconds**, and
maximum search time **7.00 seconds**. Memory sampling covered 113 seconds and
observed at least 276.1 MiB available. This partial sampling does not guarantee
the minimum for the full interval. Runtime OOM flags and sampled OOM counters
were zero. A two-minute ordinary workload is evidence of feasibility, not proof
of sustained operation or complete release acceptance.

The 128 MiB maximum-attachment run planned only 30 seconds and failed with an
application OOM. Later runs planned 120 seconds. The last run completed its local
send, but failed the host reserve and response-time criteria without an OOM.
Its sampler stopped at the reserve guard; subsequent connection errors followed
the intentional synthetic application stop. Earlier long-send reports also
counted the expected `operation_pending` response as an error; the final load
client correctly observes and polls the same durable sending operation. The
recorded OOM/reserve failures independently establish that the maximum workload
did not qualify. No automatic SMTP resubmission was introduced.

The final memory-management experiment used Go's soft runtime memory target; it
did not pass the workload. The [official Go GC guide](https://go.dev/doc/gc-guide#Memory_limit)
describes the target and its limits. These settings do not establish a safe
production budget.

Application source hashes match the previous measured source in all 125 checked
files. The load tool adds a selectable attachment size and pending-send polling.
Ruff and its CLI check passed. All seven owned remote fixture containers, their
private directory and local temporary credentials were removed. Production
container IDs/images match preflight, both services are running without Docker
OOM flags, and HTTPS returned 200 for health and 401 for an unauthenticated
session. Production start times changed to approximately 21:00 UTC on October 28,
after the measured synthetic workload. This task issued no production mutation
commands; the restart cause was not investigated. The evidence does not establish
uninterrupted production uptime. No webmail flag or maintenance timer was enabled.

## Separate webmail and mail servers

Keeping Maddy and all persistent messages on this server while hosting the React
frontend **and Python webmail backend** on a second server is feasible. The
backend already accesses messages through verified IMAP 993 and submits through
authenticated SMTP 587 with STARTTLS. Maddy documents its client ports in the
[official deployment documentation](https://maddy.email/docker/). Moving only
the static frontend would leave most of the processing here.

The second server would own profile, label, session and sending metadata and
temporary attachment spools. The mail server would retain message storage and
privileged account/maintenance functions. The current deployment shares a local
helper socket and application metadata with those functions, so the split needs
an authenticated inter-server integration for account changes, revocation,
retention and backup/recovery coordination. Do not expose the root Unix helper
directly or place live application SQLite on a network filesystem.

Two 1 GB servers provide separate memory budgets; capacity is not a shared pool.
This may relieve the shared-host pressure but does not remove Maddy's own message
processing costs or its existing 96 MiB container limit. No second server was
provisioned or tested. One-user maximum attachments, five concurrent users and
the full acceptance criteria require a new test on the actual split deployment.
Keep webmail disabled until that selected deployment qualifies.
