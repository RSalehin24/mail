# Application audit — Oct 28, 2026

The current application is operational for its configured use. No production outage,
mail/database corruption, failed scheduled backup or application-test assertion
failure was found in this audit. It is **not certified bug-free**, and the findings
and recovery limits below prevent an unconditional “everything will work” claim.

Audited live version: `mail-admin-ui-5ab98ed4b639`.
Source: `5ab98ed4b6392eb2df4a14331f4979703db2ea46f7637fd78a0b33cbac9544ed`.
The live immutable runtime image and all served static assets match the tested build.
Application code and production configuration were not changed or redeployed.

## Findings

1. **Independent monitoring confirmation is stale (operational visibility gap).**
   The helper returns `external_monitoring.state=stale`: its activation record is
   older than the 24-hour freshness window. Local host checks and external TLS
   checks are current, and recorded alert events are delivered with no pending
   outbox. This does not establish whether Google Cloud uptime policies are
   currently enabled; their provider-side state was not reverified. Runtime code
   reads the activation file but does not periodically refresh its provider-side
   confirmation. Add periodic read-only verification and distinguish configured
   monitoring from recently confirmed monitoring. Evidence:
   `build/audit-monitor-freshness-20261028.json`; implementation:
   `helper/status.py:45`, `frontend/src/ServerPage.tsx:120`.

2. **The default HTTPS browser test runner rejects the updated browser.**
   The tracked manifest pins Chrome `156.0.8078.47`, while the installed browser
   is now `156.0.8078.73`. The unmodified default command failed its checksum
   guard. A separate current-runner manifest was captured in
   `build/audit-browser-inputs-20261028.json`, frozen for the audit, and the actual
   authenticated HTTPS/Unix-helper browser checks passed with it. Refresh the
   maintained browser manifest through the normal runner-identity workflow.
   The guard was not disabled, application source identity stayed unchanged, and
   the original failed attempt remains in `build/audit-https-browser-20261028.log`.

3. **Native test summary aggregation fails on root-owned results.**
   All 43 individual native modules completed successfully, but each full-suite
   wrapper exited with `PermissionError` while building its aggregate report.
   `read_module` tolerates a read failure, but `module_references` accesses the
   same inaccessible path without handling it. Fix result-directory ownership
   or use an explicit privileged result-export step before aggregation; do not
   make secret-bearing fixture directories globally readable. The original logs
   are retained. An isolated root reader independently checked every immutable
   per-module report, its source identity, result, assertion outcomes and hash.
   This audit does **not** relabel the failed aggregate commands as passing.
   Evidence: `build/audit-native-module-results-20261028.json`;
   `tools/maddy_modules.py:73`, `tools/maddy_modules.py:149`.

## Verification results

| Check | Result |
|---|---|
| Python lint and strict type checking | Passed; 103 source files type checked |
| Frontend lint and request/date/error checks | Passed |
| Unit tests | 504 passed |
| API integration tests | 88 passed |
| Native mail/recovery tests | 79 passed across 28 modules |
| Native runtime tests | 21 passed across 8 modules |
| Native certificate/ACME tests | 14 passed across 7 modules |
| Browser workflows | Passed |
| Authenticated HTTPS browser workflows | Passed using the separately frozen current browser identity |
| Runtime dependency consistency | No broken requirements found; this is not a vulnerability scan |
| Live static assets/image identity | Match the tested build |
| Live HTTPS, IMAPS and SMTP STARTTLS | Passed with trusted certificates, expiring Dec 26, 2026 |
| Unauthenticated protected API access | Correctly rejected with HTTP 401 |
| Unauthenticated external SMTP recipient | Rejected; no email message was transmitted |
| Live dashboard, credential and IMAP database integrity | All `PRAGMA quick_check` results were `ok` |
| Current R2 repository data integrity | Native `check --read-data` passed |
| Scheduled daily backup | Oct 28, 2026, 3:00 AM Dhaka snapshot verified; cleanup complete |
| Live account/protection state | 8 matching credential/storage pairs; 9 protections; no pending recovery |
| Capacity and services | About 22.72 GiB available; running dashboard/mail/helper; no failed systemd units |

Native coverage includes real SMTP/IMAP delivery, credentials, permissions,
mailbox deletion/recovery, additive all-mailbox imports, transaction/commit
interruptions, reconciliation, repository retention/restore, restart handling,
certificate issuance/renewal, activation crashes and snapshot coordination.
Isolated fixtures were used for destructive and crash scenarios.

## Configuration and limits

- **Inbound MX remains Cloudflare**, matching the explicitly selected configuration.
  Normal Internet mail is not routed directly to Maddy by these MX records. The
  current Cloudflare forwarding destinations were not available for verification.
  A claim that every domain message arrives in these IMAP mailboxes would therefore
  be unsupported. No DNS cutover was made.
- Outgoing relay configuration exists and a prior delivery from
  `cloud@rsalehin24.me` was accepted and receipt-confirmed. Administrator sender
  delivery is not receipt-confirmed. **Email-code login is disabled**, as selected;
  authenticator/recovery-code login is the tested path. No fresh external delivery
  test was sent during this audit.
- The catalog has two verified daily snapshots: Oct 27, 2026, 6:37 PM and
  Oct 28, 2026, 3:00 AM Dhaka. Both are present in the repository that passed its
  full data check. This is expected daily retention, not redundant same-day copies.
- A booted full-server restore of the current production snapshot remains
  **unproven**. Repository integrity and isolated restoration do not establish
  successful recovery onto a replacement server with all host/network dependencies.
- Browser-driven all-mail restoration intentionally refuses more than 32
  mailboxes, 2,000 message objects, or its 32 MiB maintenance data budget; combined
  live/import checks can reject earlier. Current live data has 8 mailboxes,
  143 message objects and about 3.91 MiB of message bodies, plus its databases.
  Growing beyond these limits requires a planned SSH/operator recovery procedure.
- This was a functional, operational and focused code/security-controls audit,
  not an independent penetration test or exhaustive dependency-advisory review.
  Passing tests cannot guarantee future provider availability, delivery, storage
  integrity, or freedom from untested bugs.

## Evidence

- `build/audit-summary-20261028.json`
- `build/audit-live-20261028.json`
- `build/audit-security-mail-state-20261028.json`
- `build/audit-monitoring-20261028.json`
- `build/audit-public-protocols-20261028.json`
- `build/audit-relay-safety-20261028.json`
- `build/audit-live-repository-20261028.json`
- `build/audit-live-assets-20261028.json`
- `build/audit-native-module-results-20261028.json`
- `build/audit-unit-20261028.log`, `build/audit-integration-20261028.log`
- `build/audit-maddy-20261028.log`, `build/audit-runtime-20261028.log`, `build/audit-acme-20261028.log`
- `build/audit-browser-20261028.log`
- `build/audit-https-browser-current-runner-20261028.log`
- `build/https-browser-d11b14cf-68b9-495a-98da-04e32cb63aa5/observation.json`
