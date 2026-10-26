# Ubuntu testing handover

This is the original Mac transfer checkpoint. The active workstation is CachyOS,
not Ubuntu; consult the [Linux continuation](linux-continuation.md) for its actual
setup, source changes and missing private artifacts. Identities and results below
remain historical and must not be applied to the modified working tree.

The Linux continuation now records **111 / 111 passing catalogue cases** and a
passing candidate evidence check for source `76307198` on 26 October 2026.
Production preflight and deployment are still outstanding. The checkpoint below
retains its original historical results.

Prepared 25 October 2026. This handover covers the application and its unfinished
qualification on a better **native Ubuntu amd64** machine. It authorizes no
production deployment. Continue with one working candidate, focused fixes, then
one complete acceptance run. Keep original deadlines, memory limits and failed
attempts.

## Current state

The application, helper, host operations and all 111 acceptance mappings are
implemented. **The current candidate is not qualified.** More RAM and storage
should remove some infrastructure constraints; they do not establish that the
remaining assertions pass.

| Scope | Recorded result |
| --- | --- |
| Historical candidate `a9c00d9e` | **69 passed / 0 failed / 42 not run / 111**. Preserve unchanged; it cannot qualify the current source. |
| Current source `a70f7897` | **94 passed / 1 failed / 16 not run / 111**. The frozen catalogue completed; the candidate evidence checker remains blocked. |
| Supporting checks on current source | 452 unit and 72 API tests, Ruff, strict mypy, frontend lint/type checks and specification checks passed. Runtime and qualification images built successfully. |
| Actual HTTPS browser on current frozen candidate | Passed on the Mac; its runner and image bindings are platform specific. |
| Current native runtime comparison | Failed. After correcting the missing Maddy fixture, API crash, mail-flow and schema modules passed; secret-key restart timing still failed. |
| Current host/reboot/cutover/replacement recovery | Matching complete evidence still missing. Earlier prototype successes cannot fill these cases. |

Read the [111-case work list](qualification-worklist.md),
[qualification tooling](qualification.md), [implementation status](status.md) and
[operator handoff](operator-handoff.md). The contract and acceptance catalogue are
under [docs/mail-admin-implementation](../docs/mail-admin-implementation/README.md).
No dashboard API or database-schema change is planned for this continuation.

### Jobs and retained fixtures

At **2026-10-25 03:29 UTC**, the original Mac catalogue driver had finished:

- Former PID `24101` had exited. Its command was `tools/run_linux.py --suite qualification --evidence build/release-candidate-a70f7897/evidence.json`.
- All **63 assertion modules completed**, with no OOM or surviving controller.
  One failed module, `tests.maddy.test_guarded_deletion`, failed case **T17.01**.
  The other 16 unqualified cases require host observations, listed below.
- Log: `build/frozen-catalogue-a70f7897.log`.
- Raw reports: `build/release-candidate-a70f7897/qualification-25c9c359-9e16-4f39-a628-3aa96342c78a/`.
- Completed totals: `result.json` inside that report directory. Its sibling
  `qualification-25c9c359-9e16-4f39-a628-3aa96342c78a-evidence.json` binds the group reports.
- No container with the `mail-admin.fixture=true` label was running.
- The owned Mac mock `bddad6697a220835a56e2472a11d405740d874caaabc87cdd286075ddf60216c`
  was stopped, exit 0, without OOM. Its private state is
  `build/mail-admin-local-mock-115167ae-192e-4b01-b32b-2950c8a1ad45/state.json`.
- Native comparison jobs had finished. Production Maddy identity, mounts,
  configuration and start time were unchanged in their before/after observations.

This is a dated checkpoint. On the Mac, inspect before starting another run or
copying report trees:

```sh
python3 build/frozen-progress-a70f7897.py
ps -p 24101 -o pid,ppid,etime,command
docker ps --filter label=mail-admin.fixture=true \
  --format '{{.ID}} {{.Names}} {{.Status}}'
```

The recorded catalogue result is complete and must remain immutable. Preserve its
failure and all earlier partial reports/logs. If a later job is interrupted,
inspect its verified driver and exact recorded controllers before stopping any
survivors: a stopped parent does not prove fixture children have stopped. Do not
use blanket Docker cleanup or terminate unrelated projects. No new test job was
started to prepare this document.

## Candidate identities to preserve

