# R2 backup and replacement-server recovery

Status: the operator approved Restic for local implementation and isolated R2 tests.
Restic 0.19.1 is pinned by executable/download checksums and included in the host
release bundle and qualification image. It is absent from the dashboard web image.
No backup utility, repository, service or timer has been installed on the live server.

The operator selected a maximum verified-backup age of **26 hours** and a complete
recovery target of **4 hours**. These are qualification/release inputs, not measured
replacement-host results. The daily 03:00 Asia/Dhaka schedule includes time for
capture, upload and verification before the age threshold. Rechecking an old
snapshot never refreshes its recovery cutoff. Full replacement-host activation must
still be measured against the selected four-hour target.

## Selected behavior

- Run daily at **03:00 Asia/Dhaka**: `*-*-* 03:00:00 Asia/Dhaka` (21:00 UTC on
  the preceding date). A read-only systemd calendar observation is recorded in
  `build/backup-calendar-observation.log`.
- Use one dedicated Restic repository prefix in the operator's R2 bucket. The first
  successful run stores all required state. Later runs share unchanged blocks and
  add changed data plus snapshot metadata. They never contain earlier archives.
- After a verified successful backup, expire recovery points at or before the actual
  current clock minus seven days, then prune unreferenced blocks and check the
  repository. Failed capture/upload/check does not expire the last verified point.
  Missed runs reduce the available recovery history; seven successful days spread
  over several weeks are not treated as the last seven calendar days.
- Seven days describes **recovery history**, not the age of retained messages. A
  message received months ago and still in today's mailbox must remain recoverable.
  Its one shared stored copy can serve all seven daily points. If it is deleted,
  its data can be reclaimed after the last recovery point containing it expires.
- No extra weekly/monthly tiers are selected. A rollback baseline stays explicitly
  protected during an unfinished rollout; normal seven-day expiration applies once
  the trusted G08 acceptance checkpoint releases that protection.

## Capture, encryption and retention

The existing capture code excludes unresolved helper/ACME writers, acquires renewal
before Maddy maintenance, records inventory and stops the running writers. It copies
consistent databases, messages and configuration, then resumes and verifies those
services before remote transfer. Sources containing staging or Restic state/cache
are refused. No source may be an earlier backup archive directory.

Restic processes native staged files, compresses and encrypts shared chunks, and
publishes an exact repository/snapshot identity. A verification restore checks
content hashes, owners, modes, directories and safe relative symlinks before the
trusted manifest is published. Availability rechecks preserve the original recovery
cutoff. The older SFTP archive path retains compressed TAR plus AES-GCM compatibility;
the selected R2 service uses Restic rather than a newly encrypted TAR every day.

Retention groups by installation/host and tag, independently of staging UUID paths.
It selects exact expired IDs using the current clock, checks repository data before
forgetting, prunes with bounded repacking, and checks again afterward. R2 object-age
expiration must remain disabled for repository objects: an older shared chunk can
still be required by today's point. The operator reported the default rule to abort
incomplete multipart uploads after seven days, with no completed-object deletion
shown. This is compatible with the design. Both attempted read-only lifecycle API
inspections returned HTTP 403; bucket-level settings were not independently verified.

Explicit file, whole-bucket, staging and process budgets must be selected and measured
before enabling the live service. The service template requires whole-job memory/CPU
limits; each Restic command has a deadline, one Go processor, bounded output, two S3
connections, a private temporary cache and a durable command record. Completed command
records are bounded. Unknown completion blocks conflicting commands until an operator
observes no surviving process and explicitly reconciles the expected repository with
normal unlock and a full data check. It never replays the interrupted command or
invents its exit status. Failed/unpublished staging requires operator reconciliation;
capacity, stale backup and failure alerts must be operational before scheduling.

The runner requests quiet JSON output so routine progress cannot exhaust the existing
64 KiB subprocess-output bound during a complete server upload. Completion summaries
remain mandatory; deadlines, output limits and interrupted-command guards remain in
force. A larger 18-role fixture first hit that output bound after consistent capture
and service recovery. Its original upload remains unverified, and explicit identity,
survivor, unlock and full-read reconciliation passed before a fresh capture was started.
This continuation does not turn the interrupted command into a successful upload.

