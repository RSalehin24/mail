# Mail Management and Webmail 0.2.7

This repository builds two separate self-hosted applications. The existing live
installation is unchanged. Version 0.2.7 is a development candidate: the actual
Ubuntu installation/adoption, recovery rehearsal and two separate 1 GB host
qualification must pass before release or mailbox rollout. Local functional
checks do not establish a supported user capacity.

| Package | Persistent state | Privileges and connections |
| --- | --- | --- |
| Mail Management | Own database and keys; stable account IDs, security revisions, relay settings, pairing, Trash history, recovery metadata | Local root Unix helper manages Maddy. Message storage stays on this host. Separate HTTPS integration listener on 8444. |
| Webmail | Own database and keys; private profiles, optional mailbox MFA, labels, sessions, sending records and temporary attachments | Verified TLS IMAP 993, authenticated STARTTLS SMTP 587, certificate-authenticated integration API. No Docker socket, root credentials, shared SQLite or message-store mount. |

Both packages include the same React asset build; their selected runtime exposes
the appropriate routes. Webmail administrators have independent MFA-protected
accounts and cannot enter mailbox endpoints. Mailbox users use a separate login
path, even when a mailbox address equals the administrator address.

## Build and install

Use the existing locked build prerequisites from the repository README:

```sh
make split-build
make split-packages
```

The archives and SHA256 files appear under `build/mail-apps-0.2.7/`. Each archive
contains its immutable Linux amd64 image, locked Python dependencies, host tools,
Restic binary, service generator, integrity manifest and `install.sh`.

On **each separate Ubuntu 24.04 LTS amd64 host**, install Docker Engine with the
Compose plugin and `python3-venv` first. Verify the archive checksum from a trusted
copy, extract it and run its installer. Select candidate resource limits yourself;
the failed historical limits are not defaults. The management host reserves
separate process/container budgets for the browser API, integration listener,
Maddy and backup job. The Webmail host has its own independent budget.

```sh
# Mail host: load a reviewed immutable Maddy 0.9.6 image separately.
sudo ./install.sh --hostname manage.example.net \
  --memory-mib "$MANAGEMENT_CANDIDATE_MIB" \
  --maddy-image "$MADDY_IMAGE_ID" --maddy-memory-mib "$MADDY_CANDIDATE_MIB" \
  --maddy-go-memory-mib "$MADDY_GO_CANDIDATE_MIB" --backup-memory-mib "$BACKUP_CANDIDATE_MIB" \
  --hash-memory-kib "$HASH_CANDIDATE_KIB" --http-concurrency "$HTTP_CANDIDATE_LIMIT" --check
# Repeat the same command without --check after reviewing preflight.

# Webmail host, from the Webmail package:
sudo ./install.sh --hostname webmail.example.net \
  --memory-mib "$WEBMAIL_CANDIDATE_MIB" \
  --hash-memory-kib "$HASH_CANDIDATE_KIB" --http-concurrency "$HTTP_CANDIDATE_LIMIT" --check
# Repeat without --check.
```

Preflight checks OS, architecture, memory, disk, public ports, immutable image
identity, known service directories and existing containers before installation
mutations. Unknown installations are refused. Installation creates protected
storage, a distinct local account, keys, a temporary HTTPS certificate, services
and a 30-minute single-use setup token. Verify the printed bootstrap certificate
fingerprint before entering that token. The setup wizard replaces the temporary
certificate with the chain and private key supplied by the customer.

The wizard configures the domain, mail and application hostnames, administrator,
TLS, authenticated relay and backup destination. Administrator setup then requires
a password, authenticator enrollment and recovery codes. Setup closes after MFA
confirmation. A lost or expired initial token can be replaced locally with
`deployment.manage setup-token`; this cannot reopen an initialized account.

The customer publishes DNS records, arranges provider-controlled reverse DNS,
opens cloud/host firewall ports, obtains and renews public TLS certificates,
authorizes the relay sender domain and supplies externally replicated backup
storage. DNS diagnostics compare actual published A, MX, TXT and PTR values and
show missing provider DKIM inputs; they do not configure a DNS or cloud account.
For a relay using additional records such as DKIM CNAMEs, publish and verify those
provider-specified records separately. Fresh Maddy receives directly on port 25;
its authenticated submission queue delivers through the configured relay.

After bootstrap, authenticated installation pages provide pairing diagnostics,
credential controls, backup destination changes, relay controls and TLS renewal.
Domain/hostname changes after setup require an explicit migration.

## Pairing and revocation