The tracked working tree was clean before this document was added, at commit
`787db707fe6035065f2a659f39ef671ff0d20892` (`changed application`). The release
bundle, reports, downloaded inputs and local fixture state are ignored by Git;
a clone or source archive alone is insufficient.
This handover and the refreshed `verification/status.md` and
`verification/qualification-worklist.md` are uncommitted documentation changes;
the export commands below copy them separately from the recorded source commit.

| Identity | Value |
| --- | --- |
| Source SHA-256 | `a70f7897f23e42914901f2ac1c293fedab07f7208ab19da8d7c10d8863a5cd0d` |
| Bundle | `build/release-candidate-a70f7897/` |
| Artifact-set SHA-256 | `3b0f6a2e77d3f4bf9fdce3748ee4089d4aa3066d349558af749c0a61804da0b4` |
| Runner revision / suite archive SHA-256 | `fe63a60e548c9fe3fe8d5842238127eeb1e0b2c0596be317b19b33823e52cc66` |
| Specification SHA-256 | `8aeea6afbd82d1ac880f5207417b653fd067b03a4fe4ae19c49849018b0e9758` |

Docker stores can expose different identities for the same exported image. These
are the recorded identities, not interchangeable strings to write into reports:

| Image | OCI index on Mac | amd64 platform manifest | Configuration ID |
| --- | --- | --- | --- |
| `mail-admin:candidate` | `sha256:79cb0a07547328e7697e50590dae8e78b384f0c1b25077e54e073ef1954f473d` | `sha256:cc9b82e89e921dcb1df17755a8946be1eac11133e2cf33be7689e3533f40f44b` | `sha256:61c526229e58ab7a8909665634a6635301d73be1442e65b5858168433d671342` |
| `mail-admin:qualification` | `sha256:fc418fa18a57ae037ab0893ac07c530737f4918adaa0131ea0d675874ec45af6` | `sha256:41d17f7696bdb310509d7dbbd7a7002eb730566ed2894440fa22fcbf8d463795` | `sha256:936beda3aae5c92c4a0c9202b84800ef357841bdf5563484ef0e5f567b241bd5` |
| `mail-admin:maddy-postmaster-fixture.1` | `sha256:11c98cb08dc68166b4238527b00088e0d8be175e9fb9528c210b45600d43bfaa` | `sha256:5539c5248519b2324fa4c52a871139ee7cc48043765237581af865735298cdb3` | `sha256:3e711e7fa1295e4e16e7170e1900ee555d192b9a1147f0934aefbbff758b1f6c` |

The Maddy resolver accepts its recorded **index or configuration ID**, not the
standalone platform-manifest ID. Export the full tagged image **without
`--platform`**. A previous platform-only transfer produced an unrecognized identity
on containerd. At this historical checkpoint the bulk exporter in
`tools/run_remote.py` used `--platform`. The Linux continuation corrected it to
preserve the complete image; retain the original transfer failure as evidence.

The isolated postmaster patch and hash-pinned build inputs already have user
approval. Do not ask again. Preserve
`build/proposed-maddy-postmaster-fix.md`, `build/maddy-postmaster-approved/` and
[the fixture provenance](../tools/maddy-fixture/README.md). The Linux executable
hash is `e4596fed2f1f58ad7dda3bebeece94f806a5e56cc3bba6f2c0bc37ce0acf2dd6`.
Its recorded compiler archives are Go/Zig for the Mac; transferring the existing
image avoids an unnecessary rebuild with different inputs. The unpatched upstream
Maddy image cannot substitute for this fixture. Production image selection remains
separate.

## Transfer from the Mac

Keep the source, original frozen bundle and evidence together. Preserve relative
report paths. Copy these items through private storage or SSH:

| Item | Purpose |
| --- | --- |
| Source at the recorded commit, plus the three updated verification documents | Application, tests, drivers, locks, specification and current handover/status/work list. Carry any subsequent uncommitted changes explicitly. |
| Entire `build/release-candidate-a70f7897/` | Four frozen artifacts, evidence, browser sidecar, completed catalogue and raw attempts. `dashboard-image.tar` contains only the runtime image. Check that no later writer is active. |
| Exported runtime, qualification and patched Maddy images | Reproduce the candidate without rebuilding. |
| `build/maddy-postmaster-approved/` and patch proposal | Approved source archives, executable, parser results and build receipts. |
| Logs and native results listed below; historical `a9c00d9e` report | Preserve failures, provenance and the historical total. |

