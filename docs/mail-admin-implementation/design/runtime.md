# Runtime, container and permission contract

### Component 3: Deployment & Host Tuning

#### [NEW] `/opt/mail-admin/Dockerfile`

Use a multi-stage build performed outside the VM, targeting **Linux/amd64**:

1. **Frontend build**: Use Node.js **24 LTS**, pin a tested image digest and dependencies, and compile React + MUI with Vite. Node 20 is EOL; confirm support at implementation time using the [Node.js release schedule](https://nodejs.org/en/about/previous-releases).
2. **Runtime**: Use a tested, pinned Python 3.11-slim image with FastAPI and Uvicorn. Copy the compiled assets into the runtime image; omit Node, build tooling, and Docker CLI. Run one Uvicorn worker as a dedicated non-root UID/GID, serving HTTPS on container port **8443** with `/tls/fullchain.pem` and `/tls/privkey.pem`.

Transfer the tested image to the VM without rebuilding it there. Record its immutable image ID/digest and retain the previous tested image for rollback. Obtain approval before introducing dependencies or external build/registry services beyond those already authorized.

#### [NEW] `/opt/mail-admin/docker-compose.yml`

Set `MAIL_ADMIN_IMAGE` to the tested, locally loaded runtime image, allocate a dedicated host service UID/GID, and set `MAIL_ADMIN_MEMORY_LIMIT` and `MAIL_ADMIN_TMPFS_LIMIT` from measured usage before rendering Compose. Set `MAIL_ADMIN_TLS_DIRECTORY` to the absolute path of one validated, immutable certificate generation, as described in the [certificate procedure](../operations/certificates.md). Include temporary storage in the memory budget. Verify Linux/amd64 architecture and the resolved image ID against the deployment manifest. `pull_policy: never` prevents an unexpected registry pull; any approved registry-based deployment must define its own pinned pull procedure. These fields follow the [Compose service reference](https://docs.docker.com/reference/compose-file/services/).

```yaml
services:
  mail-admin:
    image: ${MAIL_ADMIN_IMAGE:?Set tested runtime image}
    pull_policy: never
    platform: linux/amd64
    container_name: mail-admin
    user: "${MAIL_ADMIN_UID:?Set service UID}:${MAIL_ADMIN_GID:?Set service GID}"
    mem_limit: ${MAIL_ADMIN_MEMORY_LIMIT:?Set measured memory limit}
    restart: unless-stopped
    read_only: true
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    logging:
      driver: local
      options:
        max-size: "10m"
        max-file: "3"
    ports:
      - "443:8443"
    volumes:
      - type: bind
        source: ./data
        target: /app/data
        bind:
          create_host_path: false
      - type: bind
        source: ${MAIL_ADMIN_TLS_DIRECTORY:?Set validated immutable certificate generation}
        target: /tls
        read_only: true
        bind:
          create_host_path: false
      - type: bind
        source: /run/mail-admin-helper
        target: /run/mail-admin-helper
        read_only: true
        bind:
          create_host_path: false
      - type: bind
        source: /etc/mail-admin/secrets
        target: /run/secrets/mail-admin
        read_only: true
        bind:
          create_host_path: false
      - type: bind
        source: /etc/mail-admin/installation.json
        target: /run/mail-admin-installation.json
        read_only: true
        bind:
          create_host_path: false
      - type: tmpfs
        target: /tmp
        tmpfs:
          size: ${MAIL_ADMIN_TMPFS_LIMIT:?Set measured temporary filesystem limit}
          mode: 01777
    environment:
      - ADMIN_EMAIL=rsalehin24@gmail.com
      - PRIMARY_DOMAIN=rsalehin24.me
      - MAIL_HOST=mail.rsalehin24.me
      - OTP_FROM_EMAIL=mail@rsalehin24.me
```

Prepare the data directory for the service UID/GID. Keep helper code, its socket directory, secret files, and `/etc/mail-admin/installation.json` root-owned; grant the dedicated group only the access it needs. The socket must be `0660` in a `0750` directory, and secret files/installation marker must be `0640` or stricter. Match the socket group to the container GID and verify unauthorized local users cannot connect. Provision the marker and its expected database identity before startup. The Compose example caps the dashboard's rotated logs at three 10 MB files using the [Docker local logging driver](https://docs.docker.com/engine/logging/drivers/local/).

Provision every bind source deliberately and verify its expected type, ownership, and contents. Long syntax with `create_host_path: false` rejects missing sources rather than creating directories; application checks still reject a missing database, incorrect marker, unreadable key, or empty certificate mount. Keep the helper directory inode stable across restarts as specified in the [helper contract](helper.md).

The existing TLS private key is `0640`; grant the dedicated group read access without making it world-readable. Preserve that ownership/group and mode in every certificate generation, including directory traversal access for the dedicated group. Verify the non-root container can read the certificate and key, write only its data/temporary paths, and use only the helper's permitted protocol. Confirm host and GCP firewall ingress for HTTPS before external rollout; the absence of a current 443 listener does not establish the firewall policy.

#### [MODIFY] Existing Maddy Data Permissions

Restrict `/opt/maddy/data` and its content after taking the baseline backup and identifying required daemon/operator access. Use `0700` data/message directories and `0600` databases, WAL/shared-memory files, and message files, with ownership that permits the existing Maddy process and authorized operators through sudo. Scope changes to the data tree and handle symlinks explicitly; do not blindly change ownership or unrelated TLS/configuration paths.

Apply `0077` **inside** the container for both Maddy startup and every helper CLI invocation; a host-service umask does not propagate through `docker exec`. Install a `root:root`, mode `0755` static launcher at `/usr/local/libexec/mail-admin/maddy-launcher` on the host and bind it read-only into Maddy at `/usr/local/libexec/mail-admin-maddy`:

```sh
#!/bin/sh
set -eu
umask 0077
exec /bin/maddy "$@"
```

The launcher sets permissions and forwards argument boundaries to one fixed executable; it interprets no request text and requires no additional runtime/compiler dependency. Keep the file and all writable parent directories under root control. Preserve the verified Maddy configuration by setting its entrypoint to `["/usr/local/libexec/mail-admin-maddy", "-config", "/data/maddy.conf"]` and its command to `["run"]`; retain all existing mounts/environment/ports and the recorded image identity. The helper invokes `docker exec -i maddy /usr/local/libexec/mail-admin-maddy` followed by its fixed validated CLI arguments. Document the same launcher for operator CLI mutations so they cannot accidentally recreate permissive files.

Changing the Maddy entrypoint/mount requires container recreation in the maintenance window; it does not authorize an image upgrade. Verify the daemon's process umask and helper-path behavior in isolation, then verify newly delivered messages, newly created accounts/folders, and recreated SQLite companion files retain restrictive modes. Denied read/traversal checks as `nobody` must pass while normal SMTP delivery, IMAP reads/writes, credential management, and backups still succeed. The dashboard UID must have no direct host access to Maddy data.

#### [MODIFY] Host Swap Configuration

If swap is added, first inspect active swap, filesystem support, free space, and any existing `/swapfile`; never overwrite an existing file blindly. Create and activate a **1 GiB** swapfile with mode `0600`, add `/swapfile none swap sw 0 0` to `/etc/fstab` exactly once, and validate the resulting configuration. Check persistence after a reboot in an agreed maintenance window. Record swap usage during load testing; it is a buffer, not a guarantee against memory exhaustion.
