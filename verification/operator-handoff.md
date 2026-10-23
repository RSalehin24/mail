# Operator handoff

The application and synthetic test harness are reviewable. This is not an approval
to deploy. Use the authoritative [G01–G08 rollout](../docs/mail-admin-implementation/operations/rollout.md).
Do not combine its phases into an unattended install command.

## Inputs still required

- Qualified R2 repository configuration, separately recoverable encryption key,
  local staging retention and deletion cutoff age. The operator selected
  03:00 Asia/Dhaka, rolling seven-day deduplication, a 26-hour maximum verified-backup
  age and a four-hour recovery target. Replacement-host verification must establish
  those selected objectives before deployment.
- Independent alert provider/credential reference and an external monitor. The operator
  selected `rsalehin24@gmail.com` for email alerts; no provider credentials are configured.
- Approved maintenance/reboot window, interruption allowance and rollback snapshot IDs.
- Reviewed recipient dependencies, protected recipients, configuration hashes and expiry.
- Available service UID/GID and measured production memory/hash/HTTP/tmpfs/disk/inode budgets.
- Measured `transient_resources` memory/tmpfs/CPU limits for ACME, archive and offline
  image utilities; pending values block preflight. Their temporary filesystems share
  the container memory limit, swap cannot exceed it, and logs rotate at two 5 MB files.
- Selected Maddy digest and accepted security-release disposition. Preserve Cloudflare MX.
- Authenticated target project/zone/instance and trusted SSH host-key blob digest.

These cannot be inferred from SSH access or the host's current free memory. The
release preparer writes pending values explicitly. Keep Email OTP and deletion off.

The operator selected an existing Cloudflare R2 bucket and supplied its S3 credentials
through an ignored private local file. A 340-byte encrypted synthetic archive was
uploaded, downloaded, integrity checked, decrypted and extracted successfully; this
is provider-transport evidence only. The operator subsequently approved Restic; its
pinned local utility and R2 repository implementation are delivered. A separate real
R2 probe proved deduplication and seven-day retention, restoring all seven retained
points after pruning. Full replacement-host boot recovery remains unqualified.
No backup utility or service is installed on the live server. See the
[selected backup behavior and recovery design](backup-design.md). No additional
server or paid resource is authorized. Confirm actual retained storage, temporary
prune headroom, requests and available account allowance before enabling backups.

## Root commands delivered

Host Python modules use only the standard library. Deduplicated R2 backups invoke
the separately approved pinned Restic binary included in the host bundle. Application
authentication utilities reuse the pinned isolated runtime image; install no Python
packages on the host.

| Entry point | Purpose and gate |
| --- | --- |
| `python3 -m host.render DEPLOYMENT OUTPUT` | Render reviewed group/calendar inputs; install nothing. |
| `python3 -m host.cli preflight` | Check qualified artifacts, inputs and effective configuration. |
| `python3 -m host.cli maintenance-entry` | Requires G02 and explicit artifact/target/time-bound approval; record/pause/drain legacy renewal. |
| `python3 -m host.cli legacy-reconcile` | Requires G02 and explicit `legacy-reconciliation` approval; resolve a previously observed manual legacy writer only after no writers remain and the current disk pair matches served TLS. |
| `python3 -m host.cli baseline-backup` | Requires G03; capture original deployed state before hardening/launcher installation. |
| `python3 -m host.cli tls-cutover` | Requires G05, complete preflight and explicit cutover approval; preserve a matched package, switch mounts/renewal and verify before scheduling. |
| `python3 -m host.cli rollback` | Requires G05, complete preflight and explicit rollback approval; restore the matching same-schema set and verify served TLS before restoring the original timer state. |
| `python3 -m host.cli renew` | Requires G05 and recorded coordinated cutover; acquire/stage/publish/activate. |
| `python3 -m host.cli dry-run` | Same coordination, Certbot dry run; no forced issuance. |
| `python3 -m host.cli activate` | Retry a published generation without requesting a new certificate. |
| `python3 -m host.cli backup` | Requires G07; quiesced capture, encrypted transfer/verification and bounded staging retention. |
| `python3 -m host.cli backup-verify` | Recheck the latest encrypted remote archive; refresh availability only after integrity verification. |
| `python3 -m host.cli monitor` | Capacity/backup/certificate alerts independent of web availability. |
| `python3 -m host.cli monitor-external` | Install/run on the separately configured monitor, not solely on the mail host. |
| `python3 -m host.cli recover` | Explicit recovery approval; persistent stopped intent, auth snapshot and private terminal utility. |
| `python3 -m host.cli recover-resume` | Explicit recovery approval; verify the retained utility's actual successful exit and database, then activate and verify the dashboard. |
| `python3 -m host.backup_cli PROFILE ...` | Independent repository initialization/listing, verified staging restore, closed authentication preparation, private-terminal restored credential recovery and interrupted-command reconciliation; see the recovery design. |
| `python -m backend.operator ...` | Root one-off image utility for provision/bootstrap/recover/snapshot/probe/restore-prepare/encrypt/decrypt/extract. |

