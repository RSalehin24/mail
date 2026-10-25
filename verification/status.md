# Implementation and verification status

The dashboard, API, persistent authentication, root helper, relay services and host
operation tools are implemented locally. Isolated verification is still in progress.
**The complete W01–W09 plan is not finished or release qualified.** No production
installation, live-service restart, mailbox mutation, external mail or DNS change
has been performed by the implementation run. Only explicitly authorized synthetic
fixtures have been added to the existing host.

Current source `a70f7897` is frozen in `build/release-candidate-a70f7897/`.
Its complete catalogue execution is in progress; no new acceptance total is assumed. The single 111-case
[work list](qualification-worklist.md) tracks the remaining work. New development
passes do not change the historical **69 passed / 0 failed / 42 not run / 111**.

The earlier source **`d1eb54cd`** remains frozen in
`build/release-candidate-d1eb54cd/`, artifact set
`c725e00b37f6e003d9ecb8c544358e2367e9a224c103f925b6f91116fa040c6e`.
It passes Ruff, strict mypy, frontend lint/type checks, **428 unit and 67 API tests**
(`build/check-expanded-operations-offline-isolation.log`). The application checks
and the passing specification checks (`build/plan-expanded-operations-corrected.log`)
have separate scopes. The new frozen catalogue has not completed.

The integrated fixes wire bounded 90-day terminal helper-journal cleanup into
the actual locked request path, prevent floating timestamp rounding from making
a healthy backup fixture appear future-dated, and isolate catalogue assertions in
fresh controllers. New tests cover guarded deletion, real writer quiescence,
surviving helper/Certbot children, certificate process crashes and finite logs.
The registry now contains **111 distinct local/host case mappings**. Kernel-boundary,
cutover and full replacement recovery bindings are implemented; their matching
runtime evidence is still required. Registration alone does not qualify any case.


Current full checks pass **452 unit and 72 API tests**, Ruff, strict mypy, frontend
lint/type checks and the specification suite
(`build/browser-dns-fixed-source-checks-20261025.log`). The frontend also passed
lint/type checks under pinned Node 24 (`build/frontend-node24-verification-20261025.log`).
These counts support the candidate; acceptance totals come only from its case reports.

Current focused development runs passed the approved isolated postmaster fix,
rollout rejection guards, actual install/upgrade observation, immutable HTTPS
transfer, actual HTTPS browser handling, combined application/helper/transient
memory including tmpfs, and real terminal recovery/maintenance coordination.
The corrected frozen HTTPS browser run passes
(`build/frozen-https-browser-a70f7897.log`). Its local DNS fixture explicitly observes
unknown MX status; the first frozen browser attempt remains failed in its original
`a0a5251c` bundle. The complete matching catalogue is in progress.


The matching `a70f7897` native comparison completed. Its first runtime attempt
retains missing-fixture setup errors and actual 15-second startup failures. After
staging the approved Maddy image and exact frozen test archive, the affected API
crash, mail-flow and schema modules pass; key-recovery restart timing still fails.
Production Maddy identity, mounts, configuration and start time remain unchanged.
The heavier native guarded-deletion gate is closed: 533,929,984 bytes available
against 570,425,344 required (`build/native-guarded-deletion-admission-a70f7897.json`).
No test deadline or memory cap was raised.

Two actual systemd legacy-drain assertions also passed at their original bounds
(`build/legacy-systemd-development-20261025.log`). The mock's nested cutover setup
was refused by its capacity gate: 620,843,008 bytes available versus 637,534,208
required for both controllers, Maddy and the unchanged reserve. Emulated TLS
attempts also retain Docker command/consumer-boundary deadline failures. No kernel
power-cut success or matching full replacement result has been claimed for them.

Development source `9a1abc88` passed **423 Linux unit and 67 Linux API tests**,
the real snapshot stop/resume assertion and the actual helper-parent SIGKILL
assertion (`build/snapshot-helper-current-checks.log`). Source `419a16fc` passed
all 12 private-ACME tests and four actual Maddy mutation-crash tests
(`build/private-operations-qualified-checks.log`). The later `a3c4c0be` run passed
all 21 HTTPS runtime assertions, but its expanded ACME suite passed 12 and failed
one new snapshot assertion (`build/independent-retention-checks.log`). That failure
was a stale private Docker port after restart; the corrected assertion passed at
`9a1abc88`. These results cannot fill the final candidate's reports. Process
SIGKILL results do not establish kernel-reboot behavior.

The native `8a5ad743` Maddy suite passed all 53 assertions across 17 modules, and
the unchanged normal HTTPS deletion assertion passed separately at `a3c4c0be`
under its original five-second deadline and 128 MiB controller. Their records are
`build/mail-admin-native-maddy-7dc51e2a-bf89-4bf5-96cd-994ccc29e68b/result.json`
and `build/mail-admin-normal-delete-82303fa6-2c6c-41b7-bebf-eb1f9b636d12/observed-runtime-14580c30-a287-44e2-91b7-55ede4c54697/result.json`.
The earlier emulated normal-deletion failure is retained. The `d1eb54cd`
native follow-up passed all 21 runtime assertions at the original bounds
(`build/native-startup-comparison-20261024.log`). Its Maddy capacity gate remained
closed; no failed emulated cold-start observation is replaced by later health.

Actual Docker log rotation passed at `a3c4c0be`. A separate owned-guest journal
probe wrote 64 MiB under the shipped 32 MiB limit, expired the first marker,
retained the last marker and observed 33,468,416 allocated bytes. Its result is
`build/private-journal-a3a9c1d7-edeb-46f0-be07-9f5f41792c62/observation-3371219a03394efe9e1ec11f329baa20.json`.
The probe retained the guest's older helper/runtime installation, so it is component
evidence only. The final T22.02 case requires matching local and host assertions;
missing host evidence stays `not_run`, and a local failure remains a failure.

The actual HTTPS Chrome fixture passed secret masking, persistence/profile scans,
untrusted text, safe API errors and bootstrap/lock ordering
(`build/https-browser-aeee88bc-c0b4-4109-9176-469bb17e9f5f/observation.json`).
This is development evidence. Frozen HTTPS browser, host/reboot, recovery, cutover
and complete catalogue execution still remain.

The pinned Maddy 0.9.5 bare SMTP `postmaster` failure remains recorded in
`build/guarded-pending-and-retention-checks.log`. The user approved the isolated
patch and pinned source/compiler inputs in `build/proposed-maddy-postmaster-fix.md`.
The Go SMTP parser suite and all three actual SMTP-to-IMAP recipient tests pass
against fixture image `sha256:11c98cb08dc68166b4238527b00088e0d8be175e9fb9528c210b45600d43bfaa`.
Production image selection remains separate.
Independent SMTP-sender credentials and an external monitor/provider are also
still required release inputs. No live-server change is authorized.

The frozen `8a5ad743` catalogue stopped at T20: Restic's initialization child was
OOM-killed after unrelated assertions accumulated in the same 192 MiB controller.
T20.05 failed, and the controller's observed `OOMKilled` flag stopped assembly.
Its original failure remains in `build/qualification-8a5ad743-resumed.log` and the
T20 controller progress record. There is no completed catalogue result for this
source. The new runner executes each registered assertion module once in a fresh
controller under the same limit, and assembles cases only from matching raw
module reports plus completed, non-OOM controller observations. Its regression
checks reject missing, skipped, foreign and killed outcomes. The actual Restic
restoration assertion passed in the new frozen development controller
(`build/fresh-restic-qualification-controller-actual.log`); this single assertion
does not qualify T20 or the complete catalogue. No application resource or
readiness limit was raised.

The strict helper-response checks and public-runtime bind guards have now been
merged locally, with their regression tests. The isolated development copy passed
**396 unit, 62 API/integration, 17 actual HTTPS runtime and 6 private-ACME tests**,
plus Ruff, strict mypy and frontend checks. Its runtime modules peaked at
117,555,200 bytes under the unchanged 128 MiB cap; the ACME controller peaked at
142,606,336 bytes under 192 MiB. Logs are
`build/next-runtime-isolation-full-check.log`,
`build/next-runtime-isolated-modules.log` and
`build/next-runtime-isolation-acme.log`. These records identify prototype source
`3f34376b` and do not qualify subsequent canonical-source changes. New local alert
checks exercise actual TLS-SMTP delivery during an unavailable dashboard, missed
backups, disk/inode/expiry warnings, retry ordering, recovery and a real stalled
TLS transport. Three actual API-process crash checks subsequently passed as
recorded below. T20.01/T20.03 and T22.04/T22.05 have reviewed mappings; the full
new frozen execution has not completed. No release count has been increased using
development reports.

Earlier source **`8a5ad743`** remains frozen separately in
`build/release-candidate-8a5ad743/`, artifact set
`ba7a99b7ab5b3fe511ebbc68c152bc995add763b82356cc0f4cf5cb4f5481764`.
Local full checks pass **397 unit and 66 API/integration tests** with Ruff,
strict mypy and frontend checks (`build/check-runtime-guards-and-expanded-faults.log`).
Linux unit checks pass **397 tests** (`build/linux-unit-8a5ad743.log`), but its Linux
API suite records **65 passed / one failed**. The failed new host-alert fixture
serialized an exactly-current floating timestamp to microseconds; rounding can
put the fixture cutoff fractionally in the future. The application correctly
rejects that age. A deterministic failing regression and corrected fixture pass
in the independent operations-development copy
(`build/alert-cutoff-rounding-before-fix.log` and
`build/alert-cutoff-rounding-fixed.log`). The frozen source remains unchanged and
cannot be promoted using an intermittently passing fixture. Its catalogue stopped
at the T20 OOM recorded above. The earlier Linux-suite failure remains a failure.
The seven new mappings have no successful complete frozen execution.
Actual partial Linux runs additionally passed three API SIGKILL tests and five
real image/mount/database/journal admission checks; they are distinct development
results (`build/runtime-api-crash-checks.log`, `build/linux-maddy-admission.log`,
`build/linux-maddy-journal-storage.log`).

At the user's request, unused local build-cache cleanup recovered **2.81 GiB** and
left 6,100,275,200 bytes free. No images or volumes were removed; active qualification
fixtures naturally changed container identities during the recorded interval.
The audit is
`build/approved-unused-cache-cleanup-50daffda-2a59-4b1a-840e-2aa7d2c30596/observation.json`.
Required application dependencies, recovery keys, reports and retained mock disks
were not part of that cleanup.

