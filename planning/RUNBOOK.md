# Runbook (one page)

Live host: GCP VM `rsalehin24-mail` (us-east1-c), SSH `deployer@mail.rsalehin24.me` (key only; use `-o HostKeyAlias=<ip>` if the address changed before DNS did). Two containers in `/opt/mail-admin` (one compose project): `maddy` and `mail-admin`, both uid 10001, capabilities dropped. Root work runs from systemd units that import `/usr/local/lib/mail-admin`.

## Deploy
1. `make check` and `make build` locally; `docker tag mail-admin:candidate mail-admin:<name>`; `docker save mail-admin:<name> | gzip | ssh ... 'gunzip | docker load'`.
2. Library changes: copy the changed files into `/usr/local/lib/mail-admin/{host,helper,shared}` (root:root 0644). Files listed in `helper.recovery.QUALIFIED_FILES` need a new recovery proof first (`tools/record_recovery_qualification.py`, owner reviews, install to `/var/lib/mail-admin-helper/recovery-qualified.json`, root:10001 0640).
3. Compose: `cd /opt/mail-admin; set -a; . /root/cutover-20261121/stage2.env; set +a; export MAIL_ADMIN_IMAGE=mail-admin:<name> MAIL_ADMIN_DNS_TOKEN="$(cat /etc/mail-admin/dns-update.token)"; docker compose up -d --no-deps mail-admin` (never `--remove-orphans`; the token is only for the `maddy` service and must be exported whenever compose is run).
4. Keep the previous image tag for rollback (`mail-admin:pre-stage2` is the pre-Stage-2 image).

## Restore
- Latest snapshot check without touching mail: `ResticRepository(ResticProcess(HostContext.load().deployment), time.time).verify({"reference", "content_sha256"})` from the manifest, as root in `/usr/local/lib/mail-admin` (about 25 s; restores to a scratch folder and deletes it).
- Mailbox recovery and additive restore: dashboard Backups and Recovery pages. Full-server rehearsal (done 2026-11-21): `ResticRepository.restore(reference, staging/rehearsal-restore)` as root, chown the `maddy/` folder to 10001, copy `maddy_configuration` to `maddy/maddy.conf`, run the pinned Maddy image offline (`--network none`, uid 10001, no capabilities, entrypoint `/usr/local/libexec/mail-admin-maddy`) with `creds list` and `imap-acct list`, compare message counts in `imapsql.db` with the live one, then delete the folder. The snapshot holds Maddy data, config, compose, certificates, dashboard database, helper state and operator files (about 200 MB).
- Whole stack rollback to the pre-Stage-2 setup: `/root/cutover-20261121/stage3/rollback3.sh` (as root).

## Rotate secrets
- Certificate and DNS token: `/etc/mail-admin/dns-update.token` is read by the DNS-update service and passed to the Maddy container as `CLOUDFLARE_API_TOKEN` at `docker compose up`; rotating it means installing the new file, then recreating `maddy`.
- Cloudflare forwarding token: `/etc/mail-admin/secrets/cloudflare-forwarding.token` (root-only); DNS update token: `/etc/mail-admin/dns-update.token` (0600 root, create with a minimal "Zone, DNS, Edit" token; install through ssh stdin, never paste in chat).
- Dashboard application keys: `/etc/mail-admin/secrets` (root:mail-admin 0750). Rotating them signs everyone out.
- Backup (restic/R2) keys: rotate in R2, update the backup configuration under `/etc/mail-admin`, run a backup, confirm `verified`.

## What runs when
| Unit | When | Does |
|---|---|---|
| `mail-admin-backup.timer` | 03:00 Asia/Dhaka | Backup to R2 (Restic), verified by a restore to scratch |
| `mail-admin-backup-control` / `-recovery` `.path` + `.timer` | on request file; 5 min fallback | Dashboard-requested backups, restores, mailbox recovery |
| `mail-admin-maddy-restart.path` | on request file | Restart Maddy after a relay change |
| `mail-admin-forwarding.timer` | 30 s | Sync forwarding with Cloudflare |
| `mail-admin-monitor.timer` / `-independent-monitor` / `-tls-observation` | 5 min / 15 min / 2 min | Capacity, backup age, certificate expiry alerts |
| Maddy itself | continuous | Issues and renews the TLS certificate (Let's Encrypt, DNS-01 through Cloudflare, store `/opt/maddy/data/acme`); the dashboard restarts itself within 6 hours of a renewal |
| `mail-admin-dns-update.service` | every boot | Point the `mail` A record at the VM's current IP |

## Cost rules
External IP on the STANDARD tier, one 30 GB pd-standard disk, no static IP, snapshots, NAT or load balancer, egress under 1 GB a month, $0.01 budget alert. Never switch the IP to PREMIUM.