Provision root-owned records/directories and stable `0600` lock files deliberately.
Initialize `/var/lib/mail-admin-backup/retention.json` to `{}` as root-owned `0600`
during deliberate first provisioning; the stable `retention.lock` must also exist.
Do not reset a missing index on an existing installation without reconciliation.
The helper socket parent remains the same `0750` inode across restarts. Rendered
systemd/tmpfiles/journald units are under `host/systemd/`. Root deployment and
approval records are never web-writable. Bind sources use `create_host_path: false`.

## Checkpoints and interruption recovery

`/var/lib/mail-admin-rollout/ARTIFACT_SET_SHA256/checkpoints.json` references root-protected report
paths/hashes under `reports/`. Gates require exact passing cases, artifact/spec/suite
identities, target binding and prerequisite chronology. Hand-edited green reports
do not establish qualification. The preflight entry point also runs the original
release checker against the protected evidence bundle.

Each qualified artifact set has its own private directory, `legacy.json`,
`baseline-manifest.json`, `cutover.json`, `cutover.lock`, reports and rollback set.
The root `active-release.json` binds the selected release and target. A shared
`operation.lock` serializes maintenance entry, baseline capture/reconciliation and
cutover/rollback across releases. A later release cannot bypass an unresolved prior
phase or manual writer. A completed cutover must also have verified G08 acceptance;
a verified `rolled_back` outcome permits a new release without that forward gate.
Old records/packages remain intact. Do not delete state to retry an attempt. Existing
unnamespaced rollout files require explicit operator reconciliation and block selection.
Missing/broken active-release pointers with existing lifecycle state likewise require
reconciliation; a new artifact identity cannot turn those records into a fresh start.
The maintenance entry command provisions the selected release's stable cutover lock.

The runtime marker, keys and database are explicitly provisioned and matched;
missing state never creates a fresh installation. Bootstrap is created through a
private terminal immediately before G07 startup. Leave web/helper checks outside
held deployment locks. Do not enable legacy renewal against generation mounts.

Unknown helper/Certbot completion blocks conflicting maintenance. Keep durable
consumer maintenance intent through recovery, inspect actual inventory/journal
state, and resolve with the operator. Do not replay an old mutation automatically.
`activate` respects stopped/never-deployed consumers; a missing deployed consumer
remains pending. Backups verify recovery before releasing maintenance locks.

A manual legacy renewal observed during draining leaves a durable
`manual_completion: unknown` record. Process disappearance and an older successful
systemd status cannot clear it, including after a root-tool restart. The explicit
`legacy-reconcile` action validates the current trusted certificate/key pair and
served 993 fingerprint while the timer stays disabled and no writer remains. It
records the verified current state and preserves the original exit code as unknown;
it does not claim to have recovered that process's historical exit status. A failed
validation leaves maintenance blocked. This path still needs full host qualification.

For loss of the original host, `host.backup_cli PROFILE recover DESTINATION` works
from verified staged state using its recorded image, without the old running
dashboard or its helper. Its successful completion leaves activation closed.
Interactive bootstrap/recovery utilities use Docker's `none` log driver; terminal
seeds and recovery codes must not be persisted by Docker logging.

The policy `/etc/mail-admin/secrets/runtime-policy.json` matches the installation
marker and is root-owned, service-group-readable `0640`. Bind the secrets directory
so atomic policy replacements remain visible. Recovery closes this policy before
stopping the dashboard and disables its automatic container restart. A separate
stable recovery lease excludes concurrent SSH recovery commands while the two host
maintenance locks are released for the private terminal utility. A lost terminal
does not authorize restart: inspect the named retained utility and use the explicit
resume command. Resume verifies actual exit zero and current authentication state
before reopening the policy; served TLS and the active generation must pass before
the durable outcome says `resumed`.

Certbot creation persists its unique name before Docker is invoked. Reconciliation
finds and verifies that exact container after a lost identifier write. A container
that never started can be removed without running it; a surviving writer keeps
backup/cutover blocked until its actual exit is observed. The following acquisition
removes only the verified previous completed container, keeping container/log growth
bounded. The synthetic Docker writer tests do not prove HTTP-01 or actual ACME renewal.

Backup alerts check the archive's original recovery cutoff and the recent integrity
observation independently. Rechecking old encrypted bytes cannot refresh their RPO.

Archive transfer alone leaves `restore_passed: false`. A real isolated restore of
that archive, measured against the recorded objectives, is required before trusted
deletion coverage can be approved. The synthetic peer-directory test does not prove
the selected off-VM provider, credentials or archive-key recovery.

Preserve the full matched baseline image/schema/helper/protocol/launcher/TLS/unit
set for rollback. Restore only when necessary, account for mail newer than cutoff,
invalidate restored authentication/readiness, and never overwrite newer mail blindly.