Generate an enrollment token in Mail Management's Installation page, then paste
it in the Webmail administration page within five minutes. Its temporary client
certificate authenticates the initial mutual-TLS exchange. Webmail generates its
own permanent private key and exchanges a CSR for a domain- and installation-bound
certificate and revocable service credential. The enrollment token is consumed
once. Protect its full contents as a secret; it includes bootstrap transport keys.

The dedicated integration CA validates client transport certificates. The HTTP
identity comes from the actual TLS socket, never forwarded headers. Each service
request additionally checks its certificate fingerprint, credential, domain,
expiry and API version. No message-content endpoint exists. Password changes also
verify the current mailbox password over IMAP and atomically reserve the expected
account security revision. A concurrent or uncertain security change blocks
subsequent checks until reconciled.

Rotate from Webmail. A prepared replacement is persisted before activation;
activation retires the previous credential. Resume an interrupted activation from
the same page. Mail Management can revoke credentials and their prepared
replacements. A second installation must be explicitly unpaired/revoked first.
Mailbox identity checks fail closed when the management service is unavailable.
A changed account ID, credential fingerprint or security revision invalidates the
old mailbox session. Recreated accounts cannot inherit private profiles.

## Trash and restore

Webmail records durable cleanup holds before moving/restoring/deleting messages,
then reports ordered, idempotent events. Interrupted moves and missing destination
UIDs retain their holds. The mail host owns observation times and exact native
removal. Cleanup requires current native identity and UIDVALIDITY, no outstanding
hold, fresh coordination and an explicitly reconciled maintenance state. A hold
and the final removal decision serialize through the management database.
Missing coordination postpones deletion. Native Maddy writes are quiesced and
references rechecked before removal.

Each application has its own encrypted Restic repository and daily backup timer.
Set the destination under `/var/backups/mail-management` or
`/var/backups/mail-webmail`; mount or replicate that destination externally. Keep
`/etc/mail-APPLICATION/backup.key` off-host separately. The backup contains keys,
installation identity and recovery metadata. Mailbox deletion must be enabled
explicitly in Management and requires a recent root-verified backup with matching
native identity and credential coverage; operational recipients are protected. The current backup implementation
stops the application's containers and, for Management, Maddy while capturing the
snapshot; it therefore interrupts service and mailbox sessions. Test its pause
length and memory on the target hosts. Restic transfer/check is bounded and all
stopped services are resumed on failure. This maintenance behavior requires target
qualification and is not a zero-downtime backup claim.

Open a root shell and change to `/opt/mail-APPLICATION/VERSION` before running
host commands. In the examples below, `python` means that release's
`.venv/bin/python`; the working directory is required for module discovery:

```sh
python -m deployment.recovery backup /etc/mail-management/host.json
python -m deployment.recovery restore /etc/mail-webmail/host.json \
  --repository /var/backups/mail-webmail --password-file /secure/off-host-copy.key \
  --snapshot SNAPSHOT_ID --destination /new/empty-restoration-directory
```

Restoration never overwrites the live installation or starts restored services.
It revokes sessions/pairing, marks interrupted sends uncertain, disables mailbox
rollout and Trash cleanup, and writes explicit reconciliation metadata. Review
restored identities against the native store, re-pair and reconcile message
references before enabling cleanup. Stop the paired Webmail after flushing its
outbox, allow its coordination heartbeat to expire, and run
`deployment.manage reconcile-trash /etc/mail-management/host.json`. Reconciliation
checks active native account identities, clears old cleanup holds and restarts
Trash clocks; it does not authorize replaying SMTP sends. Review restored native
identity generations separately if they differ: the tool refuses to guess owners.
For a verified same owner after an interrupted account operation or folder-generation
change, use `deployment.manage reconcile-account HOST_JSON --mailbox ADDRESS --retain-owner`.
Use `--new-incarnation` to retire the previous identity for a confirmed replacement;
the next lookup receives a new ID. These explicit ownership decisions disable
cleanup until references are reconciled again.

## Adopt the existing deployment

Do not run a fresh installer over the existing installation. First provide a
validated, mode-0600 Site JSON with `domain`, `mail_hostname`, `admin_email`,
`hostname`, `relay_host`, and the existing `delivery_domain` if used. The exporter
reads a consistent SQLite snapshot and native identity metadata. Stop the legacy
dashboard and Webmail before exporting to freeze profiles, labels and sending
records; leave Maddy running. Exported sessions are revoked. The exporter copies
no mailbox contents and changes no routing:

