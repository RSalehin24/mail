# Backups and recovery

#### Consistent Backups and Restore Procedure

- Before deployment or an upgrade, back up both Maddy databases, message storage, configuration, Compose files, the existing deployed TLS pair, existing ACME/renewal state in `/opt/maddy/letsencrypt` and `/opt/maddy/letsencrypt_lib`, renewal units/script, and the current image identity. Once provisioned, also include referenced TLS generations and `/var/lib/mail-admin-certificates/` activation state, dashboard data, installation marker, matching secret files, deployment record, helper operation journal/policy, umask launcher, service units, tmpfiles/boot-ordering configuration, and independent alert configuration. Restrict/encrypt all private-key material. Record a compatible image/helper/protocol/schema/launcher set and per-consumer TLS generation for rollback.
- Use a quiesced backup in the approved maintenance window. Complete maintenance entry before the first baseline backup; for later backups, acquire renewal-run then Maddy maintenance so Certbot/ACME-state writers, publication, helper commands, and container recreation are excluded. Drain helper work, record inventories, and stop Maddy plus any deployed dashboard before capturing their databases/message files/authentication state. Run no Maddy CLI during capture and do not copy active SQLite/WAL files independently. Record which containers were running; restart those after the complete consistent staging copy and verify service recovery. Release locks after capture/recovery. Transfer and validate the immutable staging copy off-VM without holding services stopped for the transfer. Include staging capacity and downtime in the release record.
- The first baseline covers the components actually deployed at that time. Prove restoration of that Maddy state before hardening or recreation; test dashboard authentication/key recovery beforehand using the built candidate and isolated fixtures. After real dashboard provisioning/enrollment, take a complete backup including its actual keys, marker, and journal before deployment acceptance. Do not require restoration of nonexistent production dashboard state before its runtime has been built.
- Restrict and encrypt backup access, keep a recoverable copy outside the VM, and record its snapshot identifier, timestamp, mailbox inventory, covered databases/message paths, and archive integrity. Publish a root-owned trusted manifest only after the encrypted off-VM copy has been verified. Document recovery of the backup-decryption key separately from that archive and the VM itself.
- Define and provision an ongoing backup schedule, retention, recovery-point/time objectives, and the maximum backup age allowed before permanent deletion. Check scheduled success/age independently and alert on failures or missed runs. A pre-deployment backup alone is not the ongoing recovery procedure.
- Before deletion, the helper checks that the archive is available/verified, includes the selected current mailbox in both stores and its message storage, and does not predate its recorded creation/recreation. If coverage cannot be established, require a fresh consistent backup. Display the trusted recovery cutoff and require explicit acknowledgment that messages arriving after it are outside that backup; otherwise refuse deletion. Browser-supplied timestamps or claims of coverage are insufficient.
- Prove restoration in an isolated environment using the recorded runtime version, all required databases/message files, and the matching keys. Verify account inventory, fixture messages, admin login/2FA, and decryption of relay settings. Block permanent deletion until this procedure has passed and a qualifying backup is available.
- Include recovery from failed storage deletion: restore fixture message bodies and their database references, not merely an account row. Prove that recovery preserves other accounts/newer mail and makes the selected backup cutoff explicit. Do not assume a rolled-back SQL transaction implies that message files survived.
- Before reopening any restored dashboard, invalidate all restored sessions, setup/login/reauthentication/replacement challenges, Email OTP records, and recent-reauthentication grants; replace the entire recovery-code set and invalidate relay/OTP readiness until retested. Verify the database/installation marker and runtime/schema/protocol match, restore restrictive permissions, and reconcile helper operation state against actual Maddy inventories before enabling mutations. Post-restore reconciliation is observational: do not automatically replay old create/reset/delete requests; require fresh authorization for still-desired changes. A backup can predate password/factor changes, so establish the intended master password and TOTP seed with the operator; use the SSH-only recovery utility to replace them if their trusted state cannot be established. Do not reset active TOTP replay protection or reuse consumed recovery codes as a shortcut to regain access. Test that cookies and codes invalidated after the backup cannot authenticate after restoration.

The production Restic policy keeps the latest verified snapshot for each calendar
day in Asia/Dhaka within the rolling seven-day window. The existing 03:00 Dhaka
schedule is unchanged. Manual backups replace an earlier same-day restore point
only after full extraction and integrity verification. Unresolved restores pin
both their source and verified safety snapshot. Successful restoration requires a
verified post-restore backup before safety copies can be retired.

Verification records and complete retention decisions live under the root-owned
Restic state directory's `retention/` subdirectory. Cleanup failures are recorded
separately from successful backup publication. Native `forget` and `prune` run
under the shared backup and recovery locks; never delete R2 objects directly.
The explicitly requested initial cleanup uses `python3 -m host.initial_cleanup`
from the installed host tools and refuses while any recovery work is unresolved.
It freshly restores/verifies the newest snapshot, audits retired staging and
repository IDs in `retired-references.json`, and retains exactly that snapshot.
Keep this audit record with the repository state: it retires obsolete baseline
and rollback pins without altering historical qualification evidence.

Settings contains Security, Outgoing mail provider, and Backup & Recovery.
Deletion policy and protections are on Deleted Mailboxes under Configure and
Protected Mailboxes; the default tab lists deleted mailboxes. Backup & Recovery
opens Backups, with complete operation history in Logs. URL tab parameters
survive refresh; legacy deletion/protection Settings links redirect.
