# Architecture and resource boundaries

## Architecture & Resource Footprint

```mermaid
flowchart TD
    subgraph Devices ["Responsive Client Access"]
        AndroidPhone["Android Smartphone\n• Numeric OTP keypad\n• Card layout\n• Fullscreen modals"]
        LaptopDesktop["Laptop / Desktop\n• Data table & sorting\n• Side-by-side tabs\n• Keyboard navigation"]
    end

    subgraph Server ["GCP VM 35.237.68.175 (953 MiB RAM / 28 GiB Disk)"]
        subgraph AdminContainer ["Docker: mail-admin (Host 443 to container 8443)"]
            FastAPI["FastAPI Backend\n(Static files + REST API)"]
            AuthEngine["Auth & 2FA Engine\n• Argon2 Hashing\n• TOTP (pyotp)\n• Email OTP"]
            SQLite["SQLite: admin_data.db\n• Metadata, 2FA secrets\n• Relay settings (Brevo)"]
            ConfigGen["Client Config Engine"]
            HealthCheck["DNS & Mail Diagnostics"]
        end

        HostHelper["Restricted Host Helper\n• Private Unix socket\n• Fixed Maddy operations\n• Validated arguments"]
        HelperJournal["Root-owned Operation Journal\n/var/lib/mail-admin-helper/"]

        subgraph MaddyContainer ["Docker: maddy (Ports 25, 993)"]
            MaddyDaemon["Maddy Mail Daemon"]
            MaddyCreds["credentials.db"]
            MaddyIMAP["imapsql.db"]
        end

        Swap["1 GiB Persistent Swap\n(Optional memory buffer)"]
        Certs["/opt/maddy/tls/\n(Let's Encrypt Certs)"]
        SecretFiles["Persistent Secret Files\n(Outside SQLite and source)"]
    end

    subgraph External ["External Services"]
        BrevoSMTP["Brevo SMTP Relay\n(smtp-relay.brevo.com:587)"]
        GmailInbox["rsalehin24@gmail.com\n(Receives OTP Codes)"]
    end

    AndroidPhone -->|HTTPS Port 443| FastAPI
    LaptopDesktop -->|HTTPS Port 443| FastAPI
    FastAPI --> AuthEngine
    FastAPI --> SQLite
    FastAPI --> ConfigGen
    FastAPI --> HealthCheck
    FastAPI -->|Restricted Unix socket protocol| HostHelper
    HostHelper --> HelperJournal
    HostHelper -->|Fixed Docker Exec commands| MaddyContainer
    AuthEngine -->|Sends Email OTP| BrevoSMTP
    BrevoSMTP -->|Delivers Code| GmailInbox
    Certs -->|Mounted| AdminContainer
    SecretFiles -->|Read-only mount| AdminContainer
```

### Resource Breakdown

* **Runtime memory**: Measure idle usage and peak usage during password hashing, concurrent login attempts, diagnostics, and mailbox operations. Include the helper and transient Docker processes. Choose the container memory limit and hashing concurrency from those measurements while preserving capacity for Maddy and the host.
* **Build memory**: Build the complete runtime image locally or in approved CI for **Linux/amd64**. Do not run frontend or image builds on the mail VM.
* **Disk**: Measure the runtime image, retained rollback image, data, logs, and backups; the earlier 200 MB total estimate is not an acceptance criterion.
* **Swap**: If provisioned, use a **1 GiB** swapfile with restrictive permissions and a validated persistent `/etc/fstab` entry. Swap does not guarantee stability or replace load testing and memory limits.

---
