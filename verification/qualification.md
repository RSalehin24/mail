# Qualification tooling

`tools/run_tests.py` records development application checks. The distinct
`tools/run_qualification.py` executes reviewed mappings in
`tools/qualification_cases.py` against a frozen release bundle on isolated
Linux/amd64. Every other catalogue case remains `not_run`; a missing assertion,
skip, expected failure, test-loading error or changed source cannot pass a case.
All 111 cases now have reviewed local/host assertion mappings. G01 remains blocked
until their actual matching execution and the candidate checker pass. A mapping is
not an executed acceptance result. See the [111-case work list](qualification-worklist.md).

The isolated Maddy postmaster patch and pinned build inputs were approved. Its
fixture image is restricted to the exact OCI/configuration identities in
`tools/maddy_fixture.py`; production selection is separate. Source hashes, patch,
parser regression and provenance are described in
[the fixture notes](../tools/maddy-fixture/README.md).

`tools/run_https_browser.py --evidence BUNDLE/evidence.json` exercises the actual
HTTPS runtime with an isolated Chrome profile and a specific fixture-leaf SPKI pin.
The source-frozen `tools/browser-fixture.json` binds the existing Node, Chrome
launcher and Chrome framework by path, hash and version before and after execution.
The catalogue rejects development, changed-runner, stale or differently bound
browser reports. The private material remains in the bounded fixture directory.

Resume interrupted local catalogue execution with:

```sh
.venv/bin/python tools/run_linux.py --suite qualification \
  --evidence BUNDLE/evidence.json --resume BUNDLE/qualification-UUID
```

The runner reuses only successful, complete, fresh modules whose source, artifact,
image, engine, inventory and controller limits still match. Failed/interrupted
module attempts and partial assembly files are retained. Live controllers block
resume; completed catalogue results remain immutable. Normal assertion failures
remain failures while independent modules continue.

Guest kernel scenarios use `python -m tests.kernel_scenario_worker prepare DIRECTORY
--instance UUID --evidence EVIDENCE` in the bounded fixture controller, followed by
an externally observed power cut of the exact `LocalMock`, then `verify` with the
same bindings. `--only activation` and `--only recovery` select independent batches;
only their recorded selected cases can qualify. The full TLS batch includes every
staging/publication boundary. Host T05/T15 assertions consume the resulting frozen
observation through `MAIL_ADMIN_KERNEL_OBSERVATION`.

The private systemd cutover fixtures exercise the production `Cutover`,
`RootCutoverHost`, `ReleaseFiles` and `RollbackPackage` with only unit names, paths
and the isolated renewal entry point adapted. T16 also requires a real partial
installation across a kernel boot (`tests/cutover_reboot_support.py`), supplied by
`MAIL_ADMIN_CUTOVER_REBOOT`. A disabled timer or a configuration file alone cannot
complete these runtime assertions.

Full replacement uses `tests/recovery_capture_support.py`, the existing
`tests/recovery_target_support.py` phases and
`tools/record_replacement_isolation.py`. A verified frozen R2 capture must precede
source shutdown. Source/replacement controllers are inspected independently; the
source must stay stopped and its disk must never attach to the target. Recovery
phase records are immutable. Host T18 consumes `MAIL_ADMIN_REPLACEMENT_CONTEXT` and
requires all 18 roles, real ACME lineage/account state, matching before/after-cutoff
mail markers, fresh private password/TOTP, actual HTTPS/helper/mail checks, a new
kernel boot and the selected RPO/RTO. Earlier prototype observations cannot fill
these bindings. Alert-provider credentials and external monitoring are separate
release inputs and are not dependencies of source capture.

The operations-development runner now isolates catalogue assertions by test
module, preserving the original 192 MiB controller bound. The previous frozen
group runner accumulated unrelated T20 imports/state before invoking Restic;
its child was OOM-killed and T20.05 failed. That interrupted run is preserved in
`build/qualification-8a5ad743-resumed.log`. The actual restoration assertion passes
in a fresh development controller (`build/fresh-restic-qualification-controller-actual.log`).
Assembly requires each frozen module's exact assertions, artifact/image bindings
and observed controller exit without OOM; shared assertions execute once and keep
their original execution start time. Unmapped cases remain `not_run`, and a missing
module/controller observation prevents assembly. This runner change still needs a
complete final frozen catalogue run; its focused resource regression is not G01.

