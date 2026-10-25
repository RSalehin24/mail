# Server baseline and provenance

Sanitized observations from the recorded SSH sessions are in [observations.json](observations.json). They describe the production host at those times, not this development workstation or current production resource limits. Recheck before maintenance. No dashboard was deployed at the recorded checks; local implementation status is tracked separately in repository-root `verification/status.md`.

## Verified Server Baseline — October 21–22, 2026

These facts were checked over SSH and, where noted, from an external client. The latest SSH recheck was **October 22, 2026 at 02:30 Asia/Dhaka (October 21 at 20:30 UTC)**. Recheck them before deployment. The older `maddy-mail-server-docs.txt` is not the deployment source of truth.

| Item | Verified state |
| :--- | :--- |
| Host | Ubuntu 26.04 LTS, Linux/amd64, `35.237.68.175` |
| Maddy | Running version **0.9.5**, using the mutable `foxcpp/maddy:latest` image tag |
| Configuration and storage | `/opt/maddy/config/maddy.conf`; SQLite `credentials.db` and `imapsql.db` under `/opt/maddy/data` |
| Existing data permissions | Follow-up review confirmed `nobody` can read both Maddy databases and all **143 stored message files**; data directories are traversable and files are `0644`. Restrict access before deployment. |
| Account inventory | **8 credential accounts and 8 matching IMAP accounts**; local parts: `cloud`, `demo_user.library`, `dev_01`, `dev_02`, `library`, `mail`, `reyan`, and `salehin`, all under `rsalehin24.me` |
| Memory and disk | **952.7 MiB RAM total**, **551.7 MiB available at the latest SSH check**, and **22.4 GiB disk free**; no active swap. Available memory varies; this snapshot is not a runtime budget. |
| Published services | TCP **25 and 993** reachable externally; no listener on **443**; Maddy submission ports are not published |
| Certificates | `/opt/maddy/tls/fullchain.pem` and `privkey.pem`, mounted into Maddy at `/data/tls`; externally verified IMAPS certificate valid through **December 26, 2026** |
| DNS | `mail.rsalehin24.me` resolves to the server; public domain MX records point to Cloudflare Email Routing |
| Renewal | `/opt/maddy/renew-cert.sh` restarts only Maddy; `maddy-cert-renew.timer` checks monthly on day 1 at 03:00 UTC, with a 32-day threshold |
| Brevo | Connection to `smtp-relay.brevo.com:587` and verified STARTTLS succeed; SMTP credentials, sender authorization, and mail delivery remain untested |
| Dashboard | `/opt/mail-admin` and the dashboard container do not exist yet; application performance and behavior remain unverified |
| Boot and clock | Docker is enabled at boot; system clock synchronized with NTP active. The helper is not installed yet. |
| Host maintenance and tools | A host restart is required; include it in the agreed reboot window. Docker Compose **5.1.4** and util-linux `flock` **2.41.3** are installed. No host updates or reboot were performed during review. |
| Process umask | Both the Maddy daemon and a fresh `docker exec` process use `0022`. A read-only probe confirmed that setting the invoking host process to `0077` does **not** change the container exec process's umask. |
| Operational recipient | The configuration rewrites bare `postmaster` to `postmaster@rsalehin24.me`, which is absent from both account inventories. Delivery to that operational address remains unverified; resolve it before any direct-MX cutover. |

The running Maddy version and renewal script match this plan's original assumptions. Review the [Maddy 0.9.6 security release](https://github.com/foxcpp/maddy/releases/tag/v0.9.6) before deployment, assess applicability, and pin a tested image digest. Any upgrade needs a consistent backup, compatibility checks, and a rollback procedure; do not upgrade implicitly while deploying the dashboard.

The baseline Maddy local image ID is `sha256:de42151adff6388edb5e4ee88f60334fa1ab85e309485193ecb1c2db20203315`. The legacy renewal script SHA-256 is `638dfd7338d3b7d7c518089cfe631847568f4e9862ec9c02c8415c5352acdfc2`; it still copies the flat TLS files, restarts Maddy, and uses no `flock`. Its timer is enabled, its oneshot service was inactive at the check, and its next scheduled run was November 1 at 03:00 UTC. An inactive snapshot does not replace the required drain immediately before maintenance.

---

## Limited read-only recheck — October 22, 2026

At 06:11:32 UTC, SSH confirmed Maddy running on version 0.9.5 with the same recorded image ID and legacy renewal-script SHA-256. The renewal timer remained active/enabled, NTP reported synchronized, and `/opt/mail-admin` was absent. This limited recheck did not repeat account inventory, file-permission, memory, DNS, TLS, SMTP-authentication or delivery tests; their earlier observations remain dated snapshots. No server configuration or mail was changed.
