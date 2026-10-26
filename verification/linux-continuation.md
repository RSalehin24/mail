# Linux continuation checkpoint

Updated 26 October 2026 in `/home/rsalehin24/Documents/mail`.
This workstation runs CachyOS (Arch based), x86_64, with approximately 31 GiB RAM.
The [Ubuntu handover](ubuntu-handover.md) is a historical Mac transfer record.
Its candidate identities and results do not qualify this working tree.

## Latest outcome

Source `76307198` passed **111 / 111 catalogue cases**, with zero failures and
zero unrun cases. The candidate evidence checker passed on 26 October 2026.
The final evidence and remaining production work are recorded at the end of this
checkpoint; earlier sections preserve intermediate attempts and their limitations.

## Environment

The development environment is installed without replacing system Python:

| Component | Installed version and location |
| --- | --- |
| Python | CPython 3.11.17, managed by uv 0.13.5; environment `.venv/` |
| Node / npm | 24.22.1 / 11.19.1; `build/setup/node-v24.22.1-linux-x64/` |
| Python dependencies | All 30 hash-locked packages installed |
| Frontend dependencies | 102 packages installed using `npm ci` |
| Docker / Compose | 29.9.2 / 5.6.1; services active |
| Buildx | 0.38.1, installed under `~/.docker/cli-plugins/` |
| Restic | 0.19.1; `build/restic/0.19.1/restic-linux_amd64` |

Downloads were verified against pinned or official checksums. Receipts and setup
logs remain under `build/setup/`, including `verified-downloads.json` and
`buildx-receipt.json`. Dependencies and local downloads are ignored by Git and
must be recreated on another workstation; no dependency lock was changed.

Activate the environment in Bash:

```sh
. .venv/bin/activate
python --version
node --version
npm --version
make check plan
```

For Fish, use `source .venv/bin/activate.fish`.
The operator installed Docker and granted Docker-group access. This existing
agent process uses `newgrp docker`; a fresh login receives the group normally.
For a fresh CachyOS workstation, administrator setup is:

```sh
sudo pacman -Syu --needed docker docker-compose docker-buildx
sudo systemctl enable --now docker.service docker.socket
sudo usermod -aG docker "$USER"
```

Log out and back in before using Docker. These are CachyOS instructions, not
Ubuntu package commands or production-host deployment instructions.

## Corrections and completed checks

- Linux browser inputs now have a separate pinned manifest. Node and Chrome hashes,
  versions and paths are checked before execution. The macOS manifest is preserved,
  and reports are validated against the browser's observed platform.
- Browser fixture ownership and asynchronous secret-reveal timing were corrected.
- The owned local mock supports the native amd64 engine with the original memory,
  CPU, PID and isolation bounds. It receives no host devices or Docker socket.
- The approved patched Maddy fixture was rebuilt using pinned Linux Go/Zig inputs.
  [Its provenance](../tools/maddy-fixture/inputs-linux.json) records the static binary
  and image identities. Image export preserves the complete OCI identity.
- SQLite writers share the existing 250 ms acquisition budget. Concurrent budget
  reservations are serialized without increasing the deadline. Regression checks
  cover timeout, rollback and release after a connection-close failure.
- Inode-alert fixtures use a real inode pool on filesystems reporting no inode
  capacity. TLS recovery fixtures validate the issued DNS name using the actual CA
  root while connecting only to loopback.
- Cutover reboot validation samples its clock after generating the completion
  timestamp; the earlier ordering falsely rejected the new timestamp as future-dated.
- Documentation links, historical/current evidence distinctions, module-isolation
  instructions, evidence-checker paths and TLS rollback ordering were corrected.

The latest complete `make check plan` passed **465 unit tests and 72 integration
tests**, Ruff, strict mypy (86 files), frontend checks, specification models,
49 verifier regressions, nine shell examples and six Compose rejection examples.
Log: `build/setup/cutover-clock-checks.log`. The frontend production build also
passed. The document audit checked all 33 Markdown files and 41 shell examples;
`build/setup/documentation-audit-current.json` records its results.

