# Runbook (one page)

Live host: GCP VM `rsalehin24-mail` (us-east1-c), SSH `deployer@mail.rsalehin24.me` (key only; use `-o HostKeyAlias=<ip>` if the address changed before DNS did). Two containers in `/opt/mail-admin` (one compose project): `maddy` and `mail-admin`, both uid 10001, capabilities dropped. Root work runs from systemd units that import `/usr/local/lib/mail-admin`.

## Deploy
1. `make check` and `make build` locally; `docker tag mail-admin:candidate mail-admin:<name>`; `docker save mail-admin:<name> | gzip | ssh ... 'gunzip | docker load'`.
2. Library changes: copy the changed files into `/usr/local/lib/mail-admin/{host,helper,shared}` (root:root 0644). There is no recovery proof any more (D-078); the tests are the gate.
3. Compose: `cd /opt/mail-admin; set -a; . /root/cutover-20261121/stage2.env; set +a; export MAIL_ADMIN_IMAGE=mail-admin:<name> MAIL_ADMIN_DNS_TOKEN="$(cat /etc/mail-admin/dns-update.token)"; docker compose up -d --no-deps mail-admin` (never `--remove-orphans`; the token is only for the `maddy` service and must be exported whenever compose is run).
4. Keep the previous image tag for rollback and write a small rollback script next to `stage2.env` in `/root/cutover-20261121/` (the current one is `rollback-v13.sh`, back to `mail-admin:stage12`, including the recovery files, proof and unit). Dashboard-only deploys need no restore point; changes to host files do (take a backup and keep copies of the replaced files).

## Restore
- Latest snapshot check without touching mail: `ResticRepository(ResticProcess(HostContext.load().deployment), time.time).verify({"reference", "content_sha256"})` from the manifest, as root in `/usr/local/lib/mail-admin` (about 25 s; restores to a scratch folder and deletes it).
- Mailbox recovery and additive restore: dashboard Backups and Recovery pages. Full-server rehearsal (done 2026-11-21): `ResticRepository.restore(reference, staging/rehearsal-restore)` as root, chown the `maddy/` folder to 10001, copy `maddy_configuration` to `maddy/maddy.conf`, run the pinned Maddy image offline (`--network none`, uid 10001, no capabilities, entrypoint `/usr/local/libexec/mail-admin-maddy`) with `creds list` and `imap-acct list`, compare message counts in `imapsql.db` with the live one, then delete the folder. The snapshot holds Maddy data, config, compose, certificates, dashboard database, helper state and operator files (about 200 MB).
- Roll back the last dashboard deploy: `/root/cutover-20261121/rollback-dashboard.sh` (as root; needs image `mail-admin:stage11`, which stays on the VM). Roll back the v13 deploy (password-only confirmation, queue code): `/root/cutover-20261121/rollback-v13.sh` (needs `mail-admin:stage12`). Undo the DNS update service: `/root/cutover-20261121/rollback-dns-update.sh`.

## Rotate secrets
- Certificate and DNS token: `/etc/mail-admin/dns-update.token` is read by the DNS-update service and passed to the Maddy container as `CLOUDFLARE_API_TOKEN` at `docker compose up`; rotating it means installing the new file, then recreating `maddy`.
- Cloudflare forwarding token: `/etc/mail-admin/secrets/cloudflare-forwarding.token` (root-only); DNS update token: `/etc/mail-admin/dns-update.token` (0600 root, create with a minimal "Zone, DNS, Edit" token; install through ssh stdin, never paste in chat).
- Dashboard application keys: `/etc/mail-admin/secrets` (root:mail-admin 0750). Rotating them signs everyone out.
- Backup (restic/R2) keys: rotate in R2, update the backup configuration under `/etc/mail-admin`, run a backup, confirm `verified`.

