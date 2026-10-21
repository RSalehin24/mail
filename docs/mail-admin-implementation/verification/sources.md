# Primary sources and verification boundaries

These sources support upstream semantics; they do not prove the application has been implemented. Source references were reviewed during the planning session on October 21–22, 2026. Recheck version-specific behavior when selecting a different image.

| Source | Supported decision |
| :--- | :--- |
| [Maddy 0.9.6 release](https://github.com/foxcpp/maddy/releases/tag/v0.9.6) | Security-release assessment; no implicit upgrade |
| [Maddy account model](https://maddy.email/tutorials/setting-up/) | Credentials and storage are distinct |
| [Maddy 0.9.5 provisioning](https://github.com/foxcpp/maddy/blob/v0.9.5/internal/cli/ctl/imapacct.go) | Storage may exist after a later error; folder warnings |
| [Maddy password update](https://github.com/foxcpp/maddy/blob/v0.9.5/internal/auth/pass_table/table.go) and [SQL SetKey](https://github.com/foxcpp/maddy/blob/v0.9.5/internal/table/sql_query.go) | Reset requires existence checks; credential update can insert |
| [Versioned storage deletion](https://github.com/foxcpp/go-imap-sql/blob/b5e85e90f14d/backend.go) and [file store](https://github.com/foxcpp/go-imap-sql/blob/b5e85e90f14d/fsstore.go) | Message files can be removed before SQL commit; partial deletion requires reconciliation |
| [bcrypt](https://pkg.go.dev/golang.org/x/crypto/bcrypt#GenerateFromPassword) and [Maddy stdin](https://github.com/foxcpp/maddy/blob/v0.9.5/internal/cli/clitools/clitools.go) | 72-byte limit and newline-sensitive CLI input |
| [Compose services](https://docs.docker.com/reference/compose-file/services/) and [Docker security](https://docs.docker.com/engine/security/) | Missing-bind rejection, non-root limits and no web Docker socket |
| [Certbot renewal](https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates) | No-op success, ordinary renewal and controlled dry run |
| [systemd directory lifecycle](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#RuntimeDirectoryPreserve=) | Stable socket directory and boot ordering |
| [MUI installation](https://mui.com/material-ui/getting-started/installation/) and [Uvicorn behavior](https://uvicorn.dev/server-behavior/) | Required frontend dependencies and bounded server work |
| [Brevo sender requirements](https://help.brevo.com/hc/en-us/articles/208836149-Create-a-new-sender-From-name-and-From-email) | Sender onboarding and observed delivery rather than inferred readiness |

The latest SSH observation file is [baseline/observations.json](../baseline/observations.json). External TLS/DNS/STARTTLS observations and local/tool check outcomes are recorded separately. Nothing in these sources substitutes for SMTP authentication, actual delivery, restore drills, target memory measurements or the application test suite.
