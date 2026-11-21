# Plan: Maddy's built-in ACME instead of certbot (Stage 3, D-036). Repo only; not scheduled.

## What was checked (2026-11-21)
- The pinned Maddy 0.9.6 binary contains the `tls.loader.acme` loader and the libdns providers cloudflare, digitalocean, gandi, gcore, hetzner, namecheap and vultr (scanned the binary inside the running dashboard container).
- Maddy's reference page for the ACME loader (https://maddy.email/reference/tls-acme/, updated 2026-11-19): `challenge dns-01` is the only challenge; Cloudflare is configured as `dns cloudflare { api_token "..." }`; one `hostname`; certificates go to `store_path` (default `state_dir/acme`); `ca` and a staging `test_ca` fallback. The page does not say: renewal timing or backoff, reload without restart, the on-disk layout, whether several hostnames work, or whether a token can come from the environment.

## Why it is not worth switching now
1. The DNS token would sit inline in `maddy.conf`, which is mounted into the dashboard container and readable by group 10001. Today only root workers hold a Cloudflare token.
2. The dashboard serves HTTPS from an immutable certificate generation directory that certbot, the activation worker and the monitors manage (`host/renewal.py`, `host/certificates.py`, `host/tls_*`). Maddy's store layout is undocumented, so all of that would be reworked or the dashboard would need its own certificate.
3. Nothing is saved: certbot renews on `maddy-cert-renew.timer` (certificate valid until 2026-12-26) and the monitor already alerts on certificate expiry and backup age.
4. A local end-to-end spike is not possible with the existing fixtures: Pebble supports http-01, but Maddy only does dns-01 and would need a Cloudflare API mock for the libdns provider.

## If it is ever done
Spike on a throwaway hostname with the Let's Encrypt staging CA and a zone-scoped token; confirm renewal timing, file layout and reload behaviour first; keep certbot until two renewals of the new path succeeded; add a restore point and rollback (T3).