```sh
python -m deployment.adopt \
  --source-database /existing/dashboard/admin_data.db \
  --source-marker /etc/mail-admin/installation.json \
  --source-secrets /etc/mail-admin/secrets --mail-store /existing/maddy/data \
  --site /secure/reviewed-site.json \
  --management-output /new/management-export --webmail-output /new/webmail-export \
  --webmail-hostname webmail.example.net \
  --hash-memory-kib "$HASH_CANDIDATE_KIB" --http-concurrency "$HTTP_CANDIDATE_LIMIT"
```

Profiles, labels, sending records and verified existing mailbox IDs are migrated.
Different native generations stay inactive. Webmail receives fresh independent
keys; authenticator secrets are re-encrypted and mailbox recovery codes must be
reissued after password/authenticator login. Both applications revoke web sessions.
Sending states interrupted at submission or Sent-save boundaries become uncertain.

Review both exports, transfer only the Webmail export securely to the Webmail
host, then activate with `deployment.activate_adoption KIND EXPORT PACKAGE`,
providing `--fullchain`, `--private-key`, `--memory-mib` and, on Management,
`--legacy-deployment` and an explicit `--maddy-memory-mib` for transient native
maintenance (the running Maddy limit is unchanged). Use a locked-dependency virtual environment for these host
commands. Keep the old HTTPS dashboard stopped throughout activation; Maddy continues running. The activation command validates the original Maddy
image/mount identity and retains its configuration and incoming routing. Select
backup destinations in both authenticated interfaces and enroll the separate
Webmail administrator with a locally issued setup token. Keep Webmail disabled.

Adoption is distinct from incoming cutover. First verify public DNS, firewall,
reverse DNS, TLS and direct delivery using synthetic external mail while retaining
the old route. Back up and record the existing route. Change the primary MX only
in a separately authorized, monitored cutover with a tested rollback. This task
has not performed that production action.

## Upgrade

Keep both packages compatible with integration v1 and the advertised capabilities.
Upgrade either host independently with a checksum-verified new version:

```sh
cd /reviewed/new-webmail-package
sudo /opt/mail-webmail/OLD_VERSION/.venv/bin/python -m deployment.upgrade \
  /etc/mail-webmail/host.json "$PWD"
```

The upgrader verifies package contents and recognized service definitions, stages
the new release/dependencies and image, takes a pre-upgrade database snapshot,
stops that application's services, performs the explicit additive schema migration,
revokes sessions and restarts its services. Maddy data/routing and the other
application remain separate. The native container retains its reviewed launcher mount;
keep the original release directory while that mount references it. Check HTTPS
health and pairing immediately afterward.
A failed staged dependency/image step leaves the running release selected; inspect
and remove only its identified incomplete new release before retrying. Do not
blindly restore the pre-upgrade database over later sending records. Recovery is a
separate restore-to-new-directory procedure above. Unsupported versions or missing
capabilities block operations with an actionable upgrade message.

## Two-host qualification and rollout

Use two separate Ubuntu 24.04 amd64 test hosts with 1 GB RAM each, marked with the
installer's `--qualification-only` option. Configure, enroll MFA and pair them.
Make a mode-0600 synthetic configuration containing scope
`isolated-two-host-mail-qualification`, separate `management_ssh` and `webmail_ssh`
targets, HTTPS `management_origin` and `origin`, a current management administrator
session `admin_token`, ten `accounts` named `qualify-...@DOMAIN`, one synthetic
`password`, and `messages_per_mailbox: 1000`.

Use `deployment.manage prepare-qualification HOST_JSON --fixture CONFIG` locally
on both test hosts. Only this explicit root operation in a marked test installation
can enable ten synthetic mailboxes before qualification. Then seed and measure:

```sh
.venv/bin/python tools/seed_two_host_mail.py /secure/synthetic-config.json
.venv/bin/python tools/qualify_two_hosts.py /secure/synthetic-config.json build/two-host-run \
  --cases /reviewed/matching-lifecycle-case-evidence.json
```

The harness uses verified HTTPS and IMAP, runs ten concurrent mailbox workloads,
10 MiB sends/uploads, attachment downloads, listing/opening/searches and management
activity. It independently observes each host through authenticated SSH, checks
its isolated marker, records actual cgroup memory limits/peaks and OOMs, physical
available memory and maintenance cycles, and identifies distinct machines and
installations. Missing monitoring, lifecycle cases or samples block qualification.
The lifecycle report must contain actual matching installation/adoption, pairing,
rotation/revocation, upgrade, disconnection, restore, isolation, MFA/recovery,
password/recreation/revocation, native mail, HTML, attachment interruption/limits,
UIDVALIDITY, partial-failure and logged-out Trash evidence. Local unit-test totals
cannot substitute for these real deployment cases.