## Frozen candidates and retained evidence

| Candidate | Recorded status |
| --- | --- |
| `build/release-candidate-linux-bc690da0/` | Complete catalogue: 93 passed, 2 failed, 16 not run, 111 required. Original failures are retained. |
| `build/release-candidate-linux-accb1679/` | Browser passed; actual guest T08 passed four assertions; T22 has partial host evidence. These reports retain this older source binding. |
| `build/release-candidate-linux-925cfe17/` | Browser passed; source for the supporting R2 retention result below. |
| `build/release-candidate-linux-cutover-clock/` | Browser passed; all 63 catalogue modules completed: **95 passed, 0 failed, 16 not run, 111 required**. Release remains blocked. |

The latest frozen source is
`31fdf6af21bf82f83dcdc75e64d4ddfa9bc716990b3a421f409fb8b1e3733fa9`.
Its artifact set is
`2c1949cd9422d59f1ed270f1ae295e9a9a0a553d2773fef40167cd68be3dc5f7`;
runner revision is
`069c991bc3d06558fa642a10d4d7e450273a7ecc3dec2cab901774e39daa9ff8`.
The specification digest is
`a9f65b7bdf3270702f68f0c6a86703989145707279083d617b35acfd93b254c2`.

Completed catalogue log: `build/setup/frozen-cutover-clock-catalogue.log`.
Raw reports are in its bundle under
`qualification-33baa3cb-2e13-4557-aa03-043cd5385c4c/`.
Original failed attempts, logs and source-specific reports remain immutable.
Historical Mac artifacts were not transferred; newly built Linux artifacts do not
reconstruct those original reports.

## R2 and replacement recovery

Private R2 credentials are present with restrictive permissions. Only the S3
credential subset is transferred to disposable guests. An independent synthetic
repository password is retained outside the source guest; it does not unlock an
existing repository. The production operator-input password reference remains
unset. Secret values must stay out of chat and reports.

The operator authorized fixture storage. Tests use a **2 GiB total-bucket cap** and
new `mail-admin-fixtures/UUID/` prefixes; existing objects are preserved.
The small encrypted upload/download/decryption test passed. The supporting real
Restic retention run also **passed**:

- Ten synthetic daily snapshots verified; three expired points removed.
- All seven retained points independently restored; the expired point unavailable.
- Initial added data: 8,393,588 bytes; unchanged second upload: 4,035 bytes.

Report: `build/restic-probe-404658c2-efd1-4549-972a-54de256410c4/result.json`.
It binds source `925cfe17` and synthetic files. It does not establish full
application or replacement-host recovery.

The source guest is instance `b2a039d9-874a-48b1-8dc6-e1c94f626417`; the separate
replacement is `db2f3c2f-fa8c-4fb3-8bc1-ea85367cce03`. Both use bounded owned
software-emulated amd64 mocks. The replacement booted with Docker active and
received matching bootstrap files while its Docker engine remained empty.

The source fixture was prepared with all eighteen state roles, actual Certbot
account/lineage/work state, the issued private-CA certificate, matching release
images and an independently recoverable repository key. Two capture attempts
failed at the original five-second Docker-stop/resume boundary. **Neither
published a trusted recovery manifest.** Logs:

- `build/setup/full-recovery-source-capture-module.log`
- `build/setup/full-recovery-source-capture-after-reconciliation.log`

Follow-up observation found both consumers running without OOM and serving the
expected certificate. Explicit reconciliation verified their actual identities
and served fingerprints before restoring fixture intents. A suitable native
disposable Ubuntu amd64 host was requested for the remaining host tests; deadlines
and resource limits were not raised.

Use a fresh source fixture with its lifecycle password set during the original
create. The current preparation added a password-change journal operation, whereas
the full-host boot fixture expects one completed synthetic create. Do not promote
this preparation into passing boot evidence.