Latest completed frozen evidence at **`a9c00d9e`** records **69 passed / 42
`not_run` / zero failed out of 111**. All 22 controllers completed without OOM;
the maximum observed peak reached the unchanged 192 MiB cap. Its result is
`build/release-candidate-a9c00d9e/qualification-4fdf4f33-0546-4051-8392-9d2e52fe9e37/reviewed-summary.json`.
The candidate checker remains blocked (`build/release-check-a9c00d9e-candidate.log`).
It also has known response-validation and helper-bind-type defects discovered
during separate negative testing, so it cannot be promoted. Its missing host
cases are not filled by older reports.

Earlier completed frozen evidence at **`6ea4037d`** records **66 passed / 45
`not_run` / zero failed out of 111**. Its four T08 lifecycle cases passed all five
assertions in the owned replacement guest: an actual kernel reboot, late-helper
authorization, socket replacement, Docker restart and failed-dependency recovery.
The runner verified installed source, units, configuration and runtime-image
identities before and after execution. The unchanged report is
`build/release-candidate-6ea4037d/host-qualification-6dc88074-f269-41d2-830e-5a45b40086a7/T08.json`.
Validated combined references are recorded in that bundle's
`combined-host-observation.json`. The release checker still rejects the pending
frozen deployment inputs (`build/release-check-6ea4037d-with-host.log`).

The first current-image startup in this emulated guest exceeded the unchanged
15-second readiness bound. A later observation found it healthy, running and
not OOM-killed; that does not turn the first attempt into a pass. Both observations
remain in `build/prepare-current-host-context-6ea4037d.log` and
`build/current-host-web-startup-failure-diagnostics.log`. The subsequent lifecycle
report does not establish cold-start timing or production resource headroom.

New development source **`a9c00d9e`** adds actual TLS-SMTP/API sender-readiness checks,
real stalled TCP/UDP/Unix transport deadlines and overload checks, native SQLite
busy/corrupt/full failures, and clock-driven cleanup assertions against persistent
state. Local Ruff, strict mypy, frontend checks, **386 unit and 59 API/integration
tests** pass (`build/check-relay-storage-cleanup-audit-values.log`). Its complete
Linux/amd64 API/integration suite also passes **59 tests**
(`build/linux-integration-a9c00d9e.log`). The preceding `592e9da0` checkpoint passed
386 Linux unit tests. The final `a9c00d9e` source subsequently passed all **386 Linux
unit tests** as well (`build/linux-unit-a9c00d9e.log`). All seven new case mappings passed in the frozen `a9c00d9e` execution,
raising its catalogue count to 69. No application runtime code or dependency
changed in that batch.

The admitted qualification build passed its 2 GiB free-space check
(`build/build-qualification-a9c00d9e-admission.json`). An earlier rebuild was
mistakenly started after a failed disk check and finished before cancellation;
that attempt is not qualifying evidence. Its log and observation remain in
`build/build-qualification-a9c00d9e.log` and
`build/qualification-rebuild-below-disk-gate-observation.json`. The final local Linux catalogue subsequently completed after the approved
cleanup recovered disk headroom.
The approved limited cache cleanup was executed with durable before/after
identity checks. A corrected discard targeted Docker's separate Colima data
filesystem, rather than the system disk; this recovered free blocks without
removing allocated resources. A broader cleanup of five unused test images and
all unused build cache was explicitly approved and executed. Revalidation and the
durable before/after comparison confirm that only the five approved image identities
were removed; all containers, volumes and retained images were preserved. The result
is `build/approved-qualification-cleanup-b47dbf51-5530-48ab-828c-a07cc24a6f6f/observation.json`.
It observed 8,310,681,600 bytes free after the correct data-filesystem discard.

The frozen `a9c00d9e` catalogue completed from
`build/release-candidate-a9c00d9e/`, artifact set
`e2a82eed1c598b31604f7d1a4ffb2f17a7f85a9cd6172c71c3452bb9cd02892d`.
A separate development copy found that its helper client accepts JSON `true` and
`1.0` as protocol version 1, and truthy non-boolean success flags can falsely report
a committed mailbox result. The failing regressions are retained in
`build/next-helper-version-typed-protocol-before-fix.log` and
`build/next-helper-success-type-before-fix.log`. Strict type checks and their API regressions passed in the separate copy and
were subsequently merged locally; the frozen `a9c00d9e` artifacts remain unchanged.
This known defect blocks promotion regardless of the existing mapped-case results.
The first expanded actual-runtime suite passed 16/17 checks, including rejection
of malformed response types over HTTPS and the real Unix socket. The remaining
failure showed that a file mounted at the helper-directory target did not prevent
startup (`build/next-schema-response-runtime.log`). The next copy now validates
public-runtime bind types before starting workers; offline utilities retain their
separate no-helper/no-TLS path. Local Ruff, strict mypy (86 files), frontend checks,
**389 unit and 62 API/integration tests** pass
(`build/next-bind-guard-full-check.log`). Its rebuilt combined runtime suite was OOM-killed at the unchanged 128 MiB
controller cap during the mail-flow check. Kernel and Docker events identify the
killed controller; the web container remained running without an OOM. Its partial
results are not a suite pass (`build/next-bind-guard-runtime.log` and
`build/next-bind-guard-runtime-kernel.log`). The two exact synthetic orphan
containers were observed and removed; production resources were excluded.
The next-copy driver now reuses fresh module controllers with the same 128 MiB
runtime cap and requires all source/image-bound module reports before success.
Regression checks reject killed, missing, skipped, mismatched and duplicate
results. Its new actual-runtime execution passed all 17 checks across six fresh module
controllers (`build/next-runtime-isolated-modules.log`). No test limit was raised.
The next final-candidate host run will use the corrected runtime. Interrupted-DDL
and Compose bind-admission checks are also under development; static rendering
or local prototype passes do not qualify those runtime cases.

Completed development checkpoint **`47dccdce`** passed **369 unit, 46 API/integration,
48 real-Maddy tests across 15 modules, 9 HTTPS runtime tests, 6 private-ACME tests
and the browser check**. Unit/API suites also passed in Linux/amd64. Its combined
catalogue controller was subsequently OOM-killed at the unchanged 192 MiB limit
during T09; that catalogue run is incomplete. Its log, kernel evidence and owned
orphan cleanup are retained in `build/qualification-47dccdce*`. The frozen bundle
`build/release-candidate-47dccdce/` remains unchanged.

Completed source **`6ea4037d`** adds HTTP assertions for TOTP replay across setup and
factor replacement, actual container/key fault tests, and fresh catalogue group
controllers. It passes Ruff, strict mypy, frontend checks, **382 unit and 48
API/integration tests** (`build/check-catalogue-group-isolation.log`). The isolated
authentication/key checkpoint passed **11 actual HTTPS runtime tests**, including
container recreation and missing/wrong/unreadable encryption and verifier keys
(`build/next-persistent-key-runtime-build-directory-fixed.log`); this precedes the
catalogue-runner additions. The new runner records each controller identity before
starting, then records actual exit/OOM status and requires every group report before
assembly. No memory limit or application deadline was increased. Its complete
catalogue run completed with **62 passed / 49 `not_run` / zero failed out of 111**.
Reports are in `build/release-candidate-6ea4037d/qualification-4444b5c1-b642-4cba-a201-38497e01fd18/`;
artifact set `bcc5f89cfdd926e2a737098d726013d2ae22eee70a48a855a91c7df52ce8642e`.
All 22 recorded group controllers completed without OOM, and final assembly
validated their artifact/image identities and assertion sets. The largest observed
controller peak reached its 192 MiB cap. This is not a production-host peak or
complete qualification. The release checker remains blocked
(`build/release-check-6ea4037d.log`).

The same source subsequently passed **382 Linux unit and 48 Linux API/integration
tests** (`build/linux-unit-6ea4037d.log` and `build/linux-integration-6ea4037d.log`).
The actual R2 bucket has now been recorded in the mutable operator-input record.
A private local preparation file, bucket S3 credentials without the broad Cloudflare
token, and a separate 0600 Restic password were created under ignored `local-state/`.
No repository was initialized and no production configuration was installed. The
previously frozen `6ea4037d` deployment artifact still has its original pending
backup destination; its checker rejection remains accurate. Final configuration
must be frozen anew after the remaining release inputs and qualification are complete.

Earlier completed frozen acceptance result: **60 passed / 51 `not_run` out of 111**,
at source `512f66c5`. Its candidate checker remains blocked. Later work fixes backup
command output and native-suite supervision and adds a full replacement-host recovery
harness. Its development results remain separate from that frozen catalogue.

The completed expanded catalogue executed on native Linux/amd64 after its unchanged
spare-memory gate passed on a later admission attempt. Reports:
`build/release-candidate-512f66c5/qualification-07871b70-d73a-47a6-8aae-fbb5e5d6f6fa/`;
artifact set `e724f4dd85db4ebf8f008cafddd799b68678956bde291415fa526554751ea587`.
All 111 cases are recorded: 60 passed, 51 `not_run`, no failed mapped case.
The source/image-bound application assertions and separately verified T08 host
report were collected unchanged. The live Maddy before/after comparison passed
(`build/native-catalogue-retry-admission-512f66c5.log`). This is isolated qualification,
not production acceptance. Later `4c55859a` fixes need their own affected qualification.

The complete frozen `4c55859a` development suites subsequently passed **48 actual
Maddy tests across 15 modules and 9 actual HTTPS runtime tests**. The retained log is
`build/current-full-maddy-runtime-4c55859a.log`; its source-bound Linux reports are
separate from the acceptance catalogue above. Later recovery harness additions pass
local Ruff, strict mypy, frontend checks, **331 unit and 44 API tests** at the checkpoint
`build/check-replacement-harness-foundation.log`. Additional target guards pass three
targeted unit tests (`build/check-recovery-target-guards.log`). These results do not
establish full replacement-server recovery or change the 60/111 acceptance count.

