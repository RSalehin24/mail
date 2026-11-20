# Mail Admin

Self-hosted mail for `rsalehin24.me`: the Maddy mail server and the Mail Admin
dashboard on one small VM. Design contract: [docs/mail-admin-implementation](docs/mail-admin-implementation/README.md).
Plans and decisions: [planning/](planning/STATUS.md) (start with `STATUS.md`).

The HTTPS web application runs without Docker access or a Maddy data mount.
A root Unix-socket helper owns mailbox changes; root host tools own TLS, backups
and recovery. Production changes need an owner-approved plan and a restore point.

Mail clients use `mail.rsalehin24.me` for IMAP 993/TLS and SMTP 587/STARTTLS,
with the full mailbox address and mailbox password for both. Enable saving sent
messages in the server IMAP Sent folder. See [SMTP submission and Sent storage](verification/smtp-submission.md).
Forwarding controls are in the main navigation and mailbox Configuration pages;
see [forwarding status](verification/forwarding-implementation-status.md).

Paths: `frontend/` (React/MUI), `backend/` (API/domain/data), `shared/` (validators),
`helper/` (private Unix service), `host/` (root lifecycle tools and systemd units),
`tests/` (application assertions), `tools/` (test runners and fixture tooling).

## Setup

Use Python 3.15 and Node 24 LTS. Image builds need Docker Engine, the Buildx plugin
and Docker Compose. See also the [Linux setup notes](verification/linux-continuation.md).

```sh
python3.15 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
npm --prefix frontend ci --ignore-scripts
```

The dependency locks are generated from package-manager resolution, never edited.
After an approved change to `requirements.in` or `requirements-dev.in`:

```sh
mkdir -p build
.venv/bin/python -m pip install --dry-run --ignore-installed -r requirements.in --report build/runtime-resolution.json
.venv/bin/python tools/lock_dependencies.py build/runtime-resolution.json requirements.lock
```

(Resolve `requirements-dev.in` the same way into `requirements-dev.lock`.)

## Checks

| Command | What it runs |
|---|---|
| `make lint` `make typecheck` | Ruff, strict mypy, TypeScript |
| `make unit` `make integration` | unit and API integration tests |
| `make reads` | frontend read-path tests |
| `make build` | the runtime image (pinned Node 24 and Python 3.15 stages, hash-locked wheels) |
| `make browser` | headless Chrome against a synthetic fixture (set `MAIL_ADMIN_CHROME` for another Chrome) |
| `make maddy-integration` | real Maddy 0.9.5 fixture ([patched image](tools/maddy-fixture/README.md)), synthetic mail |
| `make runtime-integration` | the non-root HTTPS image: socket, TLS, enrollment, restarts |
| `make acme-integration` | private ACME with pinned Certbot and Pebble images; pull the references in `tools/fixture_images.py` first |
| `make linux-check` | the three Docker suites plus unit and integration inside Linux containers |
| `make plan` | validates the design-contract docs |

`make check` runs lint, typecheck, reads, unit and integration. The Docker suites
need about 2 GiB free disk. To run one module after rebuilding the test image:

```sh
docker build --platform linux/amd64 --target qualification -t mail-admin:qualification .
.venv/bin/python tools/run_linux.py --suite maddy --module test_permissions_sessions
```

The implemented API covers token-gated setup, TOTP/recovery/optional Email OTP,
sessions, mailbox management, client configuration, DNS/Maddy diagnostics, encrypted
relay settings, delivery confirmation, backups and recovery. Email OTP and permanent
deletion default to disabled. No incoming mailbox password is stored in plaintext.

Before maintenance read the [operator handoff](verification/operator-handoff.md) and the
[security assessment](verification/maddy-security-assessment.md).