Full host cutover, capture, replacement activation, post-recovery kernel boot and
matching acceptance reports remain incomplete. The earlier failed T16 batch is
preserved in `build/setup/frozen-accb-cutover-powercut-and-t16.log`.
The release remains blocked; these checks authorize no production deployment.

Both exact owned recovery mocks are now stopped, with disks and evidence retained.
Receipt: `build/setup/recovery-mocks-stopped.json`. No qualification controller
survived the completed catalogue.

## Docker KVM continuation

The operator selected Docker for the remaining work. `/dev/kvm` is available on
this workstation, so a native external test host is no longer a prerequisite.
The explicit `--acceleration kvm` profile runs a real amd64 guest inside Docker
using only that virtualization device. It retains the 1280 MiB controller,
1024 MiB guest, one-CPU and 64-task limits, with no host disk or Docker socket.
Default TCG fixtures still expose no device.

A KVM cutover attempt identified QEMU I/O thread exhaustion at the existing task
limit. All three guest disks now share an I/O context with a two-thread maximum.
Disk order, writeback caching and flush semantics are retained. Its failed logs
and original controller inspections are preserved; replacement controllers reuse
only their exact owned private volumes. The corrected source controller has
subsequently peaked at 16 tasks, below its unchanged limit of 64.
Cutover fault subtests now use fresh fixtures: a completed rollback had restored
the legacy timer before the next subtest reused its old paused-state assumption.

The latest source is `9aba9d4f2fd2c0bf1569df19a1865275baaa141c81932abd12ddb666067a56b7`,
in `build/release-candidate-linux-kvm-final/`, artifact set
`f3a61fdcb7210b428ab7798058f18edafe3c254d280fcf9d086fd4476ce56015`.
Runner revision: `eaf7bd1f760eade6cec5d9517859fd7403765a41bb13cbbc2096e61e70a424d7`.
It passed browser verification, all 467 unit and 72 integration tests, and the
remaining `make check plan` checks. Its native catalogue is running.

The new source guest is `7f8a95a0-0ff9-40ba-a554-064f8e265288`; the replacement is
`f6e2a2fe-f758-4a09-a83f-4e9c120a6a8f`. T08 lifecycle cases and host journal retention
passed at the latest frozen identity. The corrected cutover run is underway.
Kernel scenarios and the eighteen-role R2 replacement test remain pending.
The earlier `31fdf6af` 95/0/16 catalogue and failed emulation attempts remain
source-specific history, not acceptance results for this new candidate.

## Consolidated recovery candidate

The current frozen source is
`76307198695bcc6a040f692d37ed8dc8b9e8d87ad3b5cd270d3135fa1b7d002a`,
in `build/release-candidate-linux-docker-verified/`. Its artifact set is
`c28e9f264e129ed0fbb6747e38b184bec575294323866243a5f6a4501c436a1f`;
runner revision is `373662fa1e3e68aee89ae4cfbdc7cdce2ffb0a13a5d789dbff647a25c44a4a0b`.
All 472 unit and 72 integration tests, browser verification and `make check plan`
passed. Its matching catalogue and host observations are running.

Additional fixes separate Restic JSON stdout from diagnostics under the existing
combined 64 KiB output cap, retain the subprocess collector's legacy crash-hook
interface, and retry only real SQLite busy/locked errors within the crash
observer's existing deadline. Full-capture publication is observed directly after
repository verification and the real manifest write, rather than inferred from
coarse filesystem mtimes. Regression checks reject premature or duplicate writes.

The earlier `e6fd0a4a` capture uploaded and independently verified its full R2 point,
then its qualification guard rejected the filesystem-mtime ordering observation.
Its manifest had already been published; no passing source-capture observation
was created. That failure remains in
`build/setup/docker-recovery-full-source-capture.log`. Its completed catalogue
records 93 passed, 2 failed and 16 not run; both failed cases depended on the old
collector hook signature. These older reports cannot qualify the new source.
Closed private cutover fixtures were archived outside the guest to retain their
contents and recover guest disk space; the exact archive inventory and digest are
in `build/setup/kvm-closed-cutover-fixtures.json`.