The approved private ACME development suite now uses immutable Certbot 5.8.0,
Pebble 2.10.1 and challenge/DNS-helper images, listed in `tools/fixture_images.py`.
No test port is published and HTTP-01 validation remains enabled. The first actual
Certbot attempt rejected the fixture CA because its critical certificate-signing
key usage was missing. The new strict OpenSSL regression failed before correction
and passes afterward (`build/private-ca-strict-key-usage-before-fix.log` and
`build/private-ca-strict-key-usage-fixed.log`). Real issuance and ordinary no-op/due
renewal then passed in `build/private-acme-real-host-noop-and-renewal.log`. A later
negative-acquisition check preserved the pair but failed an over-specific diagnostic
assertion; that failed run is retained in
`build/private-acme-failure-diagnostics-retry.log`. Further consumer tests are still
development checks, without new frozen case claims. Current complete local checks
pass Ruff, strict mypy, frontend checks, **361 unit and 46 API/integration tests**
(`build/check-private-acme-consumers.log`). Separate specification checks pass in
`build/plan-private-acme-consumers.log`; they do not verify application behavior.

The first six-case extension passed acquisition, renewal, validation and first/missing
dashboard assertions, but its fresh-process activation worker omitted the original
private Maddy container name. That failed run is retained in
`build/private-acme-fresh-process-and-absence.log`. Identity reconstruction is fixed;
regressions additionally reject production consumer names and changed Compose paths.
The following rerun did not start because Mac disk exhaustion caused local Colima
metadata I/O errors (`build/private-acme-worker-identity-fixed.log`). It is not an
application pass and does not replace the worker failure.

An approved retirement removed only the stopped original synthetic source VM and its
disk after replacement recovery had been verified. The replacement VM, R2 recovery
point, recovery kit and Mac reports remain. The early removal observation saw no
substantial immediate free-space gain; a subsequent filesystem flush/discard observed
4,577,701,888 bytes free. Records are in
`build/approved-source-mock-retirement-0dfc7be3-cc59-49ab-8b8a-7ff86eea53dd/`.
Local Docker service restart initially failed; an orderly local Colima stop/start
recovered engine access. Both retained frozen artifact bundles passed integrity
checks. No production service was restarted. Some cached qualification content was
damaged: the image's helper journal source was empty while the checkout was intact.
The resulting import failures remain in
`build/private-acme-complete-after-storage-recovery.log`.

Build/fixture entry now enforces the existing 2 GiB native disk reserve on the actual
checkout filesystem, and image/source verification occurs before fixture volume
creation. The new regression failures and corrected checks are retained in
`build/fixture-disk-admission-before-fix.log`, `build/fixture-disk-admission-fixed.log`,
`build/fixture-image-admission-before-fix.log` and
`build/fixture-image-admission-and-packaged-driver.log`. The actual damaged image was
rejected before fixture creation in
`build/damaged-qualification-image-rejected-before-fixtures.log`.
Current complete local checks pass Ruff, strict mypy, frontend checks, **369 unit
and 46 API/integration tests** (`build/check-source-and-disk-admission.log`). The
runtime and qualification layers were rebuilt without the damaged cache before
further application runs. Four T15 assertion mappings have been added, but no new
frozen acceptance result has been claimed.

A fresh empty software-emulated guest subsequently restored the actual R2 full-state
point while the source VM stayed powered off. Private-terminal recovery established
a different synthetic master password, new TOTP and five new recovery codes. Actual
HTTPS checks rejected the old cookie/password/factors and authenticated with the new
password/TOTP, listed the native mailbox through the helper, read the original IMAP
message, and verified role-specific permissions and restored mailbox metadata.
The source guest disk was never attached to the target. Retained observations are in
`build/mail-admin-local-mock-115167ae-192e-4b01-b32b-2950c8a1ad45/replacement-recovery-proof/`.
This is development recovery evidence, not frozen T18 acceptance.

The initial restore flow failed because the mock bootstrap omitted `/usr/bin/docker`;
explicit reconciliation completed authentication preparation after that prerequisite
was corrected. Initial loopback Host/Origin headers were also incorrect and caused
the application to reject the fixture. Subsequent inventory requests measured 14.34
seconds through the non-root helper in this emulated guest; the original five-second
fixture HTTP deadline remained a failed attempt. A separate measured 30-second mock
profile passed the complete application check. Ordinary login rate limits also
blocked repeated probes; those limits were allowed to expire naturally, never reset.

The first controlled kernel reboot failed its unchanged 360-second observer. Its
Docker startup exceeded the mock's 120-second service deadline, forcing daemon
restarts and leaving the helper inactive. A real, privately named systemd regression
reproduces the helper dependency failure with the retained original unit and passes
after adding Docker to the helper's `WantedBy` targets. Its initial immediate marker
observation raced the asynchronous dependent start; the final comparison uses the
existing 15-second helper-readiness bound on both original and fixed units.
Logs: `build/systemd-dependency-recovery-bounded-baseline.log` and
`build/systemd-dependency-recovery-bounded-fixed.log`. Only the private mock selects
a measured 180-second Docker startup budget; application readiness, native budgets,
production configuration and the 360-second boot observer remain unchanged.
The separate corrected kernel run and post-boot assertions passed. The source VM's
last stopped timestamp predates target creation and remained unchanged; only the
new target's disk is mounted. Actual new password/TOTP login, non-root helper
inventory/outcome access, a completed-request retry without credential mutation,
the original message body and restrictive permissions passed after the new kernel
boot. Credentials match their pre-boot digest. No service was started by the final
observer. The observed backup age was **1,342.06 seconds (22 minutes 22 seconds)**
at the original outage. The complete outage through verified recovery/reboot was
**7,393.70 seconds (2 hours 3 minutes 14 seconds)**, including interrupted attempts
and explicit corrections. This meets the selected synthetic targets of 93,600 and
14,400 seconds respectively; production RPO/RTO and a clean frozen recovery run
remain unqualified. The archived host bundle was `4c55859a`; its helper unit required
the subsequent recorded correction, so this does not qualify that original bundle.
Reports: `build/fresh-recovery-post-correction-boot-verification.log`,
`build/fresh-recovery-selected-objectives.log`, and
`build/mail-admin-local-mock-115167ae-192e-4b01-b32b-2950c8a1ad45/replacement-recovery-proof-c8b631f4-6d51-410e-ada6-42cdc043a53d/`.
Current local checks pass Ruff, strict mypy, frontend checks, **355 unit and 44 API
tests** (`build/check-helper-dependency-startup-correction.log`), with separate plan
validation (`build/plan-helper-dependency-startup-correction.log`). These checks
precede the final boot-observer switch to a fresh post-boot password/TOTP login.

The subsequent actual idle-guest power-cut test passed in **344.64 seconds** after
the observer was corrected to use the existing 15-second HTTPS readiness check
before submitting credentials. The initial attempt raced TLS startup and failed;
both its log and result remain failed. The successful attempt observed kernel
`5fa04351-b5b5-4f4a-a58c-0fcfc18024aa`, preserved the original credential digest and
message body, authenticated through the real helper/HTTPS and retried the completed
synthetic operation without changing credentials. It starts no service during
observation and covers an idle host crash, not crashes at mailbox mutation boundaries.
Records: `build/fresh-recovery-idle-host-crash-readiness-corrected.log` and
`build/mail-admin-local-mock-115167ae-192e-4b01-b32b-2950c8a1ad45/idle-host-crash-ecb32f7b-c87a-4a31-90e5-f27f531ce67c/`.
The restored timer independently parses as `*-*-* 03:00:00 Asia/Dhaka` and is disabled
(`build/fresh-recovery-restored-backup-calendar.log`). No production schedule ran.

An additional explicitly approved local duplicate cleanup removed five raw image
archives only after checking their retained gzip copies, and consolidated 16
checksum-identical dashboard archives using hard links. Every frozen bundle path
remains available. Measured recovery was 1,914,134,528 bytes; both retained `512f66c5`
and `4c55859a` artifact bundles passed their integrity checks afterward. The exact
proposal and execution record are `build/post-recovery-duplicate-cleanup-proposal.json`
and `build/approved-duplicate-cleanup-c696e0d9-5169-424a-b6a3-890bb8c6cbb6/`.

Deployment observation also used a different launcher destination from the real
helper. Both observation adapters now reuse the helper's canonical launcher constant;
the before-fix path regression failed and the corrected 22 adapter/host-report
checks pass (`build/check-consistent-launcher-installation-path.log`).

Current development checks pass Ruff, strict mypy, frontend checks, **358 unit and
46 API/integration tests** (`build/check-independent-external-alert-delivery.log`).
The new network integration checks use actual trusted TLS and authenticated loopback
SMTP to deliver outage/recovery notifications, preserve queued failures until retry,
deduplicate unchanged conditions, and send alerts despite expired/wrong-host target
TLS while the dashboard database is absent. Both fixture CAs now include key
identifiers so a combined trust store distinguishes their different signing keys.
The initial ambiguous-issuer fixture failure is retained. No external email was sent.
These checks do not turn the remaining T22 host qualification into passing cases.

The earlier attempted frozen source was
`2d30399b98a7261e361d033cca0decbb082fe14f597cec3cce3b14434a3724b3`.
Its local `make check` passes Ruff, strict mypy, frontend lint/type checks, **317 unit
and 44 API tests** (`build/check-frozen-expansion-final.log`). The qualification image
builds after a local Colima interruption; the original failed connection attempts
remain in their logs. The engine resumed with its existing 2 CPU / 2 GiB profile.
The retained runtime archive and fresh qualification image have the same observed
application-file digest, `96b25a8467df5de037039ead30f4f8c3abfc2cc72e940a6f7f2b6c043791166c`.
Plan/example checks pass separately (`build/plan-frozen-expansion-final.log`).

Development checks before this freeze passed five real helper/Maddy protocol tests,
five Linux socket-boundary tests, five inventory/metadata tests, three provisioning
failure tests and four mutation-crash tests. The crash cases actually SIGKILL the
helper after durable intent and around each real CLI/progress boundary; they do not
simulate a VM power loss. The inventory module's first run failed because its own
observer indexed a one-argument `version` command incorrectly. That original failure
is retained; a corrected complete five-test run passed. The final frozen catalogue
will execute the newly added partial-reset assertion as well.
Logs: `build/maddy-helper-protocol-complete-targeted.log`,
`build/maddy-socket-bounds-targeted.log`,
`build/maddy-inventory-metadata-targeted.log`,
`build/maddy-inventory-metadata-corrected.log`,
`build/maddy-provisioning-failures-targeted.log`,
`build/maddy-mutation-crashes-targeted.log`.