Optional source/image export commands, from the project root on the Mac:

```sh
mkdir -p build/ubuntu-handover
git archive --format=tar \
  --output=build/ubuntu-handover/source-787db707.tar \
  787db707fe6035065f2a659f39ef671ff0d20892
cp verification/ubuntu-handover.md verification/qualification-worklist.md \
  verification/status.md build/ubuntu-handover/
docker image save --output build/ubuntu-handover/fixture-images.tar \
  mail-admin:candidate mail-admin:qualification \
  mail-admin:maddy-postmaster-fixture.1
cd build/ubuntu-handover
shasum -a 256 source-787db707.tar fixture-images.tar ubuntu-handover.md \
  qualification-worklist.md status.md > SHA256SUMS
cd ../..
```

These commands do not package the ignored bundle or reports; copy those separately
while their writers are idle. Check image tags against the table before
exporting. On Ubuntu, verify `SHA256SUMS` with `sha256sum -c` in the transfer
directory before extraction/loading.

Do not publish the transfer kit. Raw browser/guest fixture directories can contain
synthetic passwords, private TLS material or SSH keys. Do not copy the entire
`local-state/`, `.env`, `.venv` or `frontend/node_modules`. If an R2 recovery test
needs existing credentials or a repository password, transfer only those selected
files separately and privately. Preserve their permissions. A repository password
must remain recoverable independently of the source guest. Never put credentials
into this document, shell command arguments or Git.

## Ubuntu preparation and first checks

Use the supplied machine as an isolated test host. A practical allocation is
8 GiB RAM, at least two CPU cores and 20 GiB free disk; this is planning headroom,
not a change to enforced fixture budgets. Use native `x86_64`/`amd64` Docker,
a local Unix Docker socket, Docker Compose v2, Python 3.11+ with venv, and Node
24/npm for frontend checks. Python 3.11 matches the reference runtime. The images
pin Python 3.11.17 and the frontend build pins Node 24.22.1. Do not install new
unapproved dependencies or use an unpinned Maddy/ACME replacement.

Assuming transferred files are under `~/mail-handover/`:

```sh
mkdir -p ~/mail-qualification
tar -xf ~/mail-handover/source-787db707.tar -C ~/mail-qualification
cd ~/mail-qualification
cp ~/mail-handover/ubuntu-handover.md ~/mail-handover/qualification-worklist.md \
  ~/mail-handover/status.md verification/
mkdir -p build
cp -R ~/mail-handover/release-candidate-a70f7897 build/
docker image load --input ~/mail-handover/fixture-images.tar
uname -m
docker info --format '{{.OSType}} {{.Architecture}} {{.ID}}'
docker compose version
node --version
python3.11 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
npm --prefix frontend ci --ignore-scripts
```

Use an available Python 3.11+ executable for venv creation if it has another name;
record its actual version. Do not copy the Mac venv. Commands below use
`.venv/bin/python`. Run all project commands from this checkout root.

Check capacity once before scheduling a batch. The existing fixture disk check
requires 2 GiB free on the checkout filesystem. Also inspect `/var/lib/docker`'s
filesystem and available memory; guest disk capacity cannot substitute for host
free space.

```sh
free -m
nproc
df -h . /var/lib/docker
.venv/bin/python tools/check_fixture_disk.py
```

Recorded admission budgets from `tools/resources.py` include companions and a
128 MiB host reserve:

| Batch | Controller limit | Minimum available RAM |
| --- | --- | --- |
| Qualification / ACME | 192 MiB | 800 MiB |
| Maddy | 192 MiB | 544 MiB |
| Runtime development suite | 128 MiB | 480 MiB |
| Unit / API Linux controllers | 192 MiB | 448 MiB |

The larger mock additionally uses a 1024 MiB guest inside a 1280 MiB controller,
with a 3 GiB disk reserve. Account for simultaneous workloads. Run heavy batches
and source/replacement guests sequentially. Preserve no-swap-above-memory bounds,
CPU/task limits, tmpfs accounting, the 15-second application readiness deadline,
and existing operation deadlines. Timebox infrastructure diagnosis to 15 minutes,
then record the specific blocker and continue independent work.

Fetch only the already approved ACME image references if they were not transferred;
fixture runners never pull them automatically:

