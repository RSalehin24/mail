# Production acceptance checks

These are future operator-approved checks against a built release. Destructive fault injection belongs in isolation.

### Deployment Checks

Run these only after the dashboard has been implemented and deployed. First confirm the rendered Compose configuration, existing bind-source types, local Linux/amd64 image identity, service identity/permissions, memory/tmpfs/log limits, read-only installation marker, compatible schema/helper protocol, and lack of Docker socket/Maddy database mounts. Confirm existing Maddy data is inaccessible to unprivileged users, both Maddy launch paths enforce `0077`, the protected-recipient policy is current, shared maintenance coordination works, and boot-time helper directory creation is configured. Use the recorded project checks before promotion.

From the local Mac terminal, verify HTTPS using the certificate hostname and normal CA verification:

```bash
curl --fail --silent --show-error --max-time 15 \
  --resolve mail.rsalehin24.me:443:35.237.68.175 \
  https://mail.rsalehin24.me/api/status
```

Expected: HTTP 200 with minimal application status. This pins the connection to the target VM while checking the hostname and certificate trust; do not disable certificate verification. Independently verify the normal DNS route:

```bash
curl --fail --silent --show-error --max-time 15 \
  https://mail.rsalehin24.me/api/status
```

Both checks must fail for an expired, untrusted, or wrong-hostname certificate. Test those negative cases against local fixtures, not by replacing production certificates.

Confirm that an anonymous request cannot retrieve detailed diagnostics:

```bash
curl --silent --show-error --max-time 15 --output /dev/null \
  --write-out '%{http_code}\n' \
  https://mail.rsalehin24.me/api/diagnostics
```

Expected: HTTP 401 with no sensitive response. Repeat authorization checks through the API test suite for account/settings endpoints and password-only sessions; a successful public liveness response is not an authorization test.

Inspect live memory, restart/OOM state, and the daily renewal schedule:

```bash
ssh deployer@35.237.68.175 'sudo docker stats --no-stream maddy mail-admin'
ssh deployer@35.237.68.175 'sudo docker inspect mail-admin --format "{{.State.OOMKilled}} {{.RestartCount}}"'
ssh deployer@35.237.68.175 'systemctl list-timers --all maddy-cert-renew.timer'
```

Record idle and peak memory during an agreed bounded workload. The dashboard and helper must remain within their measured budget, leave capacity for Maddy/host activity, and exhibit no OOM kills or unexpected restarts. A single idle snapshot cannot substantiate the earlier 60 MB estimate. Confirm clock synchronization, post-restore authentication/readiness invalidation, helper journal recovery, scheduled off-VM backup success, independent alerts, free-space headroom, maintenance coordination, renewal outcome, and the certificate actually served on both 993 and 443. Verify helper access and file permissions after the agreed reboot; if swap is added, verify it persists too.

### Manual Acceptance Flow

1. **Responsive UI**: On Android, check numeric OTP entry, whole-code paste/autofill, text recovery-code entry, focus/error labels, touch targets, cards, fullscreen dialogs, and copy feedback. On laptop/desktop, check tables, search, keyboard navigation, and configuration tabs. Verify intermediate widths from 600–959px also remain usable and sensitive UI state clears on dismissal/logout.
2. **Protected enrollment**: Use the SSH-provisioned bootstrap token, set the master password, scan and verify TOTP, and save the five recovery codes. Confirm setup is closed afterward and provisioning state survives a container restart.
3. **Login and recovery**: Verify password + TOTP login, logout, one-time recovery-code use, and code regeneration. Change the master password and confirm old sessions and pending login/OTP/replacement challenges fail. Replace the authenticator and confirm the previous seed/codes/sessions fail. Exercise SSH-only recovery and interrupted-recovery cases in isolation. Ensure a password-only session cannot reach the API and sensitive actions require recent reauthentication.
4. **Brevo setup**: Confirm the SMTP login/key, authenticate over verified TLS, and explicitly send a test from `mail@rsalehin24.me` to the fixed admin address. Observe the message/test identifier and confirm receipt through the authenticated action before enabling Email OTP; if Email OTP is enabled, test it end to end. When disabled or delivery is unavailable, verify OTP refusal and retain TOTP/recovery access; P06 is not required. Change the saved relay revision in isolation to verify readiness resets. For a newly created sending address, complete sender onboarding and its separate delivery confirmation.
5. **Existing and test mailboxes**: Confirm all eight existing account pairs and metadata. Create a dedicated test account, reset its password, authenticate to IMAP, and check client configuration without revealing stored mailbox passwords. Verify the documented effect on a preexisting IMAP session. When permanent deletion is enabled, delete only that test account after explicit confirmation, a backup covering its creation and stores, and acknowledgment of the recovery cutoff; verify both stores/metadata reflect the result and existing accounts/messages are untouched. When deletion is disabled, verify API/helper refusal and retain the test account for operator cleanup; do not require P07. Prove stale-reset rejection in isolation in either mode. Verify data remains unreadable to `nobody`, including new messages.
6. **Operations and routing**: Verify candidate build/recovery evidence before maintenance, legacy pause/drain before the baseline backup or launcher recreation, baseline restore before hardening, and matched TLS cutover before dashboard startup. Check both lock scopes/order, daily UTC renewal, controlled ACME dry run, the post-enrollment full backup, scheduled encrypted off-VM backups, independent failure/expiry/backup/capacity notifications, helper/Docker/host restart recovery, cleanup/log retention, and isolated restore results with old authentication/readiness invalidated and no mutation replay. If direct Maddy delivery was selected, verify operational recipients and protected dependencies, perform the staged MX cutover checks, and document rollback evidence; otherwise verify the UI reports Cloudflare routing accurately.

Deployment acceptance requires all applicable checks to pass with recorded evidence. Mark unavailable client tests, unperformed delivery/cutover checks, and any deferred maintenance explicitly rather than claiming full verification.
