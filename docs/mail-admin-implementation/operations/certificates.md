# Certificate activation and maintenance

Lock ownership and order are defined in the [helper contract](../design/helper.md).

#### [MODIFY] `/opt/maddy/renew-cert.sh`

Replace the single-container restart logic with a host-controlled deployment step:

1. Acquire the renewal-run lock for the whole invocation, then acquire the Maddy maintenance lock only for installation/consumer activation, following the fixed lock order in the [helper contract](../design/helper.md). This prevents overlapping runs and excludes ACME writes from consistent backups without holding the Maddy lock during network acquisition. Preserve the existing certificate pair and leave it installed when acquisition or validation fails.
2. Run a tested, pinned Certbot image using the existing `/opt/maddy/letsencrypt` and `/opt/maddy/letsencrypt_lib` volumes and standalone HTTP-01 challenge on port 80. Let `certbot renew` determine when renewal is due; remove routine forced renewal and the separate 32-day gate. Confirm HTTP-01 reachability and port availability.
3. Distinguish a successful no-op from an actual certificate change or pending activation. Certbot may exit successfully without renewing. Check the complete desired certificate/key generation and each deployed consumer's mounted/served generation; a matching certificate fingerprint alone does not establish pair integrity or successful activation. Keep host deployment/Docker access out of the Certbot container. See [Certbot renewal behavior](https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates).
4. Stage each certificate/key pair in a new root-controlled directory under `/opt/maddy/tls/generations/`; validate key matching, intended hostname, validity period, trust chain, and group/mode access. Flush both files, the generation directory, and its parent directory to durable storage before publishing that generation. Never overwrite files in a published generation or replace the parent of a live bind mount. Atomically publish and durably flush a root-owned desired-generation record under `/var/lib/mail-admin-certificates/`, outside web-writable mounts; keep the previously active generations for rollback.
5. Recreate Maddy and, when deployed, `mail-admin` from their recorded Compose/image configuration, binding the explicit immutable generation directory to their existing `/data/tls` and `/tls` paths. Set the dashboard's `MAIL_ADMIN_TLS_DIRECTORY` accordingly and record the equivalent Maddy mount selection; do not mount a mutable `current` symlink. A restart alone does not change a container's pinned bind source. Attempt each deployed consumer independently so one failure cannot skip the other. Record desired and confirmed-active generation per consumer; a never-deployed dashboard does not break Maddy-only renewal, while a missing previously deployed consumer remains pending. Report failed activation with a non-zero result and safe diagnostics; recover pending activation from durable state on the next run even when Certbot issues nothing new.
6. Surface renewal failure and certificate expiry through authenticated diagnostics and the independent alert path below. Verify the certificate actually served on 993 and, when deployed, 443; an updated file alone is insufficient.

#### Maintenance Entry and Exit

Complete this entry **before the first production backup, permission change, or Maddy recreation**, including installation of the umask launcher. The later TLS cutover continues the same maintenance operation. Normal scheduled backups after migration use the cooperating lock contract above.

1. Finish the release build, isolated acceptance/recovery fixtures, deployment record, and rollback package before entering the production window. Record the current timer enabled/active state, unit/script configuration, container lifecycle state, image/configuration/mount identities, and verified certificate pair.
2. Stop and temporarily disable the legacy timer so scheduling stays paused across interruption/reboot. Check its service and known manual invocations; wait for any active run to finish and verify its outcome. Do not interrupt a script halfway through copying the old pair. Prevent manual invocations during the window. An unknown outcome stops maintenance and requires operator reconciliation: the legacy script uses neither lock.
3. Provision/validate both lock files, drain existing helper/account work if present, and acquire renewal-run before Maddy maintenance. Check unresolved command outcomes before collecting a backup or changing the container. Pause dashboard writes for snapshots if an existing dashboard is installed. Keep legacy scheduling disabled through baseline backup, hardening, and mount/script migration; release/reacquire locks only at the documented phase boundaries.
4. If aborting before mount/script migration, verify the original mount/pair/script still match, restore previously running services, verify served TLS, then restore the recorded timer state. If any mount/script selection changed, use the matched rollback procedure below instead. On unknown command completion, keep scheduling paused and notify the operator; do not resume automatically.