The cutover configuration must name exactly four `files`: `maddy_compose`,
`renewal_script`, `renewal_service` and `renewal_timer`. Each records the reviewed
absolute source/target, source SHA-256 and installed mode. `support_files` must
cover all installed host/helper/shared source, the launcher and helper unit.
Pending values block use. Rendered output includes the generation-aware legacy-path
wrapper `renew-cert.sh`; keep both script and units bound to the qualified bundle.

The Maddy runtime directory must use a bounded temporary filesystem. An unclean
shutdown can leave its update-pipe socket in the container's writable layer; a later
daemon can serve IMAP while CLI update notifications fail. The isolated fixture now
mounts `/run/maddy` as a root-only 1 MiB tmpfs within its unchanged 96 MiB container
cap and verifies actual mail/CLI access after SIGKILL and restart. That fixture value
is not a production budget: record the measured target limit in the reviewed Maddy
Compose file and ensure its configured `runtime_dir` matches the temporary mount.
Do not silently ignore update-pipe errors or delete socket files beneath a running
daemon. The original failing container and diagnostic output remain preserved.

`/var/lib/mail-admin-rollout/ARTIFACT_SET_SHA256/rollback-set/` contains protected copies of the original
files, an exported archive of the actual pinned images, compatibility/support-file
identities and the prior TLS selection. Its manifest digest is persisted before any
host file is replaced. The same release directory's `cutover.json` and its stable
`cutover.lock` prevent automatic replay or concurrent forward/rollback commands.
After interruption, inspect the recorded phase and use explicitly approved rollback;
do not delete the record to retry. Unknown writers, corrupt packages, support-file
drift, incompatible schemas or failed TLS verification keep scheduling paused.

This executor supports a compatible same-schema rollback and never replaces mailbox
or dashboard data. A later dashboard is persistently stopped when returning to a
Maddy-only pairing; its database and referenced generation are preserved. Recovery
and dashboard registration cannot reintroduce generation mounts after legacy rollback.
Database restoration or an incompatible migration requires the separately reviewed
recovery procedure. Unit/component checks do not establish full host cutover or reboot
qualification; those isolated cases are still required.

The synthetic recovery procedure copies back a missing body when its reference remains.
When both the body and its reference are gone, the new fixture invokes the native
`imap-msgs add` command with the selected recovered message on stdin, then checks the
new UID and newer mail in both selected and unrelated accounts. Its latest result is
recorded in the implementation status; it does not authorize a live import.

For an approved selected-message recovery, export the exact message from the verified
isolated archive, record its SHA-256, folder, original flags/date and cutoff, and inspect
the current mailbox before importing. The tested native interface is
`imap-msgs add [--flag FLAG] [--date ISO8601] USERNAME MAILBOX`; supply the message on
stdin through the umask launcher. It assigns a new UID. Never replay an interrupted
import automatically: verify its returned UID/body and newer mail first. Keep the helper
reconciliation warning until the operator verifies the complete result. Whole-store
replacement would overwrite newer mail and needs a separate recovery decision.

## Independent email alerts

The host and external monitor can use `provider: brevo_smtp`, the selected recipient,
port 587 or 465, and a root-owned `credential_reference` to a `0600` JSON record containing
`username` and `credential`. These credentials are independent of the dashboard database.
The shared transport validates TLS and uses the fixed sender and recipient. A configured
HTTPS webhook remains available as an alternative provider. No provider has been selected
or provisioned in production, and no alert email has been sent by the implementation run.

On the separate monitoring host, provision `/etc/mail-admin/external-monitor.json`
with its `alert_configuration`, plus root-owned `external.json` initialized to `{}` and
an empty `external.lock`, both `0600`, under `/var/lib/mail-admin-alerts/`. Install the
external-monitor service/timer only there. It needs no Maddy data, helper journal,
dashboard database or generation record. Test actual failure/recovery delivery before
enabling it; a passing SMTP mock is not receipt evidence.

The independent external probes validate each actual served certificate on 993/443
and evaluate its 21-day and 7-day expiry thresholds without the local activation
record. A valid but expiring pair is not reported as healthy reachability.

Password reset does not disconnect established IMAP sessions. An operator-approved
Maddy restart disconnects all clients and interrupts delivery. Use the same maintenance
locks, existing image/mounts and served-TLS verification; storage deletion is not a
session-revocation procedure. No automatic restart action is exposed by the web API.

## Current deployment boundary

Remote fixture testing was explicitly authorized on the existing server through
`deployer`. This authorization covers the isolated mock, not live rollout, host
reboot, DNS changes, production credential resets, mailbox deletion or external mail.
G01 remains blocked until all isolated catalogue cases are implemented and qualified.
G02–G08 remain blocked until their prerequisites and operator inputs are complete.

The helper unit is enabled for both `multi-user.target` and `docker.service`.
Run `systemctl enable mail-admin-helper.service` after installing an updated unit,
including when it was already enabled, so the Docker wants link is created.
`Requires=` and `After=` still prevent premature helper startup. The additional
installation target lets a later Docker start request the helper again after an
earlier Docker startup failure. Verify this on the isolated host before release;
enabling a unit alone does not establish successful boot recovery.