Regression checks also fixed malformed host-observation timestamps, nested diagnostic
field disclosure and malformed root deletion-capability mappings. Deletion still
requires an exact root-owned boolean `true`; request flags cannot enable it.
The expanded frozen catalogue has 56 reviewed application case mappings; unmapped
cases remain `not_run`, and the previously observed T08 passes require a fresh,
artifact-matched host run. Before the expanded frozen run completed, the preceding
catalogue result remained 38/111.

The attempted `2d30399b` bundle could not be consumed: Docker's load/save roundtrip
omitted the optional OCI descriptor `platform` field. Its original export and
failure are preserved. The adapter now accepts its absence only while validating
the hashed configuration's exact Linux/amd64 architecture and required non-root
user; explicitly wrong/null platform fields and ambiguous manifests still fail.
The [OCI image-index contract](https://github.com/opencontainers/image-spec/blob/main/image-index.md)
defines that field as optional. Before-fix regression failure:
`build/reexported-image-regression-before-fix.log`.

The corrected frozen source is
`512f66c583faa3b58b3b11a029a3db73679a9d049c038853d751075a9f437cac`.
Its full local check passes **320 unit / 44 API tests**, Ruff, mypy and frontend
lint/type checks (`build/check-reexported-image-corrected-final.log`), and the
qualification image builds (`build/build-reexported-image-corrected-final.log`).
The actual browser harness passes against assets exported from the retained,
file-matched runtime image (`build/browser-expanded-frozen.log`). This is a local
synthetic browser check, separate from HTTPS and release qualification. The new
frozen bundle is `build/release-candidate-512f66c5/`, artifact set
`e724f4dd85db4ebf8f008cafddd799b68678956bde291415fa526554751ea587`.
Host preparation was first attempted before transfer completed and failed with a
missing runner file; it is not host evidence. Corrected preparation and a new guest
kernel reboot succeeded. All four T08 cases then passed, including Docker restart,
under the unchanged fixture limits. Actual report:
`build/release-candidate-512f66c5/host-qualification-9cba2b39-1caa-4037-9da7-c20d51d746c7/T08.json`;
log: `build/local-mock-expanded-host-qualification.log`. The installed files,
configuration and real restricted consumer image matched before and after.
Boot ID changed from `a02fda0c-1d42-4f70-b83a-e7323e35f470` to
`3ec494d1-b273-4d0c-a5ea-4ecea11e6ec9`. Its combined catalogue later passed 60/111,
as recorded at the top of this file.

The later complete recovery fixture exposed a stale Maddy update-pipe socket from
an earlier unclean shutdown. IMAP remained available, but inventory output included
the CLI's update-pipe failure. The first full capture refused before stopping any
service (`build/local-mock-full-state-capture-r2.log`). The original failing
container was retained and renamed; a new synthetic Maddy uses a root-only bounded
runtime tmpfs, and actual CLI inventory, login and existing mail were verified.
The real crash regression failed before this fix and passed afterward:
`build/maddy-runtime-pipe-before-fix.log`, `build/maddy-runtime-pipe-fixed.log`.
Update-pipe CLI errors now preserve their actual exit and require reconciliation.

All 18 roles were subsequently captured with both actual mail/dashboard writers
observed stopped before and after copying. Both services resumed successfully.
The off-VM upload then exceeded the unchanged 64 KiB command-output bound; this is
an interrupted, unverified upload, not a successful backup or restore
(`build/local-mock-full-state-capture-r2-after-runtime-repair.log`). The current
Restic runner suppresses progress chatter with its existing `--quiet` option while
retaining JSON summaries and output/deadline limits. Explicit repository identity,
survivor, unlock and full-read reconciliation is required before another capture.

Current frozen source `4c55859a7a9a14c5e7365f6c45fee29d295bec399591e1e3a73d46b9c7c95911`
passes **324 unit / 44 API tests**, Ruff, mypy and frontend checks
(`build/check-backup-progress-and-supervisor.log`); its qualification image builds
(`build/build-backup-progress-and-supervisor.log`). Bundle:
`build/release-candidate-4c55859a/`, artifact set
`0c4b77203276eb925522bdae41ebb0777b7cc7777639c060f94472e7c62b6479`.
Its full application/catalogue/replacement-host verification remains pending.

The current frozen source's four T08 cases subsequently passed after a new kernel
boot, with installed files, rendered units, fixture configuration and actual image
bindings unchanged across the run. Report:
`build/release-candidate-4c55859a/host-qualification-845e8a85-e699-4e59-a738-26df33f84e11/T08.json`;
log: `build/local-mock-host-qualification-4c55859a.log`. Boot ID changed from
`3ec494d1-b273-4d0c-a5ea-4ecea11e6ec9` to `52590cf5-0aad-4c93-81a6-f6ec6f6e96dd`.
The original source guest's disk remains retained. Full current development suites
and replacement-host qualification remain separate outstanding checks.

The same frozen source passes 324 unit and 44 API tests in emulated Linux/amd64
(`build/linux-unit-4c55859a.log`, `build/linux-integration-4c55859a.log`). The real
interrupted-upload reconciliation passed in 22.20 seconds with the original exit
kept unknown (`build/local-mock-full-upload-reconciliation.log`). A fresh 18-role
capture/transfer attempt is running; no upload/restore pass is claimed yet.

The quiet-output 18-role upload subsequently reached that small-file profile's
120-second command deadline. Its capture and service recovery passed; the upload
remains unverified (`build/local-mock-full-state-capture-r2-quiet-4c55859a.log`).
The separate complete-server fixture now uses a 600-second command budget with
unchanged memory/CPU limits, recorded in
`build/local-mock-full-state-budget-observation.log`. This is an isolated workload
measurement, not a selected production limit or a pass for the 120-second attempt.
The previous interrupted command must be reconciled before a new capture. Application,
mail CLI, HTTP and individual Maddy-module deadlines remain unchanged.

The separate larger fixture then **passed all 18-role capture/R2 verification and
restored-staging checks** in 460.42 seconds. Packed data added: 120,674,483 bytes.
Both real writers were observed stopped throughout copying, both resumed, and native
restore integrity/ownership checks passed with restored authentication closed.
Actual report:
`build/mail-admin-local-mock-8a8f1170-b11c-4adf-8626-537e42976ea3/full-recovery-proof-4c55859a/result.json`;
log: `build/local-mock-full-state-measured-budget-4c55859a.log`.
The matching runtime image's authentication-preparation utility also passed
(`build/local-mock-full-restored-auth-preparation.log`). Services were not started
from restored state. The recovery password/profile and narrow S3 credentials were
saved privately outside the source guest/R2; no secret value or broad Cloudflare
token was logged. This establishes complete capture and verified staging, with
manually provisioned fixture TLS and no acquired ACME account. New-host activation,
kernel boot and an operator-approved RTO remain outstanding.

The identical approved cache-only prune ran again, preserving every observed Docker
image/container/volume identity (`build/approved-build-cache-cleanup-20261023T103334Z/`).
No fixture evidence or production data was removed.

Native source `512f66c5` passed all 320 unit and 44 API tests plus eight complete
Maddy modules. The outer SSH observer timed out after 900 seconds while modules
continued; the original native result remains failed/incomplete, runtime was not
run, and no complete Maddy pass is claimed. Its live Maddy comparison passed:
`build/mail-admin-qualification-2b845194-a836-4bc5-973b-66da91017c99/result.json`.
The supervisor now covers the sum of the exact frozen module count's existing
900-second module bounds and retains captured output on timeout. Regression checks
cover these boundaries; no application or per-module deadline was relaxed.
An independent native frozen catalogue attempt stopped at the unchanged spare-memory
gate before running cases (`build/native-independent-catalogue-512f66c5.log`).
It does not add catalogue passes; local acceptance execution remains required.

| Package | Current state |
| --- | --- |
| W01 | Build, dependency locks, lint, type checks, test commands and runtime image delivered. |
| W02 | Explicit matched database/marker/key provisioning, storage errors, transactions and retention implemented. |
| W03 | Setup, password, TOTP, optional Email OTP, recovery codes, sessions, recent authentication and offline recovery implemented. |
| W04 | Restricted Unix helper, Linux peer identity, fixed CLI/stdin, journals and conservative outcomes implemented. |
| W05 | Protected services, bounded work, inventory, create/reset/delete guards, diagnostics and metadata reconciliation implemented. |
| W06 | Responsive React/MUI flows, relay revisions/confirmation and client settings implemented; browser checks pass. |
| W07 | TLS, maintenance, backup/recovery, matched cutover/rollback, systemd units and independent alert code delivered; full host qualification remains incomplete. |
| W08 | Real-Maddy, HTTPS-container and browser harnesses delivered; full case-level catalogue qualification and trusted release reports remain incomplete. |
| W09 | Blocked on W08, production inputs, security disposition, target acceptance and explicit live-deployment approval. |

Latest complete application-check snapshot:
`03641e337d350ed56f3d258eeef8c2721cd3e67ce56cbf49f3843b9c34f3def8`.
Build, Ruff, strict mypy, frontend lint/type checks, 283 unit and 40 integration
checks pass (`build/check-catalogue-expansion-final.log`,
`build/build-catalogue-expansion-final.log`, `build/build-runtime-03641e33.log`).
The same unit/integration counts pass in emulated Linux/amd64
(`build/linux-unit-03641e33.log`, `build/linux-integration-03641e33.log`).
Plan/example checks pass separately (`build/plan-catalogue-expansion-final.log`);
they establish no application behavior.

The working tree subsequently adds a local mock SSH transport fix. Its full local
check passed 286 unit and 40 integration checks, lint and type checks
(`build/check-local-mock-proxy-corrected.log`). This transport change remains
separate from the frozen catalogue results below; it does not create a new release pass.

The trusted frozen-bundle catalogue run passed **31 of 111** isolated cases and
recorded **80 `not_run`**, with no failed mapped case. The candidate remains blocked.
Bundle: `build/release-candidate-03641e33/`; reports:
`qualification-c923def2-318c-4736-a782-b94dfaa164ff/`; artifact set
`14a3cae9a2ab15c1853037f5a190d0806fa18d30acdda58f18d3ff08f8f72f42`.
The actual candidate checker rejects the incomplete evidence
(`build/release-check-03641e33.log`). The runner records the ARM64 engine's amd64
emulation. General test counts do not fill the remaining cases. Newly mapped
assertions cover factor replacement, session expiry/revocation, atomic password
invalidation and rollback, racing old verification, exact request size, cancelled
hashing, real CLI warnings/deletion failure and local SMTP semantics. See
[qualification tooling](qualification.md#reviewed-assertion-expansion).

Rollout records, checkpoint reports, baseline manifests and rollback sets now live
in a directory for each qualified artifact set. A shared operation lease and a
target-bound active-release record prevent a new release from bypassing an unknown
prior phase. Previous files/packages remain intact. A completed forward cutover
requires the original G08 acceptance checks before another release can be selected;
a completed rollback permits a new selection. The new regressions also exposed the
old retention reference to nonexistent G09. It now checks the contract's actual final
G08, preserving the baseline until that gate passes. The failed first regression run
is retained in `build/check-release-namespace.log`.
Missing or dangling active-release pointers also block a clean selection when prior
state exists; two additional regressions cover those loss/corruption boundaries.

The emulated Maddy run at frozen source `dda75d78` was killed during its last
restore test. The Docker daemon recorded an OOM for its 192 MiB controller:
`build/maddy-dda75d78-oom-events.jsonl`. Its original log remains
`build/maddy-dda75d78.log`; this is a failed run, not 22 passing checks. The current
runner collects completed fixture cycles before the next test and fsyncs unique
source/image-bound per-test progress records, so interrupted checks leave partial
evidence. Collection alone did not prevent the second OOM at source `2433cd1b`;
its partial progress, original log and `build/maddy-2433cd1b-oom-events.jsonl` remain.
A separate instrumented single-case diagnostic passed under the same cap and
identified repository initialization as its 193,441,792-byte peak. It is a diagnostic,
not a complete suite or catalogue pass. Records:
`build/mail-admin-memory-diagnostic-fe2f1677-bff3-47e4-b149-b1efcb199b01.jsonl`.
The current Restic process now shares a 96 MiB Go memory target with the local
repository fixture, leaving more coordinator headroom. Encryption/KDF settings,
integrity checks and hard container limits remain unchanged. Fresh module controllers
then eliminated the cumulative-memory failure without omitting any module. The
first such run at `0aa09615` passed 21 checks and exposed a stopped-container port
publication race in the negative restored-HTTPS assertion. That run remains failed.
The startup observer now handles absent/unpublished ports within the existing
15-second deadline; its regression checks cover stopped, delayed and missing ports.
The full emulated run at `f68e35a0` subsequently passed all 22 checks. Its native
run passed unit/API checks but stopped at the unchanged spare-memory gate before
Maddy and HTTPS. It records `insufficient_spare_capacity`, not a test pass:
`build/mail-admin-qualification-2dc6f7ea-6f9a-4206-aac3-564a6a3343cd/result.json`.
At the current frozen source, all 22 Maddy and nine HTTPS checks pass in emulated
Linux/amd64 (`build/maddy-03641e33.log`, `build/runtime-03641e33.log`). The Maddy
report preserves each fresh module's raw result and reports the maximum controller
peak, not whole-host memory. Its observed peak reached the 192 MiB limit; this does
not establish production headroom. The real R2 backup/restored-HTTPS fixture also
passed in 90.66 seconds:
`build/r2-application-80787e40-dd84-4f27-86ec-2138ac0c6b59/result.json`.
It verifies restored synthetic mail, relay decryption, invalidation of old
authentication, private-terminal credential recovery and fresh password/TOTP HTTPS
login. Full replacement-host roles, boot and RTO remain unqualified. The same frozen
source passed all 283 unit, 40 integration, 22 Maddy and nine HTTPS checks on
native amd64. All raw reports were collected and the live Maddy comparison passed:
`build/mail-admin-qualification-dbf2bf5d-15f7-42d0-ad14-76252ab12a7f/result.json`.
Controller peaks were 135,278,592 / 90,877,952 / 144,359,424 / 70,774,784 bytes.
These observations do not establish whole-host production resource headroom.

A larger local mock driver is prepared with a 1024 MiB amd64 guest and bounded
1280 MiB native ARM64 emulator container. Its negative isolation/admission checks
pass, and its ARM64 tool image has built. Explicitly approved unused-cache cleanup
and local free-block reclamation restored about 8 GiB of Mac disk space. Original
logs and preserved Docker-object identities are under
`build/approved-build-cache-cleanup-20261023T042302Z/`. Three identified local Maddy
fixtures left running by earlier OOM runs were stopped, with containers and files
retained (`build/orphaned-local-fixtures-stopped-20261023.json`). The new local guest
booted, completed cloud-init and started Docker 29.4.0/containerd successfully. Its
actual guest architecture, boot ID and instance ID were observed through authenticated
SSH. This establishes mock bootstrap, not application boot/reboot/crash qualification.
State and observations are under
`build/mail-admin-local-mock-8a8f1170-b11c-4adf-8626-537e42976ea3/`.
The Mac's forwarded SSH port stopped delivering banners while the same private
container socket still responded. The new adapter routes SSH bytes through the
exact validated local container, retaining its scoped key/trust and isolation checks.
Failed direct-forwarding probes and the successful authenticated readiness observation
remain intact. No production network, service, port mapping or host key was changed.

The current mock tooling also fixes a sudo-list schema incompatibility in the pinned
guest and includes the Compose plugin binary from the existing pinned Docker stage.
The guest's actual cloud-init schema validator accepted the generated replacement
configuration; the repaired guest reports Compose 5.1.1. Its originally incomplete
payload, failed probes and repair observations remain available. These fixture fixes
do not change the live server or qualify its different Compose installation.

Real R2 retention subsequently passed on this guest at frozen source `03641e33`:
ten controlled daily points became seven retained points, and all seven restored
with expected changed/removed content. The timed-out initial batch remains failed;
its interrupted command was explicitly reconciled with repository checks and its
original exit kept unknown. The separate successful continuation is
`restic-retention-after-reconciliation.json` in the mock directory above.

Manual host prototypes also exercised the real systemd helper and an actual
non-root, read-only-bound consumer. A helper restart preserved the directory inode,
replaced the listener socket, rejected an unauthorized peer and retained an actual
completed operation without rewriting credentials (`helper-restart-prototype.json`).
The seed operation ran through unchanged helper composition and real Maddy CLI
outside the socket; this does not claim the earlier timed-out socket mutation passed.
The original fixture observer timeouts remain in their logs. A controlled Docker
daemon restart inside the guest preserved the operation and credentials and returned
the helper to active state (`docker-restart-before-fix.json`); no unit fix was needed.
These are manual prototypes, not additional trusted catalogue passes. Full guest
kernel reboot also passed as a manual component prototype
(`guest-reboot-prototype.json`): the boot ID changed, restricted paths completed
before Docker/container startup, the non-root consumer reconnected, and the durable
outcome/credentials were preserved. This uses a standby consumer, not the full
dashboard. Crash, full dashboard startup and remaining case mappings remain pending.

The subsequent source `0f01bbb0e11dcdb9fd7f4f5d77310f5dadbccb9fb2de516e02e81740a6a04290`
adds actual host lifecycle tests and a separate standard-library guest runner.
`make check` passes 294 unit and 40 integration checks, Ruff, strict mypy and frontend
lint/type checks (`build/check-host-qualification-corrected.log`). The qualification
image builds (`build/build-host-qualification.log`), and plan checks pass separately
(`build/plan-host-qualification.log`). A frozen host run matches installed source,
rendered units, configuration and the real restricted consumer image before/after
the tests. T08.02/T08.03 pass; T08.01/T08.04 remain `not_run`. Its frozen artifact set is
`057441035b346b8066ff3fa9e967c2247ee3104a40e0161c42566c54da8662aa`; report:
`build/release-candidate-0f01bbb0/host-qualification-990f1edd-b4bf-45c1-859f-b40865872732/T08.json`.
The combined frozen catalogue run passes 33 of 111 cases and records 78 `not_run`:
`build/release-candidate-0f01bbb0/qualification-dc0254cb-95db-4c2e-b10c-c989974c3a71/`.
Its candidate checker correctly rejects incomplete qualification
(`build/release-check-0f01bbb0.log`). The host group remains blocked.
The same frozen source passes 294 unit and 40 integration checks under emulated
Linux/amd64 (`build/linux-unit-0f01bbb0.log`, `build/linux-integration-0f01bbb0.log`).
The local guest was shut down through its own systemd poweroff to release memory
during those runs; its retained disk and evidence were not deleted.
Negative collector regressions reject altered hashes, wrong artifacts/platform/image,
missing observations, stale/future/reversed time, invented cases, skipped assertions
and incomplete assertion sets. No manual prototype is imported as a passing case.

The later working tree adds full dashboard lifecycle/reboot assertions and exact
persistent-budget coverage. Its local checks pass 301 unit and 42 API tests, lint
and type checks (`build/check-host-reboot-order.log`). New real API assertions
cover shared authentication routes, ignored forwarded peer headers, exactly
15 peer/30 administrator attempts, restart persistence, reservation before hashing
and the exact 900-second expiry. Concurrent reservations and shared Email OTP/relay
delivery limits cover 3 peer/5 administrator sends without sending external mail.
These new mappings still need their own frozen catalogue execution.

The real non-root dashboard is now provisioned in the local mock through the matched
image. Its first start failed the unchanged fifteen-second observer at 15.31 seconds;
it subsequently served trusted fixture HTTPS and completed password-plus-factor
enrollment/login. Both facts remain separate (`web-first-start-diagnostics.log` and
`web-enrollment-observation.json` in the mock directory). A real HTTPS late-helper
component test passed: anonymous/password-only access stayed denied, authenticated
dependent operations returned unavailable, and retrying the known completed request
after helper recovery did not repeat the mutation or change credentials. Report:
`host-components-76c079da-df3b-4ff3-9941-1bca98f46bf8-result.json`. This is not yet a
frozen catalogue pass, and the first startup failure remains unresolved.

Source `973b49685d4a11d3a1be542025ae1665b31b1b6b16c2d90d045bc6dcc87af035`
has a new frozen bundle (`build/release-candidate-973b4968/`) and matched before-boot
observations. The exact local mock was abruptly power-cycled on 23 October at
07:42 UTC; its original container image and disk remain unchanged. A new kernel boot
ID was observed. The frozen host test ran the reboot case first so later
helper/Docker restarts could not mask a failed boot. Reboot, late-helper HTTPS and
helper restart assertions passed, but Docker restart failed: the standby consumer
was left stopped with a containerd `AlreadyExists` task error. Dashboard/Maddy
containers recovered. The final installed-consumer comparison then failed, so no
qualifying group report was emitted. The original failure is
`build/local-mock-host-dashboard-reboot-qualification.log`; engine/container observations
are preserved in the mock directory. This event only
tests completed-state recovery after power loss; it is not an in-flight mutation
crash or production reboot result. Original event and run logs remain under `build/`.

The following fixture fix gives its Python standby PID 1 an explicit SIGTERM handler;
the old sleep process did not handle termination, and the engine logged forced
shutdown and a stale task. This is the current suspected cause. The
old failed consumer is retained with autostart disabled; only a new restricted
standby consumer was created. Actual dashboard/Maddy code and the daemon's shutdown
deadlines were unchanged. The focused Docker-restart rerun passed in 121.17 seconds:
`build/local-mock-docker-restart-with-termination.log` and
`host-components-dfea0826-45d6-4259-9d1d-392ec5a36c62-result.json` in the mock directory.
This supports the termination-handler fix; the complete new frozen host run remains
separate. A subsequent real password/TOTP HTTPS login also passed without logging
fixture authentication material.
The host runner also now saves raw assertion results before the final installation
check, preserving failures without generating qualifying references. Both changes
have regression tests. `make check` passes 303 unit / 42 API checks plus lint/type
checks (`build/check-standby-termination-report-retention.log`).

The complete frozen host run at source
`e537d6037e23a677256bfc8d635b46a9323c3ba01f990a03f93b024c67ffa277`
then passed all four T08 cases, including a newly prepared controlled kernel reboot,
real dashboard authorization/retry, helper restart and guest Docker restart. Installed
files, root fixture configuration and restricted image bindings matched before/after.
Artifact set: `95c60ed5ca5186914caa46d5c33517eb7b71696e81cae8df82eeedd903ef772d`;
report: `build/release-candidate-e537d603/host-qualification-520a9d82-dd27-4599-be47-886a8789a48a/T08.json`.
Raw log: `build/local-mock-corrected-host-qualification.log`. This is software-emulated
isolated host qualification, not production reboot acceptance or whole-host headroom.
The combined catalogue run for that same bundle passes 38 of 111 cases and records
73 `not_run`, with no failed mapped case. Reports:
`build/release-candidate-e537d603/qualification-81b7e525-eec7-4e21-9e5c-8ddda9080469/`.
The candidate checker correctly rejects incomplete qualification
(`build/release-check-e537d603.log`). The earlier failed run and initial fifteen-second
dashboard-start failure remain preserved. Passing plan checks and general test counts
do not fill the remaining 73 cases.
The same frozen source passes 303 unit and 42 integration checks under emulated
Linux/amd64 (`build/linux-unit-e537d603.log`, `build/linux-integration-e537d603.log`).

The subsequent permissions/session development batch passes all five targeted
real-Maddy tests (`build/maddy-permissions-sessions-provisioned.log`), covering
host service UID/nobody denial for actual mail/databases, raw-exec versus launcher
umask, daemon/CLI umask, delivery/IMAP writes/WAL recreation, and retained IMAP
access followed by explicit restart without message deletion. Its first run passed
four tests and failed the whole-tree permission assertion on Docker's empty bind
placeholder. The fixture now provisions those exact targets privately before Docker
can create them; the assertion was retained. The first failure remains in
`build/maddy-permissions-sessions-targeted.log`. Targeted reports use distinct
`build/maddy-partial-UUID/` directories and cannot claim a complete suite pass.
Local checks pass 305 unit / 42 API tests plus lint/type checks
(`build/check-permissions-sessions-mappings.log`). Six new T09/T13 mappings remain
separate from the frozen 38-case result until their future frozen execution.

Frozen source `dda75d78e26b3ab6d771d8aaa9cccf0c69bb29612927821726e5af740a3b0720`
passed all 266 unit, 33 integration, 22 real-Maddy and nine HTTPS checks natively.
The live Maddy comparison passed. Controller peaks for unit/integration/Maddy/HTTPS
were 135,626,752 / 107,479,040 / 192,561,152 / 72,491,008 bytes. This native result
does not replace that source's failed emulated run. Reports:
`build/mail-admin-qualification-6638d19b-3508-47ba-b32c-582881f892bf/result.json`;
preserved summary: `build/source-dda75d78e26b/verification-summary.json`.

Earlier fully native-checked frozen source:
`5f83f7ec0e9d8ae08819d0af6cc32f690edc087fec6cb97234cddb0799c05317`.

- Native amd64 run `c6607c94-6438-44d7-879d-a47a33670ae8` passed 249 unit,
  33 integration, 22 real-Maddy and nine HTTPS checks. Reports were collected;
  the live Maddy identity/configuration/mount/start-time comparison passed.
  Controller peaks were 98,947,072 / 90,693,632 / 199,004,160 / 76,165,120 bytes.
  These measure bounded controllers, not the whole host. Result:
  `build/mail-admin-qualification-c6607c94-6438-44d7-879d-a47a33670ae8/result.json`.
- Real R2 restored-HTTPS recovery passed in 107.46 seconds:
  `build/r2-application-781af9a6-2543-4dbb-85f9-396dbf9f3a02/result.json`.
  Private-terminal credential recovery, fresh password/TOTP HTTPS login, restored
  synthetic mail/relay decryption and old authentication invalidation passed.
  Missing full-host roles remain explicit; this does not establish host boot or RTO.
- Frozen bundle `build/release-candidate-5f83f7ec/`, artifact set
  `eb120a833034f37991bc1b319527a1144d718c0e7a85015575977a13ce6e9205`,
  executed 16 of 111 catalogue cases successfully and recorded 95 as `not_run`.
  The candidate stays blocked. Reports:
  `qualification-fbd7b8ec-ad67-4500-978c-81dc15c82386/` inside the bundle.
  The runner records ARM64-engine emulation. Unit/API counts never substitute for
  the missing catalogue cases. Copies/hashes of this snapshot's local/native/provider
  results are retained in `build/source-5f83f7ec0e9d/verification-summary.json`.
- Email OTP resend now resamples a random value matching the superseded code,
  with bounded failure and no send/database replacement after persistent collision.
  Real authentication assertions cover route protection, purpose/session/expiry
  binding, concurrent factor consumption, recovery regeneration and exact password
  whitespace/Unicode preservation. Those checks underpin the additional mapped cases.
- Legacy manual-renewal observations persist unknown completion instead of borrowing
  an old managed-service success. A separately approved reconciliation checks the
  current pair and served TLS without inventing the original exit status. Both real
  process fixtures now wait for readiness and block on input instead of racing exec
  or a 30-second expiry. Earlier native run
  `52b3b64b-eff3-4695-bd0f-577e366584b8` at source `049347d9` remains failed
  (21/22 Maddy checks); its new legacy fixture lacked that readiness handshake.
  The separate targeted two-process pass is preserved in
  `build/native-process-46c61ae7-74be-4f48-8337-20f93d6dbd04/result.json` with
  its hybrid-image scope; the full frozen native pass above supplies the later result.

Earlier provider-tested frozen source:
`f6faf7234299762437c43f8c38c5c501b7ea09179a230ad7ffcc8778d96fd249`.

- Build, Ruff, strict mypy (85 source files), frontend lint/type checks,
  236 unit and 29 integration checks pass. Emulated Linux/amd64 repeats all
  236 unit and 29 integration checks successfully. All nine actual HTTPS runtime
  checks pass. Logs: `build/build-qualification-final.log`,
  `build/check-qualification-final.log`, `build/linux-unit-f6faf723.log`,
  `build/linux-integration-f6faf723.log`, `build/runtime-f6faf723.log`.
- Independent staged recovery now has a root `recover` command using the recorded
  immutable image, private terminal and restored mounts. It keeps runtime activation
  closed and reconciles a retained utility after lost completion without rerunning it.
  Interactive bootstrap/recovery uses Docker logging `none` so terminal factor
  material is not retained in Docker logs.
- Real R2 recovery passed at this source in 106.47 seconds:
  `build/r2-application-0f243157-b904-401c-ace7-77887a5d6b96/result.json`.
  It restores actual synthetic Maddy mail and enrolled dashboard state, decrypts relay
  settings, invalidates old cookies/challenges/codes/readiness, performs the real
  private-terminal recovery, and starts non-root HTTPS from the restored keys,
  marker, data and TLS files. Fresh password/TOTP login passes. The fixture reports
  its missing full-host roles; it does not prove replacement-host boot or an approved RTO.
- Frozen bundle: `build/release-candidate-f6faf723/`, artifact set
  `d83a225f267869a0cdf4d1fd5931d8b62cd3f66568d6076b4aa99588c8207dc9`.
  The new trusted catalogue adapter executed nine mapped cases successfully and
  recorded the other 102 as `not_run`. The complete T02 report passes the original
  report validator. The candidate checker correctly stays blocked. Reports:
  `qualification-a6701301-739c-4bd6-9022-ec2a9938583a/` within that bundle.
  These reports identify ARM64-engine emulation explicitly. This is incomplete W08.
- Artifact/source, SSH-target and installed-deployment adapters now have regressions
  for changed artifacts/source sets, another target/key, running-image replacement,
  helper/unit content/mode/symlink changes, extra host modules, type-sensitive
  configuration drift, renewed TLS generations, mixed keys and stale mounts. Their
  development checks do not qualify unimplemented host/CLI cases. A real read-only
  target observation is recorded in `build/target-observation-development.json`;
  it is not G02. See [qualification tooling](qualification.md).
- The earlier `94ab34ed` runtime run failed because its new unknown-route assertion
  omitted the API's safe request ID. The corrected assertion verifies its exact key
  set, error and UUID. `build/runtime-94ab34ed-failed.json` and the original log
  preserve the failure. The subsequent R2 run at `5ec923d6` failed because the private
  recovery umask removed fixture socket group traversal. Explicit fixture modes and
  a regression resolve it; the failed R2 report remains under
  `build/r2-application-422a1664-0883-483c-8596-5115f27bcea1/`.

The emulated Maddy suite passed all 21 tests at this source, with a controller peak
of 195,702,784 bytes under 192 MiB. The real R2 retention probe also passed:
`build/restic-probe-61a232a2-b9df-4f59-8f6f-f46817953d01/result.json`.
Ten controlled daily points expired three and retained seven; all seven restored
after pruning. First/unchanged-second additions were 8,394,489 / 5,117 bytes.
The six local application reports plus provider result hashes are preserved in
`build/source-f6faf7234299/verification-summary.json`.

Native run `adbde9e8-0032-46cd-a85c-31ac7e008956` passed 236 unit, 29 integration
and all nine HTTPS checks, including restored application recovery within the Maddy
suite. That suite passed 20 of 21 tests and remains failed: the timed survivor
fixture was not detected when it was expected. Its original report/log are retained.
The fixture now waits for explicit readiness and blocks on input instead of expiring
after 30 seconds; assertions verify it remains alive before and after reconciliation.
That change requires a new native result. Native controller peaks were 111,558,656 /
92,581,888 / 181,309,440 / 78,049,280 bytes. The live Maddy comparison passed.
Full host qualification remains incomplete.

Earlier fully checked backup source:
`00fbc5e55690303c049a71b7d6a9a515abb3d19eb515bccb2d3682c40a15635e`.
Source archive/checksum: `build/backup-source-00fbc5e55690/`.

- Ruff, strict mypy (85 source files), frontend lint/type checks, 215 unit and
  26 API/TLS/SMTP integration checks pass on macOS. Linux/amd64 under ARM64-host
  emulation also passes 215 unit and 26 integration checks. Logs:
  `build/check-restic-durable.log`, `build/linux-unit-restic-durable.log`,
  `build/linux-integration-restic-durable.log`. These are application checks,
  not the 111 catalogue cases or a release pass.
- Restic 0.19.1 is approved, pinned by executable/download checksums and included
  in the host release bundle and qualification image, outside the web runtime.
  The selected R2 backup path now uses encrypted native-file deduplication,
  exact snapshot identities and actual-clock seven-day retention. The selected
  calendar is daily 03:00 Asia/Dhaka. No live repository or timer is installed.
- Root recovery independently lists/restores a selected point into new private
  staging, verifies content/owners/modes and SQLite integrity, reports missing
  full-state roles, and closes restored runtime access. The matching isolated
  application image invalidates restored authentication/readiness before fresh
  SSH recovery. A persistent container identity and stable lease prevent replay
  after lost completion; only an observed real exit zero permits preparation.
  Interrupted Restic commands likewise block until explicit process/repository
  reconciliation, keeping an unknown exit unknown.
- Real R2 plus actual synthetic application recovery passed at this frozen source:
  `build/r2-application-d0c98375-fa33-4e1f-8094-dceb627e8e3b/result.json`.
  A real restored Maddy container read its message, matching dashboard keys
  decrypted relay settings, restored sessions/challenges/recovery codes were
  invalidated, fresh password/TOTP login passed, and a deliberately lost utility
  completion was reconciled without executing it twice. This is a partial
  application-state fixture; it explicitly reports missing full-host roles.
  Dashboard recovery is asserted through the matched root image and application
  domain code; starting HTTPS from those restored files is not part of that fixture.
  It does not prove full host boot or replacement-server acceptance.
- Linux/amd64 under local ARM64-host emulation passes all 21 real-Maddy and
  7 actual non-root HTTPS checks at this source. The controller peaks were
  194,957,312 bytes under 192 MiB for Maddy and 95,444,992 under 128 MiB for HTTPS.
  These are controller measurements, not the complete host peak. All six current
  macOS/Linux reports are preserved in `build/backup-source-00fbc5e55690/`.
- The repeated real R2 retention probe passed at this exact source:
  `build/restic-probe-f1c95716-7409-41b9-9d3b-3febd3ec2461/result.json`.
  Ten controlled daily snapshots expired three and retained seven; all seven
  restored after pruning, including the original shared data. First/unchanged-second
  additions were 8,394,728 / 5,179 bytes. Fixture files only, not a full host restore.
- Native amd64 run `78f3675c-b825-402b-adb7-7d5a311eb47d` passed all 215 unit,
  26 integration, 21 real-Maddy and 7 HTTPS checks at this exact source. Reports
  were collected and the live Maddy identity/configuration/mount/start-time
  comparison passed. Native controller peaks were 100,700,160 / 81,563,648 /
  177,991,680 / 67,481,600 bytes respectively. These are bounded fixtures,
  not a whole-host production measurement or catalogue qualification. Result:
  `build/mail-admin-qualification-78f3675c-b825-402b-adb7-7d5a311eb47d/result.json`.
- `build/release-candidate-backup-00fbc5e5/` contains the frozen review artifacts,
  including the pinned Linux Restic executable and independent recovery CLI in
  the host bundle. Its checker correctly rejects qualification because T01 and
  subsequent trusted catalogue reports remain absent (`build/check-backup-candidate.log`).
  No application test count or passing plan check substitutes for those reports.
- The earlier real R2 retention probe at source
  `cd96b8a0537e3960244128fce9cf552da1afcade5b7e01ccc823025f69c73a4a`
  passed ten controlled daily snapshots, expired three, retained seven and
  restored every retained point after pruning. First/unchanged-second additions
  were 8,394,487 / 5,110 bytes. Report:
  `build/restic-probe-b67f6a7b-ca6f-4977-b21f-4d0592996ff3/result.json`.
  The failed first probe (unsupported RFC3339 CLI time) remains preserved;
  the corrected UTC CLI format has a regression test.
- The immediately preceding source
  `5b95e3581d21f808a6fb303412617e5ac84fdaa9a61661e89438199b5e637201`
  passed 213 unit, 26 integration, 21 emulated real-Maddy and 7 HTTPS checks.
  Its first real R2 application restore passed as well. Reports/source archive
  remain in `build/backup-source-5b95e3581d21/`. These older results do not
  qualify the later interruption/cache/prune changes.
- Both read-only bucket lifecycle API inspections returned 403. The operator
  subsequently reported the default incomplete-multipart abort rule after seven
  days, with no completed-object deletion shown. That reported rule is compatible;
  bucket-level privacy/lifecycle was not independently verified. Restic must
  manage expiration of completed repository objects. See [backup design](backup-design.md).

Earlier completed container/browser snapshot, source
`a9b4d36ff37eb9a12819de9e880ac3becac3d5fe498bc63925c2dfcbb3e1af8d`:

- Build tooling, Ruff, strict mypy, frontend lint/type checks: pass.
- 177 unit and 18 API/TLS/SMTP integration tests pass. The test count is unrelated
  to the 111 isolated catalogue cases.
- New matched-package/cutover checks cover interrupted replacements, corrupt files
  and image archives, incompatible schema, concurrent commands, served-verification
  failure and partially enabled scheduling. Additional checks cover a later dashboard
  during Maddy-only rollback, recovery/registration after legacy rollback, and
  independent expiry monitoring using real loopback TLS.
- Linux/amd64 under local ARM64-host emulation: 19 real-Maddy and 7 HTTPS runtime
  tests pass. The new real package test exports/loads the pinned Maddy image, restores
  the original host configuration, restarts the isolated service and reads mail
  delivered both before and after the package was captured. It does not prove the
  complete systemd/TLS cutover or host reboot.
- Controller peaks were 166,248,448 bytes under 192 MiB for Maddy/package tests and
  95,019,008 bytes under 128 MiB for HTTPS. These are fixture peaks, not whole-host
  production measurements.
- The browser rerun passed against the compiled candidate assets. Its first attempt
  failed before reaching the application because Chrome's debugging port was absent;
  `build/browser-a9b4d36f-chrome-start-failed.log` preserves that failure. The later
  pass does not erase it.
- `build/release-candidate-a9b4d36f/` contains the frozen review artifacts. The candidate
  checker correctly remains blocked on missing T01 and subsequent catalogue reports.
  Its native run completed as recorded below. Neither it nor prior native passes
  qualify the changed backup implementation or missing catalogue cases.

Earlier completed container/browser snapshot, source
`a87a1d749b57646321973870f8ffb5a3825cb0d40562a4fbf8fc108fa53e63ee`:

- Ruff, strict mypy, frontend lint/type checks: pass.
- macOS/Python 3.12: 146 unit and 16 API/SMTP integration tests pass. This unit-test
  count is unrelated to the 111 isolated acceptance-case IDs.
- Linux/amd64 containers under local ARM64-host emulation: 7 HTTPS runtime tests pass.
  These include the real HTTPS API → Unix peer-authenticated helper → real Maddy
  creation/reset/delivery flow, exact password spaces, duplicate protection and
  retained IMAP-session behavior. These tests do not establish the whole
  production-host peak. The controller's measured peak was 96,833,536 bytes under
  128 MiB; the web-only peak was 80,564,224 bytes.
- Real Maddy fixture: 18 checks passed at this source snapshot,
  including encrypted restore and recovery of missing bodies/references while
  preserving newer mail. Do not carry an old source pass into qualification of
  changed artifacts.
- Offline recovery now closes a root-owned runtime policy before stopping the web
  container, retains a named utility for actual exit observation, and requires an
  explicit verified resume. The HTTPS suite verifies that the atomic policy update
  remains visible across container restart, root can probe during maintenance, and
  the service UID cannot invoke root operator commands. This is not host reboot evidence.
- Real synthetic ACME writers cover lost Docker attachment, surviving child exclusion,
  lost container-ID persistence and actual nonzero exit. They use no ACME service and
  do not establish HTTP-01 or certificate issuance.
- Additional real-CLI faults cover zero-exit password SQL failure, special-folder
  warning text, and a deferred SQL COMMIT failure after body removal. Actual exit
  code zero remains recorded alongside a separate reported-error flag; failed
  changes cannot be reported as complete. Recovery preserves newer mail.
- Loopback SMTP uses real STARTTLS/implicit TLS and certificate validation. Rejected
  authentication/sender/DATA, wrong-host/expired/untrusted TLS, unavailable relay,
  exact receipt IDs and independent alert retry are asserted. No external email is sent.
- Browser: login, responsive 360/768/1280 layouts, create/search/reset with actual
  reauthentication, text rendering, all five client tabs, relay save/connection
  test, optional-credit state, dark mode, logout and no browser storage: pass.
  A deliberately delayed relay-settings request verifies editing stays disabled
  until initial loading completes, preventing the response from erasing early input.
- Runtime dependency consistency: pass.
- Plan/model/example checks: pass, including 49 verifier tests. These verify the
  specification/tooling only and do not establish application behavior.

Reports and logs are under `build/`. Per-test reports include actual outcomes,
source/image/runtime/architecture observations and controller memory where available.
Skips, expected failures and source changes cannot become a passing application run.
The unchanged original pending release template still has no runtime qualification.

## Remote observations and mock host

The authorized connection is `deployer@35.237.68.175`, using the existing SSH trust.
Native fixture runs use unique container names, private Linux volumes, loopback
ports and bounded memory/CPU. They never mount production mail. Live Maddy identity,
configuration, mounts and start-time digests are compared before and after runs.
Historical failed reports remain under their original build directories; no failed
report is promoted to a pass.

The native run `30fa860e-d439-4d49-a471-c24762a5d615` used the earlier source
`e77f44a7462ec60d78ef1b0371e07982af313341139e1f1301bb5e23483162fd`
and passed 101 unit, 8 API and 12 real-Maddy tests. Five of six HTTPS runtime
tests passed, including the real-Maddy end-to-end flow. One test failed during
setup because the runtime did not become healthy within the unchanged 15-second
startup deadline. The container remained running, with no reported OOM and no
startup log. This is an unresolved failure, not a passing native runtime suite.
Controller cgroup peaks were 96,423,936 / 67,391,488 / 74,665,984 / 65,781,760
bytes for unit/API/Maddy/runtime respectively. The separately measured web-only
peak was 60,416,000 bytes. The live Maddy comparison passed.

Six subsequent instrumented native starts became healthy in 3.2–5.9 seconds,
without an OOM event. This investigation did not reproduce or resolve the earlier
failure and does not replace its report or constitute catalogue qualification.

The later native run `a122f4d8-82b6-4bb1-9919-d8946dcb0183`, source
`e26e42fb41b607ac8f7fbe6c0d5bf7b15732c8f3fa4861da0268b6801c16f503`,
passed 101 unit, 16 API/SMTP and 12 real-Maddy checks. Four of six runtime checks
passed; two initial starts exceeded the same 15-second health deadline. Captured
CPU counters showed approximately 8 seconds of CPU and 12–13 seconds throttled,
with no reported OOM. Its live Maddy comparison passed. Both failed runtime reports
remain failures. The current image precompiles Python bytecode with source-hash
validation.

The earlier native run `d5c876b2-75e8-4527-bbcb-c3e8e5323ea4` uses source
`42387005f184d50b2d8c3d2c592d74762f9967a2233559e6211d09be62a38c78`
and passed all four application suites: 111 unit, 16 API/SMTP, 15 real-Maddy and
6 HTTPS runtime checks. The original 15-second startup deadline and resource
limits were unchanged. Reports were collected and the live Maddy comparison passed.
Native controller peaks were 94,646,272 / 74,768,384 / 75,632,640 / 41,873,408
bytes respectively; the separately measured web-only peak was 55,091,200 bytes.
These are constrained fixture measurements, not the full production-host peak.
The archived review bundle is `build/release-candidate-42387005/`; its candidate
checker remains blocked because trusted catalogue reports are not yet complete.

The earlier native run `a6750054-0b02-4eff-bf24-f302ea5a8ed9` used source
`56f0c0a18b14e6adbeebd02097c7b1392b13b5f83ed36f89e5310e68ddf58de0`.
Its 125 unit, 16 API/SMTP, 18 real-Maddy and 6 HTTPS tests passed; reports were
collected and the live Maddy comparison passed. Controller peaks were 107,487,232 /
91,009,024 / 87,269,376 / 61,722,624 bytes; the web-only peak was 56,025,088 bytes.
Its bundle `build/release-candidate-56f0c0a1/` remains blocked on trusted catalogue
reports. This earlier native pass does not qualify the newer recovery implementation.

The intermediate local recovery snapshot `8d441120` failed one of seven HTTPS tests:
the root probe lacked the capability needed to maintain service-owned file modes.
The failed report/log remain `build/runtime-8d441120-failed.json` and `.log`. Adding
`FOWNER` to the isolated root authentication utility resolved that reproduced failure;
the web container still drops all capabilities. All seven tests passed at the
`a9b4d36f` snapshot locally and in its later native run below.

The earlier completed native run `6c1b5547-fefd-4114-8f3c-986cb69a42fd` used source
`a9b4d36ff37eb9a12819de9e880ac3becac3d5fe498bc63925c2dfcbb3e1af8d`.
All 177 unit, 18 API/TLS/SMTP, 19 Maddy and 7 HTTPS runtime checks passed; reports
were collected and live Maddy identity/configuration/start-time comparison passed.
Controller peaks were 110,190,592 / 81,481,728 / 110,825,472 / 48,590,848 bytes;
the web-only peak was 56,627,200 bytes. These are bounded synthetic fixtures,
not the complete host peak or production acceptance. Subsequent source changes
require their own applicable checks; the full catalogue remains incomplete.

The latest completed native run `78f3675c-b825-402b-adb7-7d5a311eb47d` uses
source `00fbc5e55690303c049a71b7d6a9a515abb3d19eb515bccb2d3682c40a15635e`.
All 215 unit, 26 integration, 21 real-Maddy and 7 HTTPS runtime checks passed,
including actual Restic mail/dashboard-state restoration, real Linux survivor
exclusion and lost authentication-preparation completion without replay. Reports
were collected, and the live Maddy container identity, image, configuration,
mounts and original start time remained unchanged. Controller peaks and precise
limits are recorded above and in the original reports. The independent real R2
probes at this same source passed as recorded above. Full restored-host boot and
restored HTTPS startup remain unqualified; no fixture pass fills a catalogue gap.

The remote collector now fails closed for a timed-out/unlaunched suite or missing
report transfer. These error paths have regression tests; an unchanged production
container alone cannot turn an incomplete fixture run into a pass.

A separately booted amd64 software VM has been prepared for reboot/crash/systemd
cases. Its first two boots timed out waiting for guest devices and entered emergency
mode; neither is **qualifying host evidence**. Only that mock VM was stopped. Its
own disk and logs remain available for investigation. The real mail host was not
rebooted, and its Docker daemon was not restarted. Follow-up VM tests remain pending.
The third boot reached later services, then reported an aborted guest filesystem
journal. It was stopped and preserved as well. A fresh pinned base now undergoes
offline filesystem checks before/after guest-only edits, with a separate guest
swap device and a 384 MiB container ceiling. The additional 128 MiB host reserve
is still required before startup. This setup is not host qualification or a
production resource recommendation.
The fourth boot did not reach SSH readiness; its disk/logs were preserved. Guest
journal observations showed missing SSH host keys and cloud-init not running. The
fifth boot used a read-only virtio seed instead of a SATA CD-ROM. Cloud-init ran,
and SSH eventually returned `x86_64` and the guest Docker journal. Docker repeatedly
failed while waiting for its internally started containerd; setup never became ready.
Only the mock was stopped, with its console/journal preserved. A copied disk now
prestarts fixture containerd separately; the original fifth-boot disk is preserved.
The repair SSH caller timed out after 600 seconds while the isolated process survived.
Subsequent observations confirmed its complete durable record, removed repair intent,
no remaining fixture process and unchanged live Maddy. Both the caller failure and
later completion observation are retained. The first repaired sixth-boot attempt
failed capacity admission before the VM started.
A follow-up read-only observation found 12,618,182,656 bytes free, below the unchanged
12 GiB spare-disk requirement; its log is `build/mock-sixth-admission-observation.log`.
The failed admission log is `build/mock-sixth-boot.log`. No capacity rule was reduced.
After explicit approval and checksum revalidation, 14 duplicate local raw archives
and 11 duplicate remote compressed fixture archives were removed, freeing
2,261,454,848 and 1,695,069,408 bytes respectively. Every removed file had a verified
retained copy; reports, credentials, mock disks and production mail were excluded.
The exact result is `build/duplicate-fixture-archive-cleanup-result.json`.
The sixth boot then passed the unchanged capacity admission and reached cloud-init
completion. Guest SSH commands repeatedly timed out, including a diagnostic with
multiplexing disabled. Application/systemd readiness was not established. Only the
mock container was power-cut afterward; its disk and console remain preserved.
The last observation and stop logs are `build/mock-sixth-final-console-observation.log`
and `build/mock-sixth-stop-after-bootstrap-failure.log`; the live Maddy comparison
passed. All application boot/crash assertions remain unqualified. No failed boot,
admission or SSH attempt is counted as a pass.

## Remaining release blockers

Every required missing, failed, skipped or stale catalogue case blocks G01. Outstanding
work includes real boot/legacy-cutover/rollback and fault-boundary qualification,
full restoration/retention/independent-network notification cases, total constrained
resource measurements, and immutable case-level runner reports for all 111 isolated
cases. The 16 production cases require their applicable approved target phases.

The operator selected `rsalehin24@gmail.com` for email alerts and supplied an existing
R2 bucket/access credentials privately. Real R2 deduplication/retention and synthetic
application recovery passed at their recorded source snapshots; full replacement-host
boot/activation qualification remains incomplete. The
selected backup schedule is 03:00 Asia/Dhaka with rolling seven-day retention.
The alert provider/credentials are still pending. Other pending inputs include
local staging budgets, maintenance/reboot window,
recipient dependencies, measured production budgets and security-release disposition.
The operator subsequently selected a 26-hour maximum verified-backup age and a
four-hour recovery target, recorded in `verification/operator-inputs.json`.
See [operator handoff](operator-handoff.md) and [Maddy assessment](maddy-security-assessment.md).
Email OTP and permanent deletion remain disabled in pending production configuration.

No commit or push has been made. No production deployment approval is requested yet.