```sh
.venv/bin/python - <<'PY'
import subprocess
from tools.fixture_images import ACME_IMAGES
for image in ACME_IMAGES:
    subprocess.run(['docker', 'pull', '--platform', 'linux/amd64', image], check=True)
PY
.venv/bin/python tools/fetch_restic.py linux_amd64
```

Restic is pinned to 0.19.1 with archive/executable hashes. Validate the transferred
source, four artifacts and loaded application images before testing:

```sh
.venv/bin/python - <<'PY'
from pathlib import Path
from tools.qualification_bundle import QualificationBundle
from tools.run_remote import image_snapshot, verify_images
from tools.run_tests import source_digest
from tools.maddy_fixture import fixture_reference
expected = 'a70f7897f23e42914901f2ac1c293fedab07f7208ab19da8d7c10d8863a5cd0d'
assert source_digest() == expected, 'Transferred source changed'
bundle = QualificationBundle(Path('build/release-candidate-a70f7897/evidence.json'))
bundle.verify_source()
images = image_snapshot()
verify_images(images)
print(bundle.identity)
print(images)
print('Maddy fixture:', fixture_reference())
PY
```

If this fails, resolve the missing/mismatched input before starting tests. Do not
edit evidence hashes or image IDs to make the checker accept a transfer.

## Resolve these failures first

Run the affected modules against the transferred images before rebuilding:

```sh
.venv/bin/python tools/run_linux.py --suite maddy --module test_guarded_deletion
.venv/bin/python tools/run_linux.py --suite runtime --module test_secret_keys
.venv/bin/python tools/run_linux.py --suite runtime --module test_schema_startup
.venv/bin/python tools/run_linux.py --suite maddy --module test_operational_recipients
```

| Issue | Evidence and next action |
| --- | --- |
| Actual HTTPS normal deletion | Current frozen Mac attempt errors at `tests/maddy/test_guarded_deletion.py:213`: the response lacks `state`. The status/body were not retained, so the exact API error is unknown. Reproduce on native Ubuntu at the original deadline; capture a sanitized status/error identifier if it fails. The other four assertions in that module passed. |
| Cold start / key recovery | Current native `test_secret_keys` still misses an original 15-second lifecycle deadline. Resource stalls were observed, but an application defect has not been excluded. Preserve the failed result even if the new machine passes. A later healthy container does not pass an earlier readiness deadline. |
| Postmaster | Approved patch passes parser and actual SMTP-to-IMAP assertions. Confirm bare/qualified postmaster, ordinary recipients, malformed addresses and ESMTP behavior using the exact fixture identity. No additional approval is pending for this isolated patch. |
| Capacity | Native guarded deletion was never run: 533,929,984 bytes available versus 570,425,344 required. Mock nested cutover was refused at 620,843,008 versus 637,534,208 bytes. Record these as admission blockers, not test failures or passes. |

These are partial development checks, not acceptance totals. Their distinct report
directories are under `build/SUITE-partial-UUID/`. If a fix is needed, make it in
this one workspace, add a regression, rebuild the affected images and rerun its
subsystem. Keep earlier artifacts and failed attempts intact.

## Two portability gaps before all 111 can pass

**HTTPS browser:** `tools/browser-fixture.json` pins Mac Node 22.20.0 and Chrome
155.0.8059.86 executables/framework by absolute path, version and SHA-256.
`tools/browser_inputs.py` also constructs a macOS framework path. Setting
`MAIL_ADMIN_CHROME` alone does not make it work on Linux. Frontend compilation's
Node 24 requirement is a separate input.

For Ubuntu-native browser execution, extend the existing runner/input validation
and its regressions for reviewed Linux binaries; approve any additional downloads
as needed, pin their real identities, then freeze a new source/candidate. The
already passing Mac browser sidecar can be consumed only while all source,
artifact, runner, freshness and **exact observed image IDs** still match.
A different Docker store's platform/configuration IDs may prevent reuse. Evidence
expires after 24 hours. Do not rewrite the report or disable browser verification.
`make browser` is an HTTP UI development fixture and cannot fill the actual HTTPS
browser acceptance cases.

**Host/reboot/replacement:** `tools/run_local_mock.py` requires a local ARM64
engine. Its retained state and `tools/record_replacement_isolation.py` use the
Mac-specific `LocalMock` controller. They cannot simply be pointed at Ubuntu.
`tools/run_mock_host.py` supports an amd64 software-emulated VM on an authorized
SSH host, but its existing 256 MiB guest/384 MiB outer profile was insufficient
for the complete application. It is not a verified drop-in replacement for the
larger local mock.

