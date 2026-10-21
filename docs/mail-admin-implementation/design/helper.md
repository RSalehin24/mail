# Host helper and coordination contract

#### [NEW] `/opt/mail-admin/helper/` & `mail-admin-helper.service`

A root-owned host service owns `/run/mail-admin-helper/control.sock`, accessible only to the dedicated dashboard service group. It exposes named, bounded operations for status, account inventories, creation, password reset, and confirmed deletion. Do not expose arbitrary commands, container names, configuration paths, or Docker API forwarding. A read-only Docker socket mount still provides broad control; see [Docker daemon security](https://docs.docker.com/engine/security/).

Before invoking the CLI, verify the fixed container's tested version/image and expected configuration/data mounts against the operator manifest, and verify that the existing Maddy databases are present. If a mount/database is missing or the container points at replacement storage, fail closed instead of allowing a CLI initialization to create empty replacement state. Perform these checks inside the host helper without exposing general Docker inspection through its API.

The helper validates a bounded request schema and normalized `@rsalehin24.me` addresses independently of FastAPI. Use one reusable pure validation module, invoked at each trust boundary. For new local parts, accept ASCII letters/digits with internal dot/underscore/hyphen, normalize to lowercase, enforce the 64-byte local-part limit, and reject leading option characters, empty/consecutive dot segments, controls, whitespace, path separators, and reserved operational aliases. Preserve all eight existing addresses. Validate the password policy again inside the helper. Use fixed executable/argument arrays, a fixed `maddy` container, operation timeouts, checked exit status, bounded output, and a bounded mutation queue. Never construct shell command strings, interpolate request data into scripts, use `eval`, or invoke `shell -c`. Passwords travel only through the private socket and non-TTY process stdin; never through command arguments, environment variables, or logs.

The verified Maddy CLI operations used internally by the helper are:

- `maddy creds list` and `maddy imap-acct list`.
- `maddy creds create <email>` and `maddy creds password <email>` with password input through `docker exec -i` stdin.
- `maddy imap-acct create <email>`.
- `maddy creds remove --yes <email>` and `maddy imap-acct remove --yes <email>` only after API confirmation and helper-side backup/precondition checks.

Execute these commands through the fixed in-container umask launcher in the [runtime contract](runtime.md), including read operations that can initialize SQLite companion files. The helper's status operation returns only an allowlisted host summary: Maddy availability/version, host memory/disk, renewal outcome/served-certificate state, and backup/protected-recipient readiness with observation timestamps. Read only fixed operator-configured sources; expose no arbitrary paths, Docker inspection payloads, credential hashes, or message data. Container memory/filesystem statistics must not be mislabeled as host-wide metrics. An unavailable or stale observation is unknown/degraded, not healthy.

Keep reviewed recipient dependencies in root-owned `/etc/mail-admin/helper-policy.json`, outside web-writable mounts, including identities of the reviewed Maddy configuration and any referenced routing tables. Block deletion when the policy is absent/stale or the address is a protected delivery/alias target. Update this policy only through the operator procedure after validating changed routing; do not implement a second general Maddy configuration editor/parser in the dashboard.

Keep backups and helper files outside dashboard-writable directories. Log operation, outcome, and safe diagnostics without credentials, OTPs, QR payloads, session tokens, or message contents.

Store a small durable operation journal under root-owned `/var/lib/mail-admin-helper/`, outside the web container's mounts. Record request identifiers, normalized addresses, preconditions, completed steps, observed postconditions, and unresolved outcomes; store no passwords. Bind each request identifier to its operation/address and reject reuse for conflicting operations or accounts. Persist intent before executing a mutation and progress after each step; if durable recording fails, do not launch the command. On helper restart, reconcile unfinished records before accepting conflicting writes. Duplicate requests return the recorded/reconciled outcome; they never repeat a completed password change implicitly. Journal/database-full or I/O failures must remain explicit and must not be treated as successful writes.

Use two root-controlled host locks, provisioned before the first production backup and recreated with restrictive permissions at boot. Validate root ownership, regular-file type, and mode `0600`; reject unexpected owners or symlinks. Their order is fixed: **`/run/lock/mail-admin-renewal.lock` before `/run/lock/mail-admin-maddy.lock` whenever both are required**.

| Participant | Lock scope |
| :--- | :--- |
| Helper CLI, including inventories | Hold the Maddy maintenance lock for the complete CLI operation and its journal/postcondition handling. |
| Generation-aware renewal, including manual invocation | Hold the renewal-run lock for the whole run, including Certbot acquisition and ACME-state writes; additionally hold the Maddy maintenance lock during publication and consumer activation. |
| Consistent backup/restore or initial TLS migration | Acquire renewal-run, then Maddy maintenance; hold both while collecting or changing the coordinated state. This excludes ACME writes as well as helper/container writes. |
| Initial dashboard creation/registration or other container recreation | Hold the Maddy maintenance lock; use both locks if the procedure also changes TLS/ACME state. Resolve the desired generation and record consumer registration inside the same protected operation. |

Never acquire the renewal-run lock while holding only the Maddy lock. Use the bounded waits recorded in the deployment record and keep lock-file inodes stable while participants are active. Drain operations and check unresolved journal outcomes even if a crashed helper released its lock: lock acquisition alone does not prove an old container command finished. Automatic maintenance must defer and alert when completion is unknown. Release locks before invoking a service/helper that acquires them itself. ACME acquisition can proceed alongside mailbox operations, but cannot proceed during a consistent backup. Before the legacy script is replaced, explicitly pause/drain it using the maintenance-entry procedure below; it participates in neither lock. Do not depend on the dashboard API to coordinate host maintenance.

Apply the same unknown-completion rule to Certbot: durably record the renewal-run/container identity in the root-owned certificate state before launch, reconcile a surviving container after interruption, and block backup or conflicting renewal while completion is unknown. A terminated host script or released lock does not establish that a Docker-managed Certbot process stopped writing ACME state.

Create `/run/mail-admin-helper` at provisioning and on each boot using a root-owned tmpfiles configuration, with the documented owner/group and mode. Ensure directory creation completes before Docker can autostart the dashboard. Enable the helper at boot after Docker is available and restart it on process failure. Keep the same directory inode across helper stops/restarts; replace only the socket inside it. Do not let service cleanup remove the mounted directory. `/run` is recreated at boot and systemd runtime-directory cleanup has explicit lifecycle rules; see [systemd directory handling](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#RuntimeDirectoryPreserve=).

The API tolerates the helper starting later or reconnecting: return an explicit unavailable/pending result for dependent operations, reconnect with bounded retries, and check the durable request outcome before retrying any mutation. Test helper restart, Docker restart, and a full host reboot; no socket directory may be silently recreated with broader permissions.

## Durable operator maintenance intent

Use the existing root-owned certificate/consumer state to record operator maintenance intent before deliberately stopping a consumer for recovery or restore. Renewal distinguishes never deployed, expected running, temporarily stopped, and unresolved outcomes. An intentionally stopped consumer stays stopped until explicit operator resume; it is not silently treated as healthy or automatically restarted. Reconcile stale intent observationally after restore. This preserves the recovery requirement even when scheduled renewal or Docker restarts occur.