The Restic Go memory target is now 96 MiB, shared by provider and local-repository
execution. An instrumented fixture observed the largest peak during repository key
initialization; the previous 128 MiB target left insufficient coordinator headroom
in two emulated 192 MiB controller runs. This target is
[soft](https://go.dev/doc/gc-guide#Memory_limit), not a process or whole-job hard cap;
the selected systemd/cgroup limit still enforces the complete job. The pinned Restic
binary and its [key calibration](https://github.com/restic/restic/blob/v0.19.1/internal/repository/key.go)
are unchanged. At frozen source `03641e33`, the full 22-check Maddy suite and real R2
mail/dashboard recovery passed with this target. The provider fixture completed
private-terminal credential recovery and actual restored HTTPS login in 90.66 seconds
(`build/r2-application-80787e40-dd84-4f27-86ec-2138ac0c6b59/result.json`). This is
partial-state application recovery, not replacement-host boot or an approved RTO.

A separate real R2 check at the same frozen source ran inside the software-emulated
guest. Ten controlled daily points shared the original 8,393,288-byte upload; the
unchanged second point added 3,730 bytes. Expiration removed three old points and
all seven retained points restored, including each day's changed content and the
continued absence of an earlier removed file. The original ten-minute fixture batch
timed out after nine verified publications, leaving one command unresolved. Normal
identity verification, unlock and a full data check reconciled that command while
preserving its historical exit as unknown. The subsequent retention/restore check
passed separately; it does not turn the timed-out batch into a pass. Records are in
`build/mail-admin-local-mock-8a8f1170-b11c-4adf-8626-537e42976ea3/`:
`restic-interruption-reconciliation.json` and
`restic-retention-after-reconciliation.json`. These are controlled timestamps, not
ten elapsed production days or complete replacement-host recovery.

The R2 free allowance is not a hard bucket cap. Local budget checks do not control
other clients, other buckets, account-wide requests or provider billing. Measure
retained bytes and temporary prune headroom; no paid resource has been created.

## Recovery kit and root commands

A historical prototype full 18-role R2 point was restored onto a different empty
software-emulated guest while the source remained powered off and its disk was
never attached. The restored dashboard passed new password/TOTP login and rejected
the old credentials/cookie/factors; relay-key decryption, saved mailbox metadata,
native inventories, the original message and restrictive permissions were verified.
After a failed first reboot and recorded helper/mock-startup corrections, a second
kernel reboot and separate application observation passed. The measured backup age
at outage was 22 minutes 22 seconds; outage through verified recovery was 2 hours
3 minutes 14 seconds, including development repairs. These meet the selected
26-hour/4-hour synthetic targets. They do not establish a production recovery time
or qualify the old archived host bundle without its subsequent helper-unit fix.
They also do not qualify the later `a70f7897` bundle or this modified Linux checkout;
matching frozen replacement evidence remains missing. Referenced private `build/`
reports require transfer from the original workstation.
The original 15-second emulated startup failures remain failed; native resource
qualification is separate. Exact non-secret observations are retained under
`build/mail-admin-local-mock-115167ae-192e-4b01-b32b-2950c8a1ad45/replacement-recovery-proof-c8b631f4-6d51-410e-ada6-42cdc043a53d/`.

Keep these outside the mail server, in a password manager or protected offline copy:
repository address and ID, installation ID, the Restic password, narrowly scoped R2
S3 access (or the means to issue replacement access), and the qualified host bundle
with its checksum and Linux/amd64 Restic checksum. Losing the password loses access
to the encrypted data. Never keep the only password inside its own repository.
Application encryption/verifier keys remain inside the encrypted recovery point.

Rollout baselines and rollback packages are retained per qualified artifact set under
`/var/lib/mail-admin-rollout/ARTIFACT_SET_SHA256/`. Scheduled retention reads that
release's baseline manifest and checks the contract's final G08 acceptance before
releasing baseline protection. A newer release cannot overwrite an earlier manifest
or use a new artifact identity to bypass an unresolved rollout.

The private operator profile contains non-secret repository configuration and paths
to root-owned `0600` credential/password files. Production R2 credentials should
contain only `access_key_id` and `secret_access_key`; the broader Cloudflare API token
supplied locally is not needed. Keep backup credentials outside the web-mounted
`/etc/mail-admin/secrets` directory. For example, use
`/etc/mail-admin/backup-r2-credentials.json` and
`/etc/mail-admin/backup-restic-password.key`. Protect all parent directories.

On a compatible Linux/amd64 recovery target, use the qualified standard-library host
bundle. Provision a private profile and its two external secrets first:

```sh
sudo python3 -m host.backup_cli /root/mail-recovery/profile.json list
sudo python3 -m host.backup_cli /root/mail-recovery/profile.json restore EXACT_64_CHARACTER_SNAPSHOT_ID /root/mail-recovery/selected
sudo python3 -m host.backup_cli /root/mail-recovery/profile.json prepare /root/mail-recovery/selected
sudo python3 -m host.backup_cli /root/mail-recovery/profile.json recover /root/mail-recovery/selected
```

`initialize` is an explicit separate action for a **new empty repository**, never a
step for recovering an existing repository. Preserve its returned repository ID in
the external kit. `reconcile` is an explicit interrupted-command recovery action.
Preserve the original `staging_directory` value in the recovery profile: it identifies
the paths stored inside snapshots, even when the new restore destination is elsewhere.
Update executable, password and command-state paths for the recovery host as needed.
The CLI refuses an existing destination, foreign snapshot or unsafe parent, verifies
the native restore and SQLite integrity, and records missing full-state roles.
When dashboard/operator state is present it automatically pauses the restored runtime
policy. It starts no mail/dashboard service and overwrites no installed data.

Before `prepare`, load and verify the exact saved application image specified by the
restored `operator/deployment.json`. `prepare` runs its root `restore-prepare` utility
with only restored data/key/marker mounts, no network, published ports, helper or
Docker access. The utility rejects incompatible database/marker/keys, invalidates
sessions, challenges, OTPs and prior recovery codes, preserves TOTP replay history,
clears outbound readiness and leaves recovery required. Preparation persists a unique retained container identity before Docker creation.
After lost completion, repeating `prepare` only observes that exact utility and
requires its real exit zero; it never automatically executes another copy. A missing,
running, never-started or failed utility keeps access closed for operator reconciliation.
`recover` requires a private SSH terminal and uses that same recorded image and
restored mounts to establish the intended fresh password and verified TOTP seed.
Save its five replacement recovery codes privately. Docker logging is disabled for
interactive authentication utilities so the seed/codes do not enter container logs.
The recovery lease and retained utility identity prevent concurrent or repeated
execution: after lost completion, a retry only observes the original utility's
actual exit and authentication probe. Missing/running/failed completion keeps
access closed. A successful result records
`credentials_recovered_requires_activation`; it does not start a service or reopen
the runtime policy. Reconcile and qualify target activation separately. Never run
fresh provisioning/bootstrap over restored state.

Full-state capture must include all 18 roles enforced by `host/backup.py`:
mail databases/messages; dashboard data; operator marker/deployment/matching keys;
helper journal/policy; certificate activation state; host tools; units; tmpfiles;
Docker boot ordering; launcher; independent alert configuration; Maddy configuration;
Maddy Compose; TLS generations; ACME state; ACME library state; renewal script; and
release artifacts. `release_artifacts` must contain the actual matching Maddy and
application image archives, qualified host bundle and version/checksum manifest.
An image ID text file alone cannot rebuild a lost server. The first pre-dashboard
baseline has a deliberately smaller existing-state scope and is reported as such.

Before installation/activation on a replacement host, map those roles to the reviewed
paths, verify versions and owners/modes, reconcile the helper against real mail
inventory without replaying mutations, and prove account/message/authentication/TLS
and boot behavior in isolation. Fresh target identity, networking and certificate
checks are required; restored old evidence does not qualify a new server. Full
systemd/reboot/crash recovery and service/DNS cutover remain unqualified. Any change
to the live server requires separate operator approval.

## Recorded checks

- At frozen source `4c55859a`, the larger software-emulated fixture captured all
  **18 required roles**, observed both real mail/dashboard writers stopped before
  and after copying, resumed services, uploaded to R2, verified the off-VM copy and
  restored all roles into separate private staging. Native integrity/ownership
  verification and automatic closure of restored authentication passed. Total:
  **460.42 seconds**, with 120,674,483 packed bytes added. Actual report:
  `build/mail-admin-local-mock-8a8f1170-b11c-4adf-8626-537e42976ea3/full-recovery-proof-4c55859a/result.json`;
  log: `build/local-mock-full-state-measured-budget-4c55859a.log`.
  Matching Maddy/dashboard image archives and the current host bundle were included.
  TLS is the actual manually issued fixture pair; ACME was never acquired in this
  fixture. Its 600-second command budget is separate from pending production limits.
  The previous inventory/output-limit/120-second failures remain failed records.
  Matched-image authentication preparation also passed and leaves services stopped
  pending private-terminal recovery. Replacement-host activation, reboot and an
  approved RTO remain unqualified.

- Source `5f83f7ec0e9d8ae08819d0af6cc32f690edc087fec6cb97234cddb0799c05317`
  passed real R2 mail/dashboard recovery in 107.46 seconds, including the private
  terminal and actual HTTPS login from restored state:
  `build/r2-application-781af9a6-2543-4dbb-85f9-396dbf9f3a02/result.json`.
  Its full native run passed 249 unit, 33 integration, 22 real-Maddy and nine HTTPS
  checks; live Maddy was unchanged. Reports:
  `build/mail-admin-qualification-c6607c94-6438-44d7-879d-a47a33670ae8/`.
  These checks precede the later rollout namespace changes. The provider fixture
  explicitly reports missing full-host roles and does not establish full boot or RTO.
- Earlier source `f6faf7234299762437c43f8c38c5c501b7ea09179a230ad7ffcc8778d96fd249`
  passed real R2 mail/dashboard recovery in 106.47 seconds, including the real
  private-terminal credential procedure and non-root HTTPS startup from restored
  state with fresh password/TOTP login:
  `build/r2-application-0f243157-b904-401c-ace7-77887a5d6b96/result.json`.
  It reports missing full-host roles and does not qualify replacement-host boot
  or an approved RTO. The independent R2 retention probe at the same source retained
  seven of ten controlled daily points and restored all seven after pruning:
  `build/restic-probe-61a232a2-b9df-4f59-8f6f-f46817953d01/result.json`.
  First/unchanged-second additions were 8,394,489 / 5,117 bytes.
- The earlier R2 restored-HTTPS attempt at source `5ec923d6` failed because its
  private umask removed fixture socket group traversal. Explicit provisioning modes
  and a regression fixed it. The original failed result remains under
  `build/r2-application-422a1664-0883-483c-8596-5115f27bcea1/`.
- Real R2 encrypted archive transport passed at source
  `8e600cd72bc42295752d09212e0c71a0627232f11360b5206d33dbe47a61f7bb`:
  `build/r2-probe-364062c7-8588-4eaf-a605-253f02cb7c39/result.json`.
- The first Restic R2 probe failed because the CLI requires a space-separated UTC
  `--time`, not RFC3339. The failure remains in
  `build/restic-probe-d54c7967-c552-44ec-9b0d-f7e0fa167a3f/`. The fix has a regression test.
- The later real R2 Restic probe passed at source
  `cd96b8a0537e3960244128fce9cf552da1afcade5b7e01ccc823025f69c73a4a`:
  `build/restic-probe-b67f6a7b-ca6f-4977-b21f-4d0592996ff3/result.json`.
  Ten controlled daily snapshots expired three and retained seven. Every retained
  point restored after pruning. The first added 8,394,487 bytes; the unchanged second
  added 5,110 bytes. This proves sharing and calendar retention for synthetic files,
  not full host recovery. Each fixture password/profile remains private locally.
- Source `e1652137d18ed6b2d604ce61ec6d06d6814bbd052b021ad37637f6bd03a3d1af`
  passed 203 unit, 26 integration and 20 emulated Linux/amd64 Maddy checks, including
  real mail/enrolled dashboard recovery through a local encrypted Restic repository.
  Subsequent restore-safety changes require their own checks; see
  [current implementation status](status.md) for the final snapshot.

- The earlier frozen source
  `00fbc5e55690303c049a71b7d6a9a515abb3d19eb515bccb2d3682c40a15635e`
  passed macOS and emulated Linux 215 unit / 26 integration checks, 21 real-Maddy
  checks and 7 actual HTTPS runtime checks. Real R2 application recovery passed:
  `build/r2-application-d0c98375-fa33-4e1f-8094-dceb627e8e3b/result.json`.
  It checks restored mail, relay decryption, old authentication invalidation,
  fresh password/TOTP login and non-replayed recovery after lost utility completion.
  Dashboard assertions use the matched root image and application domain code;
  this fixture does not start HTTPS from the restored files.
- At that same earlier source the repeated real R2 retention probe passed:
  `build/restic-probe-f1c95716-7409-41b9-9d3b-3febd3ec2461/result.json`.
  Ten points became seven; all seven restored after pruning. The first added
  8,394,728 bytes and the unchanged second added 5,179 bytes. This is a controlled
  ten-day synthetic test, not ten elapsed production backup days.

- Native amd64 run `78f3675c-b825-402b-adb7-7d5a311eb47d` at that same earlier
  source passed 215 unit, 26 integration, 21 real-Maddy and 7 HTTPS runtime checks.
  Reports were collected and the live Maddy comparison passed. These bounded
  native fixtures do not establish full host boot/replacement-server acceptance.

Synthetic R2 objects are restricted to unique fixture prefixes. No real mailbox,
private production key, production configuration or live service was backed up.
Passing plan/model checks never counts as application or host qualification.
