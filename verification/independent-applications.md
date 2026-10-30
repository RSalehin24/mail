# Independent applications 0.2.7 — implementation and verification

Source: `05ed1cf05e1a8de2802201e84f59be0e601a652bab00db9c4e1fb758c0f626ab`. Both independently versioned packages are built in `build/mail-apps-0.2.7/` with SHA256 files. See [installation and recovery instructions](../docs/independent-applications.md).

Production rollout remains disabled. The passing capacity result below applies to the documented Docker simulation; it cannot unlock the production gate. No production server or incoming route was changed.

## Implemented

Separate databases, keys, service definitions, browser setup and mandatory administrator MFA; certificate-verified IMAP/SMTP; single-use mutual-TLS enrollment; scoped revocable credentials and rotation; stable mailbox IDs/security revisions; separate administrator/mailbox roles; durable Trash coordination; private profiles/labels/MFA; bounded attachment processing and sending records; independent backups, migration/adoption and upgrades. Installation-specific domain, email, hostname and provider defaults were removed from product code.

## Measured baseline

Two separate Ubuntu 24.04 amd64 guests, each with 1024 MiB RAM, one vCPU, its own disk/Docker daemon and no swap. Each Docker wrapper has a separate 1280 MiB limit for QEMU overhead. These guests share a workstation. The OS reports about 961 MiB usable RAM in each guest.

Ten active synthetic mailboxes held exactly 10,000 small multipart seed messages. The 120-second run included searches, listing/opening, attachment transfers, labels, administrator activity, maintenance, and ten outgoing 10 MiB attachments. It had **zero unexpected errors, zero explicit search timeouts and zero new OOM kills**. All ten durable sends completed; the matching operation headers were independently found in recipient INBOXes through verified IMAP after authenticated-relay delivery.

| Metric | Result | Target |
| --- | ---: | ---: |
| Listing p95 | 0.422 s | < 3 s |
| Opening p95 | 0.349 s | < 3 s |
| Administration p95 | 0.347 s | < 3 s |
| Search maximum | 1.057 s | ≤ 10 s |
| Management minimum available memory | 266.0 MiB | ≥ 128 MiB |
| Webmail minimum available memory | 409.3 MiB | ≥ 128 MiB |

| Guest / container | Measured hard limit | Observed cgroup peak |
| --- | ---: | ---: |
| management / mail-management | 80 MiB | 64.3 MiB |
| management / mail-management-integration | 80 MiB | 57.5 MiB |
| management / maddy | 288 MiB | 289.2 MiB |
| webmail / mail-webmail | 384 MiB | 256.5 MiB |

Native Maddy used an explicit 192 MiB Go soft memory target inside its separate hard limit. The candidate administrator hash budget was 8192 KiB and HTTP administration concurrency four. These are measured simulation choices, not approved physical-host defaults. The two hosts produced 209 / 210 monitoring samples. Historical kernel OOM totals remained 6 → 6 and 1 → 1; earlier failures remain recorded.

Raw artifacts: [workload](../build/split-simulation/run-01/qualification-027-baseline/workload.json), [host observations](../build/split-simulation/run-01/qualification-027-baseline/qualification.json), [SMTP delivery confirmation](../build/split-simulation/run-01/qualification-027-baseline/submissions.json). The production decision in the raw report remains blocked because this is a simulation and no matching physical deployment attestation was installed.

## Functional and security verification

- 561 unit tests, 126 integration tests and 13 native Maddy tests pass on the final source. Strict mypy, Ruff, TypeScript checks, frontend read checks and package integrity checks pass.
- Independent startup, browser setup, required administrator MFA, offline diagnostics and role denial pass in standalone containers.
- Browser checks pass at 360, 768 and 1280 pixels on both applications and a real mailbox. Administrator endpoints cannot open messages; no browser credential storage is used.
- Two-host checks passed mailbox/profile/label isolation, optional mailbox MFA and single-use recovery, password changes and cross-server session revocation, UIDVALIDITY denial/reconciliation, pairing rotation/revocation/re-enrollment, hostile HTML, partial bulk failures, attachment limits and interrupted upload cleanup.
- Confirmed Trash moves/restoration/deletion were durably coordinated. After logout, native maintenance removed the exact requested UID. Missing/stale coordination is covered by conservative retention tests.
- Each application completed an encrypted Restic backup and non-destructive restore. Restored sessions and pairing were revoked, rollout/cleanup disabled and the attachment spool excluded. Management and Webmail rehearsals took about 20 and 10 seconds including restore checks.
- A second disposable Ubuntu pair rehearsed legacy export, independent activation and pairing. Mailbox IDs, profiles, labels, content, administrator MFA and uncertain sending records survived. The original Maddy container/start time and incoming configuration stayed unchanged; mailbox contents were never transferred to Webmail.
- Independent upgrades were exercised from 0.2.0 through 0.2.7. The native Maddy instance and launcher mount were retained.

Versioned lifecycle rehearsals span candidates 0.2.3–0.2.6; they are retained with their actual scope and are not presented as matching final physical-release evidence.

## Failed experiments and limits

The initial 256 MiB Webmail container was OOM-killed. Native Maddy at 224 MiB also suffered OOM restarts. Larger bounded spooling, separate I/O capacity, connection reuse, explicit Go memory control and measured container budgets addressed those bottlenecks.

Repeated trials accumulated multiple GiB of large Sent/Trash copies. That larger history eventually hit the 30-second serial compose admission deadline; listing/opening and memory targets still passed, but the run failed the zero-error criterion. It remains unqualified. The comparable passing baseline reset only the isolated synthetic dataset to 10,000 seed messages; prior results and encrypted backups were retained. No broad mailbox-size capacity claim is made.

Backups currently stop writers and interrupt service. Public DNS/PTR/firewalls, external backup replication, physical-host qualification, production cutover and a qualified pilot remain operational prerequisites. Restore creates a separate recovery directory and requires reviewed activation and identity/reference reconciliation.

Detailed, credential-free evidence is in [the JSON report](independent-applications.json). Raw private fixture files remain under `build/split-simulation/` and are not committed.

Both disposable Docker pairs and their private providers were shut down after testing.
Synthetic mailbox rollout was disabled and sessions revoked; private fixture disks
remain available for review.