## 26 October: resumed Docker replacement recovery

The latest frozen candidate is `build/release-candidate-linux-docker-verified/`,
source `76307198695bcc6a040f692d37ed8dc8b9e8d87ad3b5cd270d3135fa1b7d002a`,
artifact set `c28e9f264e129ed0fbb6747e38b184bec575294323866243a5f6a4501c436a1f`.
Its native catalogue completed at **95 passed / 0 failed / 16 not run / 111**.
The supporting local checks passed **472 unit and 72 integration tests**.
Matching source-host observations passed the cutover, TLS reboot, offline reboot
and helper checks; these must still be assembled with replacement evidence.

Full-state R2 source capture passed after the capture-observation correction.
The replacement downloaded and verified the snapshot and loaded the saved release
image. Its controller stopped during authentication preparation. On 26 October,
the same owned Docker controller was restarted, and preparation reconciled from
the retained successful utility exit. Private terminal credential recovery and
fresh-target installation then passed. Logs use the `resumed-` prefix under
`build/setup/`; interrupted attempts and their observations remain preserved.

The source controller remains stopped. The interruption exceeded the four-hour
RTO budget; completing this attempt can diagnose recovery behavior but cannot
establish a passing recovery time. A fresh timed recovery remains required,
as do final evidence assembly and the candidate gate. No production deployment
is qualified by these results.

## Final candidate evidence: 26 October 2026

The same frozen source and artifacts above now have a complete assembled result:
**111 passed / 0 failed / 0 not run / 111**. Original native module reports and
controller receipts were retained unchanged, validated again against the same
source, artifacts, images, engine and resource limits, then assembled with matching
host reports. The original 95/0/16 result remains unchanged in its own directory.

- Final evidence: `build/release-candidate-linux-docker-verified/qualification-bef1aa31-5826-4f32-927a-b76de102cc7b-evidence.json`.
- Catalogue result: `build/release-candidate-linux-docker-verified/qualification-bef1aa31-5826-4f32-927a-b76de102cc7b/result.json`.
- Candidate checker: `build/setup/consolidated-candidate-check.json`, result `pass`.
- Assembly log: `build/setup/consolidated-catalogue.log`.
- Fresh replacement pipeline: `build/setup/fresh-timed-recovery-pipeline.log`.

Fresh empty replacement instance `984f4744-f126-435b-90af-c0ec0fc28d15` restored a
new immutable full-state R2 snapshot using the independently retained workstation
password and credentials. The source remained stopped from 11:38:34 UTC through
the post-reboot host checks at 11:40:06 UTC. All three T18 host assertions passed,
including the selected 26-hour RPO and four-hour RTO. Captured mail was present,
post-cutoff mail absent, old authentication rejected, fresh authentication accepted,
and restored helper, relay keys, TLS, ACME state and services verified after an
actual new-kernel boot. The earlier interrupted attempt remains a recorded RTO
failure; it was not reused as a passing timing observation.

The fixture preparation now preserves qualified systemd unit mode 0644 rather
than inheriting its private capture umask. Replacement bootstrap also installs the
static journal namespace configuration from the verified restored host bundle.
The installed-source gate subsequently passed without excluding either check.
Bootstrap actions and retained failed attempts are recorded under `build/setup/`.

All 33 Markdown documents and 42 shell examples pass the local document audit.
This is candidate qualification with synthetic fixtures. Production operator inputs,
preflight, maintenance scheduling, target acceptance and final approval remain
outstanding. Follow the documented rollout for those stages; no production system
was deployed or changed during this continuation. Private `build/` and `local-state/`
artifacts are ignored by Git and require protected retention for another workstation.

A read-only production preflight check remains blocked at
`backup_key_reference`, which is intentionally unset in the production inputs.
The independent synthetic qualification password does not fill that production
reference. The recovered fresh guest remains running for inspection; the source
and interrupted replacement are stopped with their private disks retained.
