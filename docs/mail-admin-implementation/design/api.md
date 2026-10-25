# API contract

These paths define the required application API. The API is implemented locally;
qualification and production deployment remain pending. See the
repository-root `verification/status.md` for source-specific results.

#### [NEW] `/opt/mail-admin/backend/main.py`

FastAPI serves the compiled React assets (`dist/`) and REST API. Authentication is enforced centrally for every protected route; password verification alone never grants account, settings, or helper access.

- `GET /api/status`: Minimal public application liveness response; expose no account inventory, secrets, or detailed host diagnostics.
- `GET /api/diagnostics`: Authenticated DNS, certificate/renewal, resource, relay, and Maddy diagnostics with explicit unknown/unverified states.
- `POST /api/setup`: Bootstrap-token-protected enrollment and TOTP confirmation; reject setup after initialization.
- `POST /api/login`: Verify credentials and issue only a short-lived pre-authentication challenge.
- `POST /api/2fa/verify`: Complete that challenge using TOTP, Email OTP, or a recovery code; issue the full session only after success.
- `POST /api/2fa/send-otp`: Requires a valid login challenge, or a reauthentication challenge bound to the current full session; sends only to `rsalehin24@gmail.com` through the tested Brevo configuration.
- `POST /api/logout`: Revoke the current server-side session and clear its cookie.
- `POST /api/security/reauth`: Requires a current full session; verify the current password and create a short-lived reauthentication challenge bound to that session/authentication revision using the shared challenge logic.
- `POST /api/security/reauth/verify`: Complete that challenge with an existing TOTP, recovery code, or tested Email OTP using the shared factor logic. Record the five-minute grant on that session; do not issue a login session or extend its absolute expiry. Login and reauthentication verification reject challenges with the wrong purpose or session binding.
- `POST /api/security/password`: Change the master password after recent password + 2FA verification; atomically revoke existing sessions, login/reauthentication challenges, Email OTPs, pending factor replacements, and recent-authentication grants.
- `POST /api/security/recovery-codes`: Regenerate recovery codes after recent password + 2FA verification and invalidate previous codes.
- `POST /api/security/totp`: Start and confirm time-limited replacement enrollment after recent password + an existing second factor; commit the new verified seed and invalidate old authentication state atomically.
- `GET /api/accounts`: Reconcile both Maddy inventories and return mailboxes, metadata, and discrepancies.
- `POST /api/accounts/create`: Provision credentials and IMAP storage, then metadata, with explicit partial-failure handling.
- `POST /api/accounts/{email}/password`: Reset an existing, consistent Maddy account after recent reauthentication and helper-side inventory checks; report that existing IMAP sessions are not disconnected by this operation.
- `POST /api/accounts/{email}/test-sender`: After recent reauthentication and an explicit send action, test the existing mailbox address as a Brevo From identity, delivering only to the fixed admin recipient. Record the outcome separately from inbound provisioning; share relay validation/sending logic with the settings test.
- `DELETE /api/accounts/{email}`: Require recent reauthentication, exact-address confirmation, an acknowledged backup cutoff, and helper-verified backup coverage/dependency checks before deleting credentials and stored mail.
- `GET /api/accounts/{email}/config`: Return client configuration with password placeholders and masked relay secrets.
- `GET /api/settings/relay`: Return relay configuration with secrets masked.
- `POST /api/settings/relay`: Update relay configuration after recent reauthentication; validate permitted SMTP host, port, and TLS mode.
- `POST /api/settings/relay/test`: Test the saved relay revision after recent reauthentication; an explicitly requested delivery test sends only to the fixed admin address using the fixed, authorized OTP From address and returns a test identifier.
- `POST /api/relay-tests/{test_id}/confirm`: After recent reauthentication, record the operator's observed receipt for a server-created, SMTP-accepted test bound to its sender, fixed recipient, and current saved relay revision. Reject unknown, failed, or superseded tests; confirmation cannot turn a connection-only test into delivery evidence.
- `POST /api/settings/relay/reveal`: Reveal only the configured SMTP credential after recent reauthentication and an explicit user action; set `Cache-Control: no-store`.

Enforce the deployment capability flags centrally: disabled permanent deletion returns a safe forbidden response and cannot reach a mutation; disabled Email OTP cannot send or verify Email OTP even when relay readiness is confirmed. Hiding UI controls is additional feedback, not enforcement. Enablement changes require updated deployment configuration/evidence.

Apply CSRF protection or strict Origin checks to cookie-authenticated mutations. Validate mailbox domain and local part before helper calls, including existing dot/underscore names. Restrict relay connections to the intended Brevo endpoint and TLS ports; do not turn connection testing into a general network proxy.

Apply `Cache-Control: no-store` to API/authentication responses, including setup QR material, recovery codes, and error responses. Accept only the configured HTTPS host/origin, render names/notes as text, and keep secrets out of URLs, browser storage, and access/error logs. Serve `/api` routes before the SPA fallback; unknown API routes return API errors. Return a request identifier and a safe error/outcome for failed or pending mutations rather than exposing CLI traces.

Enforce a 64 KiB JSON request-body limit while streaming, including requests without `Content-Length`; bound field sizes and helper request size separately. Run blocking password hashing, SMTP/DNS work, and helper calls outside the event loop with bounded queues and operation deadlines. Hold hash-work capacity until the actual work finishes even if the HTTP request is cancelled. Select and document HTTP concurrency/backlog/timeouts from the bounded load test; return explicit overload/unavailable responses instead of accumulating work. See [Uvicorn resource behavior](https://uvicorn.dev/server-behavior/).