The gate requires no unexpected errors/OOM kills, at least 128 MiB available on
**each** host, responsive management, p95 listing/opening under three seconds and
search completion or explicit timeout within ten seconds. If a host fails, retain
its measurements, identify its bottleneck, change candidate sizing and repeat the
complete test; no proposed limit becomes approved automatically.

Install a passing report locally with `deployment.manage qualify HOST_JSON
--report REPORT`. The authenticated rollout control first permits one pilot
mailbox. Verify it operationally, mark it with `deployment.manage verify-pilot
HOST_JSON --mailbox ADDRESS`, then expand only to the ten tested users. Disable
the synthetic testing allowlist after measurement. Until all gates pass, production
Webmail remains disabled. This development task has no two-host capacity report.

Configuration syntax and enforced relay TLS follow the
[Maddy SMTP target reference](https://maddy.email/reference/targets/smtp/) and
[Maddy installation guidance](https://maddy.email/tutorials/setting-up/).


## Docker simulation

The development harness boots two separate Ubuntu 24.04 amd64 KVM guests inside
Docker containers. Each guest has 1 GiB RAM, one vCPU, its own disk, Docker daemon,
systemd and kernel memory accounting, with swap disabled. Each outer wrapper has
a separate 1280 MiB limit to accommodate QEMU overhead. This measures guest hosts;
it does not reinterpret the workstation's `/proc/meminfo` as a 1 GB server.
Linux with an accessible `/dev/kvm` is required.

Build the tooling and obtain the pinned Ubuntu image (the create command verifies
its SHA256 before boot):

```sh
docker build --platform linux/amd64 --target qualification -t mail-admin:split-qualification .
docker build --platform linux/amd64 -f tools/vm/Dockerfile -t mail-admin:split-guest .
mkdir -p build/split-simulation
curl --fail --location \
  https://cloud-images.ubuntu.com/releases/noble/release-20261017/ubuntu-24.04-server-cloudimg-amd64.img \
  --output build/split-simulation/ubuntu-24.04.img
.venv/bin/python tools/simulate_split.py create build/split-simulation/new-run \
  --base build/split-simulation/ubuntu-24.04.img
.venv/bin/python tools/simulate_split.py provider build/split-simulation/new-run
.venv/bin/python tools/simulate_split.py guest build/split-simulation/new-run \
  --kind management -- cat /etc/os-release
```

Wait for `/var/lib/mail-simulation-ready` on each guest. Copy the packages using
`simulate_split.py copy ROOT --kind KIND -- SOURCE DESTINATION`, install them with
the commands above and `--qualification-only`, and complete setup and MFA. The
private test provider supplies DNS and authenticated STARTTLS SMTP solely for
`split.test`; it cannot send public email. Its certificates are under `ROOT/tls`.
Install that test CA in the simulated applications' trust stores; production uses
customer-provided publicly trusted certificates. The application containers still
receive no host Docker socket or root credentials. The tooling's SSH proxy is
confined to these labelled disposable QEMU wrappers.

Create the same private workload configuration described above. This command adds
pinned SSH targets and private `.test` DNS overrides without editing host DNS:

```sh
.venv/bin/python tools/simulate_split.py configuration ROOT --fixture CONFIG
SSL_CERT_FILE="$PWD/ROOT/tls/ca.pem" \
  NO_PROXY=manage.split.test,mail.split.test,webmail.split.test,relay.split.test \
  .venv/bin/python tools/qualify_two_hosts.py CONFIG build/simulated-qualification
.venv/bin/python tools/simulate_split.py stop ROOT
```

Simulation reports carry `simulation: true` and cannot unlock production rollout.
The simulated capacity profile and separate adoption, recovery and browser results
are recorded in [the verification report](../verification/independent-applications.md).
Container budgets in that report are measured candidates for this fixture,
not approved physical-host defaults. The native Go memory target is a soft runtime
target inside a separate hard container limit; see the
[Go garbage collector guide](https://go.dev/doc/gc-guide#Memory_limit).
The temporary attachment spool has a bounded 128 MiB retained budget and twice
that for concurrent processing on split Webmail installations, configurable at
installation. The per-message attachment limit remains 10 MiB. Interactive reads,
attachment transfers and composition use separate bounded worker capacity.
Search deadlines include admission and connection waits, and are reported explicitly.