Host assertions can also supply part of a case: T22.02 requires both the database
history/container-log assertions and the independent guest-journal assertion.
`tools/run_host_qualification.py EVIDENCE --group T22` executes only its registered
host portion and leaves the other group cases `not_run`. The report remains
blocked until complete catalogue assembly. The merger verifies the frozen source,
installed files, configuration, image and exact host assertions before combining
them with local outcomes. A missing host report cannot qualify local-only evidence
or hide a local failure. The default host group remains T08.

The runtime development suite now reuses the Maddy module-isolation runner, with
its original 128 MiB controller limit. A combined expanded runtime run was killed
at that limit; kernel and Docker OOM evidence is retained. The fresh-module rerun
passed all 17 checks across six modules with an observed maximum of 117,555,200
bytes (`build/next-runtime-isolated-modules.log`). Aggregation requires the complete
validated inventory, matching Linux/source/image bindings, unchanged per-suite
limits, distinct test identifiers and every original module report with exit zero.
Partial, killed, skipped or missing outcomes cannot complete the suite. This result
is bound to the development copy, not to a later frozen release.

Runtime bind checks render and execute the shipped Compose configuration with
private fixture names/paths. All five missing sources fail without replacement;
wrong types cannot start a healthy application. Interrupted-DDL checks kill a real
SQLite provisioning process at its native COMMIT barrier and exercise failed
public startup plus compatible-state recovery over actual HTTPS. Helper-response
checks use real Unix transport and reject non-integer versions and non-boolean
success flags without committing mailbox metadata. T20.01/T20.03 mappings require
their own final frozen execution.

Catalogue groups now execute in fresh controllers under the same 192 MiB limit.
The source `47dccdce` monolithic controller was OOM-killed after the separate suites
had passed. That incomplete attempt is preserved. The driver records each group's
container identity before starting it, observes its actual exit/OOM status, and
removes only its verified stopped controller. Unknown/surviving controllers, OOMs
and missing reports stop assembly. The final assembler requires all groups, matching
artifact/image identities and the exact assertions for each passing case. Failed,
skipped and unmapped assertions cannot become passes. Per-group controller progress
and outcomes remain beside the final case reports. This changes test supervision;
it does not increase application/controller memory or application readiness deadlines.

The approved private ACME fixture uses pinned Certbot 5.8.0, Pebble 2.10.1 and
its DNS helper on an internal Docker network. Actual HTTP-01 validation remains
enabled, no port is published and no public certificate request is made. Six
development tests exercise issuance, ordinary no-op and due renewal, acquisition
failure preserving the old pair, strict pair validation, independent real Compose
activation of Maddy/HTTPS consumers, first deployment absence and missing deployed
consumers. Pending activation is resumed in a new Python process from durable
state without invoking Certbot. The corrected complete run passed at source
`47dccdce` (`build/private-acme-clean-rebuild-complete.log`). T15.01, T15.02,
T15.04 and T15.05 now have assertion mappings; staging/crash boundaries and
retention/reboot cases remain unmapped. A development suite pass does not promote
those mappings to frozen acceptance passes.

The Linux driver checks source and runtime file identities before creating its
fixture volume. A standalone native driver receives the frozen source identity
with its image archive; a missing/malformed identity fails closed. Local builds
and fixture execution require 2 GiB free on the actual checkout filesystem, so a
sparse Colima disk cannot hide host exhaustion behind its guest free-space count.
The separate full mock-VM reserve remains 3 GiB. Current certificate/catalogue
fixture admission accounts for the controller, Certbot, private CA/DNS, Maddy,
HTTPS and the unchanged host-memory reserve, totalling 800 MiB. This is an isolated
fixture budget, not a production-host peak measurement.

T08 now includes a real systemd dependency regression using privately named units
inside the owned guest. It derives the installation targets from the actually
installed helper unit, makes its synthetic Docker dependency fail, and starts only
that dependency again. The helper marker must appear within the existing 15-second
readiness bound. The test never changes or restarts the guest's real Docker service.
The helper is enabled for both boot and Docker starts so a failed initial dependency
does not leave it inactive when Docker recovers. Both original and corrected unit
configurations have been exercised against real systemd.