Provide a reviewed amd64 fixture-controller adaptation using the existing helpers
and approved dependencies, or retain the Mac for these fixture controls. Keep
the guest/resource contracts, identity checks and real reboot boundaries intact.
Such an adaptation changes frozen source and requires matching new evidence.
Do not run host lifecycle assertions directly on an ordinary Ubuntu workstation:
they restart Docker, manage systemd and cross reboot/power-cut boundaries.
They require root **inside the exact disposable amd64 guest**, matching cloud-init
UUID, root-owned fixture context and restricted synthetic consumer. Do not bypass
those checks or copy an old UUID/context onto a different machine.

The build-only `build/*-a70f7897.py` host staging/orchestration scripts are unfinished
Mac-bound helpers. Their frozen host batches were not executed successfully.
They are references for a reviewed adaptation, not Ubuntu installation commands.

## Complete the remaining host batches

Transfer unchanged artifacts once. Stage the exact frozen host/suite archives,
runtime and qualification images into disposable guests, then verify installed
source, rendered units, configuration and image before/after each batch.

| Cases | Runtime evidence required / existing helpers |
| --- | --- |
| T08.01–T08.04 | Real helper/socket/Docker dependency recovery and kernel boot. `tests/host/test_*lifecycle.py`, `tools/prepare_host_reboot.py`; prepare before the externally controlled reboot, verify T08.04 first afterward. |
| T05.05, T15.03, T15.06 | Interrupted recovery remains closed; actual power cuts at TLS staging/publication/activation boundaries; retained referenced/rollback pairs across a new kernel boot. `tests/kernel_scenario_worker.py`, `tests/host/test_kernel_scenarios.py`; bind `MAIL_ADMIN_KERNEL_OBSERVATION`. |
| T16.01–T16.05 | Real legacy-writer draining, timer persistence, partial installation across reboot, cutover, rollback and lock release. `tests/legacy_systemd_support.py`, `tests/cutover_systemd_support.py`, `tests/cutover_reboot_support.py`; bind `MAIL_ADMIN_CUTOVER_REBOOT`. |
| T18.02–T18.04 | Complete replacement recovery, selected objectives and source exclusion. `tests/recovery_capture_support.py`, `tests/recovery_target_support.py`, `tests/recovery_evidence_support.py`, `tools/record_replacement_isolation.py`; bind `MAIL_ADMIN_REPLACEMENT_CONTEXT`. |
| T22.02 | Actual guest journal rotation together with the matching local log/history assertions. `tests/host/test_log_retention.py`. |

Full replacement must verify all **18 state roles** using the approved real R2
repository and an immutable, verified off-server capture. Retain real private-CA
ACME account/lineage state, before/after-cutoff delivered-mail markers and the
repository password. Stop and observe the source, recover onto a different empty
target without attaching its source disk, recover credentials privately, then
verify actual HTTPS, helper, SMTP/IMAP and a new kernel boot. Selected objectives
are RPO **93,600 seconds (26 hours)** and RTO **14,400 seconds (four hours)**.
Run the source and replacement guests sequentially. Phase observations are
immutable; earlier prototype/manual-certificate records cannot complete this run.

Only after preparing the matching guest contexts/observations, the existing host
entry points are, for example:

```sh
# Inside the owned disposable guest, with its extracted frozen suite:
sudo python3 tools/prepare_host_reboot.py /root/fixture/bundle/evidence.json
# The external owned-guest controller then performs and observes the real reboot.
sudo python3 tools/run_host_qualification.py /root/fixture/bundle/evidence.json --group T08
```

Other registered host groups are T05, T15, T16, T18 and T22. Host output can remain
`blocked` because it contains only the group's host portion. Each invocation emits
a new sibling `host-qualification-UUID-evidence.json`. Pass that file to the next
host invocation so earlier group references remain in the chain. When changing
guests, carry that reference file and its unchanged reports with the bundle.

Collect the unchanged raw reports and the last sibling evidence file into their
original relative paths under the local bundle. The catalogue uses
`tools/qualification_host.py` to verify hashes, frozen identities, installed-source
bindings, timestamps and exact assertions before merging host/local outcomes.
Pass the collected evidence file to the catalogue, as described in
[qualification tooling](qualification.md). Do not hand-edit case outcomes.
A configuration inspection is insufficient runtime evidence.

## Final verification and reporting