#### Coordinated TLS Cutover

Complete the initial mount/renewal migration after the baseline backup and host preparation, before public dashboard startup:

1. Use the already-tested generation-aware implementation and rollback package. Stage and validate the initial production generation after maintenance entry has drained the legacy writer; keep the live legacy mount and script paired until the coordinated switch.
2. Confirm maintenance entry preceded the baseline backup and hardening, the timer remains stopped/disabled, and no legacy/manual run has resumed. Recheck these conditions after interruption; an earlier inactive snapshot is insufficient.
3. Under renewal-run then Maddy maintenance locks, drain helper CLI work, install the tested generation-aware script/service and daily timer configuration, and reload systemd while scheduling remains stopped/disabled. Validate the selected complete pair and migrate each currently deployed consumer to its explicit generation mount. Record the matching mount selection and renewal implementation together. Do not postpone the script/service change to a later rollout step. Release both locks after this phase so the new renewal service can acquire them normally during verification.
4. Exercise the newly loaded renewal/activation path and verify the certificate served on 993 and on 443 if the dashboard is already deployed. A never-deployed dashboard does not block the Maddy-only cutover. Enable/start the timer only after those checks pass; confirm the active service uses the new implementation and any missed-run activation invokes that implementation.
5. If cutover fails, keep scheduling paused while restoring the matching TLS mount selection, a validated unexpired pair, renewal script/service, and timer configuration as one rollback set. Verify served certificates before resuming scheduling. Never re-enable the legacy writer while a consumer remains pinned to a generation mount.

After interruption or reboot, a consumer must still mount one complete old or new generation. Ignore unpublished/incomplete staging directories, validate the desired record and pair before activation, and retain pending status until served certificates are verified. Never reconstruct a pair by mixing files from different generations. Retention must preserve every generation referenced by a consumer, rollback record, or pending activation. Back up the certificate-generation/activation state with the TLS data and test recovery before accepting deployment.

#### [MODIFY] `/etc/systemd/system/maddy-cert-renew.timer`

Replace the existing monthly schedule with a daily check at 03:00 UTC and retain missed-run handling:

```ini
[Timer]
OnCalendar=*-*-* 03:00:00 UTC
Persistent=true
```

Apply the timer configuration and reload systemd while scheduling remains stopped/disabled during the coordinated cutover above. Enable/start the timer only after the matching renewal implementation/mounts and served certificates have been verified. A failed renewal remains visible and is retried on the next daily run, rather than waiting until after a certificate might expire. Test ACME renewal with `certbot renew --dry-run` in a controlled window and test interrupted publication/activation separately; a dry run does not replace the production certificate.

#### Independent Renewal Alerts

Select and provision an operator alert channel before deployment. Failed renewal or consumer activation must notify the operator without calling the dashboard API or requiring its HTTPS endpoint to work. Add a daily certificate-expiry check with warnings at 21 and 7 days remaining, deduplicate repeated alerts, retry failed notifications, and report recovery. Keep host-side alert configuration/credentials root-owned and independent of the dashboard database/process; never include private keys or authentication secrets in notifications.

Use an approved external monitor or existing monitoring system to check certificate expiry and reachability on 993 and 443 independently of the VM. Confirm notification delivery while the dashboard is stopped or its certificate is invalid, and cover a host outage through the external check. Choose the provider/channel explicitly; no external service is provisioned by this plan. A failure recorded only in systemd or dashboard diagnostics does not meet this requirement.

Use the same independent path for failed/missed scheduled backups, deferred/failed maintenance, and low host disk/inode capacity. Choose warning/critical thresholds and sufficient free space for backup staging, SQLite/WAL growth, and retained images before rollout. Fail mutations safely if durable recording cannot complete; never recover space by automatically deleting mail. Test these notifications with isolated fixtures and document the operator response.
