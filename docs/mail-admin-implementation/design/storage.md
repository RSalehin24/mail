# Persistent state contract

#### [NEW] `/opt/mail-admin/backend/database.py` & `models.py`

SQLite database (`admin_data.db`):

- `admin_auth`: Admin password hash, encrypted active/pending TOTP secrets, replacement-enrollment expiry, recovery-code hashes, initialization/enrollment state, authentication revision, and last accepted TOTP time step.
- `sessions`: Hashed opaque session identifiers, creation/last-activity timestamps, expiry, recent password/factor verification time, and revocation state.
- `auth_challenges`: Bootstrap/login/reauthentication verifiers, purpose and optional bound-session identity, authentication revision, challenge expiry, and attempt/resend limits; no fully authenticated permissions.
- `auth_rate_limits`: Persistent per-admin/client enforcement windows, independent of individual challenges so creating another challenge cannot reset the applicable limit.
- `relay_settings`: Brevo SMTP host, port, username, encrypted SMTP credential, encrypted optional API key, saved configuration revision, and OTP-readiness state.
- `relay_tests`: Server-generated test identifier, saved relay revision, sender/fixed recipient, connection/SMTP outcome, observation time, and manual receipt confirmation; no OTPs or relay secrets.
- `mailboxes`: Email, display name, notes, timestamps, reconciliation status, and separately recorded outbound-readiness evidence/time; no mailbox passwords or message contents.
- `otp_codes`: Keyed verifiers bound to challenges with expiry and consumption state; no plaintext OTPs.
- `audit_logs`: Timestamped activity/outcome/request identifiers for authentication, account mutations, security/recovery changes, relay edits/tests, and secret-reveal actions; record the action, never the revealed value.

Use atomic updates when consuming challenges, OTPs, recovery codes, or initialization state. Restrict data-directory/database access to the service account. Back up the database and required encryption keys with a documented recovery procedure; keep keys out of SQLite, source control, image layers, and logs.

Version the dashboard schema and helper protocol in the deployment manifest. Apply explicit, tested database migrations with a consistent backup before public startup; incompatible or partially migrated state fails closed. Use short transactions and bounded SQLite busy handling; locked/full/read-only/corrupt storage must not bypass authentication or report a mutation as committed. Never hold a database transaction open while waiting for SMTP/helper I/O. Do not downgrade a schema by running an older image against it; rollback requires a compatible image/schema/helper set and the documented post-restore authentication invalidation when restoration is needed.

Run bounded cleanup at startup and at least hourly: remove expired sessions, login/reauthentication/setup challenges, pending replacement enrollments, and expired/consumed Email OTP records once no active rate-limit window depends on them. Preserve rate-limit state through its enforcement window, active-factor replay protection, and recovery-code consumption state until the code set is replaced. Retain audit events and relay-test history for 90 days by default, then prune in batches; preserve the current revision's readiness evidence separately until invalidated. Document any changed retention requirement. Keep unresolved helper operations until reconciled and retain completed operation records for at least 90 days. Limit automatic request retries to 24 hours after their recorded creation; older pending requests require operator reconciliation, not blind resubmission. Bound container logs using the Compose rotation settings below and document a finite retention policy for host helper/renewal logs.

---