## What runs when
| Unit | When | Does |
|---|---|---|
| `mail-admin-backup.timer` | 03:00 Asia/Dhaka | Backup to R2 (Restic), verified by a restore to scratch |
| `mail-admin-backup-control` `.path` + `.timer` | on request file; 5 min fallback | Dashboard-requested backups and restores |
| `mail-admin-recovery-expire` `.timer` | daily | Removes deleted-mailbox archives whose retention ended (`host.expire_recovery`) |
| `mail-admin-maddy-restart.path` | on request file | Restart Maddy after a relay change |
| `mail-admin-forwarding.timer` | 30 s | Sync forwarding with Cloudflare |
| `mail-admin-monitor.timer` / `-independent-monitor` / `-tls-observation` | 5 min / 15 min / 2 min | Capacity, backup age, certificate expiry alerts |
| Maddy itself | continuous | Issues and renews the TLS certificate (Let's Encrypt, DNS-01 through Cloudflare, store `/opt/maddy/data/acme`); the dashboard restarts itself within 6 hours of a renewal |
| `mail-admin-dns-update.service` | every boot | Point the `mail` A record at the VM's current IP |

## OS updates (done 2026-11-21, D-062, D-063)
`apt-get update`, then `apt-get -y -o Dpkg::Options::=--force-confold upgrade` as root, run in the background with a log because it takes minutes on the e2-micro. A Docker or containerd upgrade restarts both containers once. Install a newer kernel with `apt-get install linux-gcp linux-image-gcp linux-headers-gcp`, then `sudo systemctl reboot` (about 2 minutes, same IP). Afterwards check the containers, `systemctl --failed`, HTTPS, ports 25, 587, 993 and 443, and the latest backup. Remove a superseded kernel by name with `apt-get purge` once the new one has booted.

## Cost rules
External IP on the STANDARD tier, one 30 GB pd-standard disk, no static IP, snapshots, NAT or load balancer, egress under 1 GB a month, $0.01 budget alert. Never switch the IP to PREMIUM.

## Backup pause marker
`/var/lib/mail-admin-helper/consumers.json` records which consumers a backup or offline recovery stopped on purpose; a crash leaves it in `maintenance`, which blocks the next backup until the backup-control reconcile job restarts the consumers. Reconcile from the dashboard Backups page, or file a `reconcile` job for the stuck backup.

## Rebuild a blank VM from a snapshot (rehearsed 2026-11-21 in a sandbox on the live VM)
The snapshot holds `maddy` (data, queue and the certificate store `acme/`), `maddy_configuration`, `maddy_compose`, `dashboard` (`admin_data.db`), `helper` (journal, queues, records, recovery key), `operator` (`/etc/mail-admin` with secrets), `alerts`, `host_tools` (the root library), `units`, `tmpfiles`, `docker_ordering` and `launcher`. It does not hold Docker, the images or DNS.
1. New e2-micro in the free-tier region with a STANDARD-tier ephemeral IP; install Docker; create the `mail-admin` user with uid and gid 10001. The DNS update service fixes the A record at first boot once its files are back (step 3).
2. Get the restic repository settings and keys from `operator` of any copy you hold (R2 credentials, restic password), restore the latest snapshot with `ResticRepository.restore` into a scratch folder, then copy each role back with `cp -a` (owners and modes are recorded): `maddy` to `/opt/maddy/data`, `maddy_configuration` to `/opt/maddy/config/maddy.conf`, `dashboard` to `/opt/mail-admin/data`, `helper` to `/var/lib/mail-admin-helper`, `operator` to `/etc/mail-admin`, `host_tools` to `/usr/local/lib/mail-admin`, `launcher` to `/usr/local/libexec/mail-admin-maddy`, `alerts` to `/var/lib/mail-admin-alerts`, `maddy_compose` to `/opt/mail-admin/docker-compose.yml`, `units` and `tmpfiles` to systemd and `/etc/tmpfiles.d`.
3. Images: pull the pinned Maddy image by digest; build the dashboard image from the repository (`make build`) and load it; then `docker compose up -d` as in Deploy step 3 and enable the units and timers; run `systemd-tmpfiles --create`; the boot service updates `mail.rsalehin24.me`.
4. Check: ports 25, 587, 993 and 443 present the restored certificate (issued by Maddy; renewal continues from `acme/`), the dashboard answers, 9 mailboxes, then run a backup.
Rehearsal result: a second Maddy and dashboard started only from the snapshot (offline, owners restored) served the same certificate fingerprint as production, the dashboard answered 200, and 9 mailboxes and 153 messages matched.

## Forcing a new external IP (test of the DNS update)
Stop the VM, `gcloud compute instances delete-access-config` then `add-access-config --network-tier=STANDARD` (never an address reservation), start it, and check the Cloudflare record and the ports within a minute.

## Deleted mailboxes (D-078)
- Deleting is a direct dashboard action: the dashboard archives the mailbox (encrypted, in `/var/lib/mail-admin-helper/recovery-points`), removes it from Maddy and records it, while Maddy keeps running. There is no queue and no worker.
- Restore a deleted mailbox (rare, on the VM as root): `sudo python3 -m host.restore_mailbox <record-id>` (the id is shown on the dashboard's Deleted page; the command asks for the new mailbox password). Maddy keeps running.
- Expiry: `mail-admin-recovery-expire.timer` runs daily. Run it by hand with `sudo python3 -m host.expire_recovery`.
- The dashboard user must own `recovery-points/` and `recovery.key` (D-078): `sudo chown -R mail-admin:mail-admin /var/lib/mail-admin-helper/recovery-points /var/lib/mail-admin-helper/recovery.key`.