The fresh-target recovery driver under `tests/recovery_*_support.py` uses only the
host standard library and the matched image's authentication utility. It requires a
different empty disposable guest, an external private recovery kit and all 18 saved
roles. It loads images from the recovered archive, keeps authentication closed until
private-terminal password/TOTP recovery, and checks real HTTPS, native mail, metadata,
key permissions and a subsequent kernel boot. Missing host prerequisites, partial
restores, wrong images/targets, unresolved journal entries and failed observations
remain failures. It produces development observations; the original failed attempts,
subsequent operator corrections and source identities are retained separately.
Those observations cannot be imported as frozen case-level T18 passes.

Development suites now fsync unique per-test progress files in `build/`, including
the starting source/image identity and observed controller memory. A `started`
entry without completion remains incomplete. Only the final suite report can claim
its executed checks passed. Unreachable fixture cycles are collected before the
next test; controller limits are unchanged. The earlier emulated `dda75d78` run's
192 MiB controller OOM and original log are retained in `build/`.

The runner checks the original evidence/artifact/configuration/specification
identities using the release checker, compares executing source files and the
complete source file set with the suite archive, and compares the host bundle
with the suite and pinned Restic checksum. It reads bounded archive metadata
without extraction. It distinguishes OCI platform manifests and image
configuration digests from archive digests. The actual selected runtime image
must match the saved image; runtime file hashes must match the executing
qualification image. Source/artifact checks repeat after each group.

```sh
.venv/bin/python tools/prepare_release.py build/selected-candidate --inputs verification/operator-inputs.json
.venv/bin/python tools/run_linux.py --suite qualification --evidence build/selected-candidate/evidence.json
```

Each run creates a new `qualification-UUID/` report directory and a separate
`qualification-UUID-evidence.json` in the original artifact bundle. Existing
reports/artifacts remain intact. A blocked run is useful gap evidence, not a
release pass. The driver uses a private Linux volume, immutable runtime reference,
192 MiB controller and at most 192 MiB sequential image-observation companion;
its native admission budget retains another 128 MiB for the host. These limits
are fixture limits, not measured production resource inputs.

