# Required release inputs

## Deployment Decisions & Boundaries

> [!IMPORTANT]
> **Admin Email & Authentication**: Strictly locked to `rsalehin24@gmail.com`. The email identifies the admin but does not prove ownership. Provision a private bootstrap token through SSH, require it for all setup API access, and complete setup only after verifying the admin's TOTP code.

> [!NOTE]
> **Required Decisions**: Confirm whether to retain Cloudflare Email Routing or make Maddy the domain's primary MX. Preserve existing routing until an explicit cutover decision. Confirm the tested Maddy version, backup destination, independent alert channel, and any maintenance window before upgrading or changing production mail delivery.

This document authorizes no deployment by itself. Implementation must preserve existing accounts and messages, and treat DNS cutover, Maddy upgrades, and permanent mailbox deletion as explicit operational actions.

### Required Deployment Record

Prepare one non-secret deployment record with the release artifacts, then install it root-owned at `/etc/mail-admin/deployment.json` during host provisioning. Reuse this record for the helper's expected image/mount configuration and host deployment procedures. Store credentials and key material separately in their specified protected locations. Every required configuration value and pre-production fixture result must be present before entering maintenance. Add production evidence at the phase that produces it; the next dependent phase cannot proceed until it passes. A missing preflight value stops rollout before maintenance.

| Required record | Concrete completion condition |
| :--- | :--- |
| Release artifacts | Tested Linux/amd64 image identity, pinned dependencies, helper protocol/dashboard schema versions, launcher/renewal/unit configuration identities, exact project check commands/results, and a compatible rollback set. The isolated suite and dashboard recovery fixtures pass before production changes. |
| Target identity | Record the stable cloud project/zone/instance identity and trusted SHA-256 host-key blob digest. Bind authenticated preflight/checkpoint observations and final deployed-artifact observations as specified in the assurance contract. |
| Maddy and routing | Record the selected pinned Maddy identity and security-release assessment. Preserve the verified baseline image and Cloudflare routing unless a separate upgrade or MX cutover is explicitly selected and tested. Record reviewed recipient dependencies and their configuration identities. |
| Backup and recovery | Record the actual off-VM destination, tested backup/encryption mechanism, separately recoverable decryption-key reference, cadence, finite retention, recovery-point/time objectives, and maximum deletion-backup age. Test the mechanism with fixtures before maintenance; the baseline backup phase must produce a verified production archive and timed isolated restore before hardening. |
| Independent notifications | Record the provider/channel, operator recipient, root-owned credential reference, external 993/443 monitoring, and delivered failure/recovery test evidence independent of dashboard availability. |
| Identities and resources | Record available service UID/GID, required group access, measured memory/tmpfs/hash/HTTP concurrency budgets, bounded lock/operation waits, free-space/inode thresholds, and whether swap is selected. Documentation-validation fixture values are never production defaults. |
| Maintenance | Record the approved rollout/reboot window, recurring backup/renewal interruption allowance, timer/container state before entry, backup staging space, restart-required state, checkpoints, and the operator's abort/rollback procedure. Include the host reboot and client interruption in that window. |

These are explicit release inputs and pass/fail gates. Their destinations, credentials, resource measurements, and maintenance approval cannot be inferred from SSH access. Keep deletion disabled until its backup and dependency checks pass, and Email OTP disabled until the current relay configuration has confirmed delivery. Retaining Cloudflare requires no MX change.

---