During fixes, run affected checks only. Once the implementation and portability
work is complete, run the full build/static/application/specification checks:

```sh
make build
make lint
make typecheck
make unit
make integration
make plan
docker build --platform linux/amd64 --target qualification -t mail-admin:qualification .
```

Freeze source, image, configuration and runner identities into a **new** destination
if anything changed. Keep pending production inputs explicit in
`verification/operator-inputs.json`; do not reuse a Mac private-key path as an
Ubuntu credential. A new bundle name below is an example, not an existing result:

```sh
.venv/bin/python tools/prepare_release.py build/release-candidate-ubuntu \
  --inputs verification/operator-inputs.json
```

Complete and collect the matching host observations, retaining all group references
in the last emitted evidence file. Then run the actual HTTPS browser and full
catalogue against that file. Replace `UUID` below with that real filename:

```sh
MAIL_QUALIFICATION_EVIDENCE=build/release-candidate-ubuntu/host-qualification-UUID-evidence.json
.venv/bin/python tools/run_https_browser.py \
  --evidence "$MAIL_QUALIFICATION_EVIDENCE"
.venv/bin/python tools/run_linux.py --suite qualification \
  --evidence "$MAIL_QUALIFICATION_EVIDENCE"
```

A local-only diagnostic catalogue can use the original bundle `evidence.json`, but
missing host reports will keep its host/composite cases unqualified. Do not
overwrite that original file to insert results.

The catalogue runs 63 fresh bounded assertion modules in the current source,
sharing assertions once. A fresh Ubuntu engine needs a **fresh run directory**;
Mac module results cannot resume there. On the same engine, an interrupted run
can use `--resume BUNDLE/qualification-UUID` only when all frozen source, artifact,
image, inventory, controller and engine identities still match and no controller
survives. Completed results are immutable; failed/interrupted attempts remain.
Any later fix needs fresh matching evidence.

Read the generated `qualification-UUID/result.json` for
`passed_cases`, `failed_cases`, `not_run_cases` and `required_cases`. Run the
candidate checker against the **generated `qualification-UUID-evidence.json`**,
which contains report references; the original `evidence.json` remains pending:

```sh
.venv/bin/python docs/mail-admin-implementation/tools/check_release.py \
  build/release-candidate-ubuntu/qualification-UUID-evidence.json --phase candidate
```

Local completion requires **111 passed / 0 failed / 0 not run** and a passing
candidate evidence checker. Report concrete blockers otherwise. Unit/API counts
are supporting information. Missing alert-provider credentials and external
monitoring remain separate release inputs and should not stall independent
fixture testing. Preflight, rollout approval and production deployment are later
gates. No paid resources or production changes are part of this handover.

## Evidence worth carrying forward

Paths below are relative to the original checkout; keep their contents unchanged:

- `build/browser-dns-fixed-source-checks-20261025.log`
- `build/working-source-runtime-build-20261025.log`
- `build/browser-dns-fixed-qualification-build-20261025.log`
- `build/frontend-node24-verification-20261025.log`
- `build/frozen-catalogue-a70f7897.log` and its complete original bundle directory.
- `build/frozen-https-browser-a70f7897.log`; sidecar
  `build/release-candidate-a70f7897/https-browser-35008a48-e400-4d66-94ee-af7a4af3909c/observation.json`
  and `browser-observation-reference.json`.
- `build/native-runtime-frozen-a70f7897-701fe5e8-c048-4bc5-a3dc-e4a3045345cb/`,
  including `missing-input-followup-96e930e5-3039-4d59-837e-db41d5332a25/`.
- `build/frozen-native-runtime-a70f7897.log`,
  `build/native-affected-staged-followup-a70f7897.log`,
  `build/native-guarded-deletion-admission-a70f7897.json`.
- `build/cutover-service-capacity-20261025.json`,
  `build/legacy-systemd-development-20261025.log`,
  `build/mock-mac-capacity-handoff-20261025.json`.
- Historical total:
  `build/release-candidate-a9c00d9e/qualification-4fdf4f33-0546-4051-8392-9d2e52fe9e37/reviewed-summary.json`.

Handover verification: source export/digest, frozen artifact hashes, suite/host
consistency, loaded image identities, CLI arguments, file/link targets and command
syntax were checked on the Mac. The completed catalogue above was collected from
the original run during handover preparation. Ubuntu execution and the missing
host acceptance have not been verified; local qualification remains blocked.