`tools/qualification_target.py` requires existing SSH trust, forces ed25519 and a
new authenticated connection, hashes the negotiated key fingerprint, and reads
only GCE project/zone/instance identity metadata. It compares those actual
observations with the selected inputs. The instance format is
`projects/PROJECT/zones/ZONE/instances/NUMERIC_INSTANCE_ID`. Metadata access
uses the documented [Google metadata protocol](https://docs.cloud.google.com/compute/docs/metadata/querying-metadata).
It does not request service-account credentials or establish operator approval.

`tools/qualification_deployment.py` compares the actually running dashboard
platform image with the qualified image archive; compares installed host code,
rendered units, launcher and Restic with the host bundle; and reuses the
type-sensitive effective configuration binding. Files must have matching content,
owner/group and restrictive mode, without symlink substitution or extra host
modules. The library tree is `/usr/local/lib/mail-admin`; rendered unit/tmpfiles/
journald/Docker-ordering destinations are explicit in the adapter. Qualified
cutover roles bind the Maddy Compose and renewal files.

TLS bytes are validated against the current root activation record. A renewed
complete generation may pass without changing release artifacts. A changed key,
stale mount, unpublished generation or failed served fingerprint cannot pass.
Only after all comparisons succeed does the adapter return the three qualified
archive/configuration hashes plus the observed Maddy image ID. It starts no
services and changes no deployment state.

Adapter unit regressions are development checks. T01.05 still needs its complete
isolated execution and binding through the trusted catalogue runner. Full
boot/reboot/crash/cutover/rollback, all remaining catalogue assertions, whole-host
resource measurements and production acceptance are separate outstanding work.
Do not fill gaps with fixture reports, plan/model passes or copied expectations.

## Larger local mock

The shared-server mock's 256 MiB guest did not establish a usable application host.
`tools/run_local_mock.py` prepares a separate 1024 MiB amd64 guest inside a native
ARM64 QEMU tool container on the local ARM64 Docker engine. Its kernel, systemd and
Docker daemon are real; its CPU is software-emulated. This cannot establish native
production performance. The pinned guest Docker payload remains Linux/amd64 even
when QEMU itself is ARM64.

```sh
docker build --platform linux/arm64 -f tools/vm/Dockerfile --build-arg MOCK_BASE=python:3.11.17-slim-bookworm@sha256:8f51ccb8569b75cf967ffe3e1638b9953042bf36142d7032cbb6f0696c9cd2c6 -t mail-admin:local-mock .
.venv/bin/python tools/run_local_mock.py create mail-admin:local-mock
.venv/bin/python tools/run_local_mock.py guest build/RECORDED_LOCAL_MOCK/state.json uname -m
```

Admission requires a Unix endpoint, the same ARM64 engine, 3 GiB free on both Mac
and Linux disk, and 1408 MiB available Linux memory. The container has a 1280 MiB
memory/swap bound, one CPU, 64 tasks, a read-only root, no capabilities, no host
devices or Docker socket, and one private Linux volume. Only its guest SSH port is
published on loopback. Separate private SSH keys/trust and exact image/container/
engine/volume identities scope controls. Failed setup remains available for review;
this driver removes no disks, reports, images or caches. Offline initial preparation
reuses the tested sparse-copy/changed-block operations with filesystem checks and
configuration readback; old nonzero bytes are still overwritten by new zeros.

The local mock image built and its guest reached authenticated SSH, completed
cloud-init and started Docker/containerd. The observed instance ID matched its
private seed. This is bootstrap evidence, not an application boot/reboot/crash pass.
The operator explicitly approved unused build-cache cleanup. The exact command,
before/after image/container/volume identities and original log are retained under
`build/approved-build-cache-cleanup-20261023T042302Z/`. It removed 6.651 GB of unused
cache. Discarding already-free blocks on the local Docker filesystem reclaimed
Mac disk space without removing another file or changing the remote server.
No application deadlines or shared-server resource reserves were relaxed.
If Docker no longer retains the old tool image reference after a later tool rebuild,
restart admission can use `--capacity-image sha256:...` before `start-existing`.
The supplied utility must be an immutable, native ARM64 image running as
`10001:10001`; it only measures capacity with the same restricted utility container.
The existing mock container ID, original image, volume and memory limits remain
unchanged. Wrong-platform/root/mutable utilities are rejected. The old missing-image
restart error and subsequent admitted start are retained under `build/`.

The Mac's published loopback forwarding later stopped returning SSH banners while
the same guest listener remained responsive inside the exact QEMU container. SSH
now uses a binary socket proxy executed only in that validated container, at its
fixed private loopback port. Existing SSH key/trust checks remain in force. A
shared-control socket cannot substitute a stale connection. Unit checks exercise
binary bytes/input EOF, peer reset, immutable container IDs and rejected remote
bindings. The SSH client is still outside the guest; no Docker socket, host device
or additional capability is granted to QEMU or the dashboard.

## Reviewed assertion expansion

The frozen `03641e33` registry has 31 reviewed case mappings. Its added assertions exercise
replacement MFA through the actual API, including password-plus-existing-factor
requirements, ten-minute expiry and atomic invalidation. Password-change checks
populate pending login/reauthentication/Email OTP/replacement state, verify its
removal, preserve replay/recovery consumption and complete fresh login. A real
SQLite trigger rejects the password write to prove invalidation rolls back with
the failed transaction. A concurrent factor verifier is held before its transaction
until the password change commits; it cannot issue an old-revision session.

Clock-driven checks exercise the exact fifteen-minute idle and twelve-hour absolute
session boundaries. Streamed requests exercise 65,536/65,537 bytes with absent,
understated and overstated Content-Length. Cancelled work runs real Argon2 hashing
and keeps its permit until the worker finishes. Existing real-Maddy folder-warning,
filesystem-before-commit failure and password-input assertions, plus real local
SMTP TLS/authentication/sender/delivery checks, supply the other new mappings.
The local SMTP peer is synthetic; no external email delivery is claimed.

The later registry adds real API coverage for shared route budgets, spoofed
forwarding headers, restart persistence, reservation before hashing and exact
15 peer/30 administrator boundaries over 900 seconds. Concurrent tests cover atomic
reservation. Shared Email OTP/relay tests exercise 3 peer/5 administrator send slots
and the resend delay. These assertions map T04.04/T04.07/T04.08; their frozen-bundle
execution is recorded separately in implementation status.

## Guest helper lifecycle assertions

`tests/host/test_helper_lifecycle.py` exercises the actual systemd helper and guest
Docker daemon outside Docker, using the product Unix client inside the exact
non-root consumer. The root-owned fixture context must match the cloud-init UUID,
Linux/amd64 platform, synthetic mailbox, immutable container/image, read-only helper
bind, dropped capabilities and isolated network. These checks refuse a production
consumer name. Docker restart cannot kill the external host test runner.

From an extracted frozen qualification suite in that disposable guest:

```sh
sudo python3 tools/run_host_qualification.py /root/fixture/bundle/evidence.json
```

`tests/host/test_dashboard_lifecycle.py` additionally uses real non-root dashboard
HTTPS for late-helper authorization and completed-request retries. A before-reboot
record binds the running containers, completed operation, credential digest, installed
files and kernel boot ID to the exact frozen bundle:

```sh
sudo python3 tools/prepare_host_reboot.py /root/fixture/bundle/evidence.json
```

This prepares observations only. An external fixture controller performs the actual
guest reboot or power cycle. Afterward the host runner verifies T08.04 first, before
other tests can restart a failed consumer. The verifier requires a new kernel boot
on the same instance, unchanged artifacts/containers, restrictive paths before Docker
autostart, shared bind inodes, working authenticated HTTPS and preserved outcomes.
Missing, stale or differently bound preparation cannot pass.

The runner verifies the frozen source and artifacts, installed host/helper/shared file
set, rendered helper/path/order/tmpfiles/journald files, launcher and actual consumer
image before and after running reviewed T08 assertions. A new
`host-qualification-UUID/` report and sibling evidence file retain the original
artifacts; a failed or missing assertion keeps its case failed. Collect
those files unchanged into the original local bundle and pass that sibling evidence
file to the ordinary Linux catalogue command. The collector verifies report hashes,
artifact/specification/suite identities, observed installation/source, guest IDs,
timestamps and exact case/assertion sets before merging cases. Unmapped
cases, skipped assertions and stale or altered reports cannot become passes.

The frozen `0f01bbb0` host run passed both assertions. It used a software-emulated
amd64 kernel and standby consumer, not the complete dashboard or production host.
Its actual report is recorded in [implementation status](status.md). The separate
manual full guest reboot prototype is not mapped into the catalogue.

The standby consumer must handle SIGTERM explicitly. Its original PID 1 sleep did
not, leading to forced daemon shutdown and a stale containerd task in the first
four-case run. Docker documents this
[PID 1 signal behavior](https://docs.docker.com/reference/cli/docker/container/attach/).
The failed consumer and engine journal are retained; replacing it does not turn that
run into a pass. The actual web/Maddy consumers recovered during that failed run.
Raw case results are now persisted before the final installation observation. A
post-run drift/error leaves those assertions available and produces no qualifying
report/evidence reference.

At frozen source `e537d603`, all four T08 assertions passed against unchanged
installed files and image/configuration bindings, including a new real guest kernel
boot. The report and original failed predecessor remain recorded in implementation
status. These software-emulated host results do not establish production acceptance,
resource headroom or full replacement-server restoration.

The frozen `03641e33` bundle executed all 31 mappings successfully and recorded
the other 80 cases as `not_run`. Its artifact set is
`14a3cae9a2ab15c1853037f5a190d0806fa18d30acdda58f18d3ff08f8f72f42`;
reports are retained in
`build/release-candidate-03641e33/qualification-c923def2-318c-4736-a782-b94dfaa164ff/`.
The candidate checker rejects this incomplete evidence. General development-suite
counts do not fill the other catalogue gaps.
The restored web fixture now closes before the original Maddy fixture resumes,
including on failure; this prevents an unbudgeted 128 MiB/96 MiB overlap. The
two-Maddy companion budget and the additional host reserve remain unchanged.

The catalogue executes a shared assertion once per frozen group controller.
Every dependent case still records its exact reviewed assertion set. Only a complete
successful execution enters the in-memory run cache; failed, skipped, incomplete or
misidentified assertions cannot be reused. Every group starts with an empty cache;
assertions needed by different groups execute again in their separate controllers. Group
timestamps include the actual start of the shared execution, and frozen source,
artifact and runtime bindings are checked as before. This removes measured duplicate
fixture work within one run; it does not import development or older-source reports.
Runner regressions cover reuse, fresh-run isolation, failure/skip/mismatch rejection
and accurate execution timestamps.
