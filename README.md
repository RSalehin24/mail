# Mail Admin

Implementation of [the reviewed contract](docs/mail-admin-implementation/README.md).
The HTTPS web application runs without Docker access or a Maddy data mount.
A root Unix-socket helper owns mailbox changes; root host tools own TLS, backups,
and recovery. Production deployment requires complete qualification, operator
inputs, and explicit approval. Remote fixture testing is a separate operation.

Paths: `frontend/` (React/MUI), `backend/` (API/domain/data), `shared/` (validators),
`helper/` (private Unix service), `host/` (operator lifecycle), `tests/` (application
assertions), `tools/` (build and evidence adapters).

Use Python 3.11 and Node 24 LTS. Python 3.12 on macOS is a development check only.
For the new Linux workstation, read the [setup checkpoint](verification/linux-continuation.md).
Image builds require Docker Engine, the Buildx plugin (BuildKit), and Docker
Compose. Install all three before `make build`; the legacy builder cannot execute
the Dockerfile's wheel-cache bind mounts.
Install locked Python inputs into `.venv` and run `npm ci` in `frontend/`:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
npm --prefix frontend ci --ignore-scripts
```

The dependency locks are generated from package-manager resolution, never edited.
Regenerate the Python lock after an approved dependency change:

```sh
mkdir -p build
.venv/bin/python -m pip install --dry-run --ignore-installed -r requirements.in --report build/runtime-resolution.json
.venv/bin/python tools/lock_dependencies.py build/runtime-resolution.json requirements.lock
```

```sh
make build
make lint
make typecheck
make unit
make integration
make maddy-integration
make runtime-integration
make acme-integration
make browser
make plan
```

`make build` builds the pinned Linux/amd64 multi-stage image locally.
`make build` downloads hash-locked Linux/amd64 wheels and builds with pinned Node
24 and Python 3.11 stages. Network downloads happen outside the mail server.
The separate qualification image contains development tools and a Docker client;
those tools are absent from the runtime image.
Python bytecode is generated during the image build with source-hash validation;
the read-only runtime does not need to compile sources at each cold start.

`make check` runs Ruff, strict mypy, TypeScript checks, unit and API integration checks.
`make maddy-integration` uses the approved, pinned Maddy 0.9.5 postmaster fixture,
synthetic mail, a Linux volume,
and loopback-only temporary ports. Every fixture container has a unique name; a
fixture-only Docker adapter maps the helper's fixed `maddy` target to that name.
`make runtime-integration` tests the actual non-root HTTPS image, real Unix socket,
TLS/key permissions, enrollment, cookies, restarts and storage failures. Runtime
and Maddy modules use fresh controllers; runtime controllers keep their 128 MiB
limit. A complete suite result requires every module with matching source/image
identities, successful exits and no skipped assertions.
`make linux-check` runs these suites, the private ACME suite and unit/API checks
in Linux containers.

`make acme-integration` uses the separately approved pinned Certbot, Pebble and
challenge/DNS helper images in an internal Docker network. Pull the exact
Linux/amd64 references in `tools/fixture_images.py` before running it; the runner
never pulls images. No port is published and the CA actually validates HTTP-01.
These development checks do not establish public certificate issuance, host reboot
behavior or complete certificate acceptance. Production Certbot selection remains
a separate release input.

Image-build targets and the Linux fixture runner require at least 2 GiB free on
the actual checkout filesystem before Docker work starts. This also checks the
Mac filesystem beneath a sparse Colima disk; guest `df` alone cannot establish
available host capacity. The separate full mock-VM reserve remains 3 GiB.

During development, run one existing Maddy or runtime module after rebuilding
the qualification image:

```sh
docker build --platform linux/amd64 --target qualification -t mail-admin:qualification .
.venv/bin/python tools/run_linux.py --suite maddy --module test_permissions_sessions
.venv/bin/python tools/run_linux.py --suite runtime --module test_bind_admission
```

Partial checks write distinct `build/SUITE-partial-UUID/MODULE/` reports. They do
not replace the complete suite or produce acceptance-catalogue passes.

`make browser` exports the compiled assets from the runtime image and starts a
synthetic loopback fixture with a fresh headless Chrome profile. It tests login,
logout, dialogs, settings, dark mode and 360/768/1280-pixel layouts. Override
`MAIL_ADMIN_CHROME` for another existing Chrome executable. This HTTP UI fixture
does not establish production HTTPS trust; the runtime suite tests actual TLS.

`make plan` validates only the specification and its verification tools. Passing
plan checks never establishes application correctness or production qualification.

Runtime evidence is recorded separately under `verification/`. Missing, failed,
skipped, stale or unimplemented acceptance cases block candidate qualification.
No live deployment is authorized by local tests or by a generated evidence file.

The frozen catalogue driver runs each registered assertion module in a fresh
192 MiB controller and retains its actual exit/OOM observations. It assembles
cases from the exact module results, with separately verified host reports where
required. Missing reports, lost controller outcomes or mismatched assertions block
assembly. Unmapped cases remain `not_run`.

The implemented API includes token-gated setup, TOTP/recovery/optional Email OTP,
opaque persistent sessions, recent authentication, password/factor replacement,
mailbox management, client configuration, DNS/Maddy diagnostics, encrypted relay
settings, revision-bound delivery confirmation, and optional provider credit reads.
Email OTP and permanent deletion default to disabled. Relay polling sends no mail.
No existing incoming mailbox password is recoverable or stored in plaintext.
The helper removes terminal journal records older than 90 days in bounded batches
under its maintenance lock. Unresolved operations remain until reconciliation.

`tools/run_tests.py` writes actual per-test outcomes and source identities to
`build/unit-checks.json` and `build/integration-checks.json`. It does not issue
catalogue or release passes. Skips and expected failures cannot become passes.
Reports identify Python, architecture, source digest, image and engine architecture;
local ARM64-host emulation is distinguishable from native amd64 execution.

After explicit authorization for a specific host, the fixture runner can be used:

```sh
.venv/bin/python tools/run_remote.py deployer@35.237.68.175
```

It transfers prebuilt images, checks spare capacity, runs bounded synthetic
containers, and records live-container identity/configuration/start-time digests.
It does not install host services, change live mail mounts, or reboot the host.
Fixture Docker access belongs only to the qualification controller. The web image
never receives the Docker socket. Remote directories and loaded fixture images
remain available for review; fixture containers and volumes are cleaned up.

Prepare a review bundle without approving or deploying it:

```sh
.venv/bin/python tools/prepare_release.py build/release-candidate --inputs verification/operator-inputs.json
python3 docs/mail-admin-implementation/tools/check_release.py build/release-candidate/evidence.json --phase candidate
```

The bundle contains an exact Linux/amd64 image archive, host and suite archives,
non-secret pending configuration, hashes, and evidence references. Its checker
must remain blocked until every required case has a real qualifying report.
Unspecified production inputs remain `null`; fixture budgets are not defaults.
The checker command above is an initial gap check: freshly prepared evidence has
no completed catalogue references and must remain blocked.

Run the catalogue adapter against a frozen bundle in the isolated Linux harness:

```sh
.venv/bin/python tools/run_linux.py --suite qualification --evidence build/release-candidate/evidence.json
# Replace UUID with the actual generated run identifier:
python3 docs/mail-admin-implementation/tools/check_release.py build/release-candidate/qualification-UUID-evidence.json --phase candidate
```

This runner verifies all four artifact hashes, the executing suite sources and
runtime image, then executes only reviewed case mappings. It writes exact case
arrays and a separate evidence copy under the original bundle. Unimplemented
cases stay `not_run`. All 111 cases have mappings, but missing matching runtime
evidence still blocks qualification. Check the generated sibling evidence file,
which contains the run's report references; the input evidence remains unchanged.
It never converts general unit-test counts into acceptance coverage. Target and
deployment observation adapters are delivered separately; their regressions do not
qualify production. See [qualification tooling](verification/qualification.md).

The operator-input file records the selected alert recipient, 03:00 Asia/Dhaka backup
calendar and rolling seven-day retention requirement. The operator supplied an R2
bucket separately; credentials remain outside the source tree. The approved pinned
Restic utility implements encrypted, deduplicated R2 snapshots and seven-day expiration.
A real R2 sharing/retention probe passed. Root recovery commands restore verified state
into a new directory and close restored authentication before SSH recovery. Full
replacement-host recovery and boot passed for an earlier prototype; the final
candidate still requires matching recovery evidence. Read the
[backup design](verification/backup-design.md). The production backup schedule is now
active; the [current continuation record](verification/mail-admin-completion.md)
supersedes historical deployment and recovery status in older handovers.

`tools/run_mock_host.py` manages a software-emulated Linux mock with its own kernel,
systemd, Docker daemon and disk. Build its separate tool image with
`docker build --platform linux/amd64 -f tools/vm/Dockerfile -t mail-admin:mock-host .`.
Its server-side container is unprivileged, memory/CPU bounded, loopback-only, and has
no host Docker socket or KVM device. VM reboots and power cuts must always target
the recorded mock identity. This is test tooling, absent from the web image.

For the larger local mock on an ARM64 Mac or amd64 Linux workstation, use
`tools/run_local_mock.py` with a tool image matching the native engine. See the commands in
[qualification tooling](verification/qualification.md#larger-local-mock). It retains
an amd64 guest. On native amd64 Linux with an accessible `/dev/kvm`, select
`--acceleration kvm` for hardware virtualization inside Docker. The default stays
software emulation. Both profiles retain the same CPU and memory bounds; the KVM
profile exposes only `/dev/kvm`, without a host disk or Docker-socket mount.
The driver refuses remote Docker endpoints and requires spare disk and memory.

Read [implementation status](verification/status.md), [operator handoff](verification/operator-handoff.md),
and [security release assessment](verification/maddy-security-assessment.md) before
considering maintenance. No commit, push or production deployment is implied.
