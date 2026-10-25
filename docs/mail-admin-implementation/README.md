# Mail Admin implementation and verification plan

This package is the implementation contract for the existing mail server. It preserves the complete reviewed requirements and adds traceable implementation work, executable safety specifications and a release-evidence checker. The [earlier overview](../mail_admin_dashboard_plan.md) remains historical context.

`source_plan_sha256` identifies the reviewed 680-line input before its navigation link was added. `specification_sha256` identifies the current package and verification-tool source; it is the identity required by release evidence.

## Start here

1. Read [requirements](requirements/requirements.md), [release inputs](requirements/release-inputs.md), and the [verified baseline](baseline/server.md).
2. Implement the [work packages](implementation/work-packages.md) in dependency order using the design contracts below.
3. Prove the [acceptance catalogue](verification/acceptance.md) against the exact candidate artifacts.
4. Follow the [production rollout](operations/rollout.md); every gate must pass before its dependent phase.
5. Record actual evidence under the [assurance contract](verification/assurance.md). The supplied runtime template deliberately fails the release checker.

## Document map

| Area | Authoritative document |
| :--- | :--- |
| Boundaries and resources | [Architecture](design/architecture.md) |
| Browser behavior and responsive clients | [Frontend](design/frontend.md) |
| API permissions and errors | [API](design/api.md) |
| Authentication, sessions and offline recovery | [Security](design/security.md) |
| Create/reset/delete outcomes | [Mailbox transactions](design/mailboxes.md) |
| Root helper, locking and host lifecycle | [Helper](design/helper.md) |
| Database, migrations and retention | [Storage](design/storage.md) |
| Container, permissions and startup | [Runtime](design/runtime.md) |
| TLS generations, scheduling and notifications | [Certificates](operations/certificates.md) |
| Off-VM backups and restoration | [Recovery](operations/backup-recovery.md) |
| Brevo and optional DNS change | [Relay and routing](operations/relay-routing.md) |
| Operator acceptance | [Production checks](verification/production-checks.md) |
| External semantics | [Primary sources](verification/sources.md) |
| Review scope and outcomes | [Two-pass review](verification/review.md) |
| Verification results | [Current checks](verification/current-checks.json) |

## Verification commands available now

Run from the repository root with Python 3.9 or later; these tools use only the standard library.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 docs/mail-admin-implementation/tools/verify_plan.py
PYTHONDONTWRITEBYTECODE=1 python3 docs/mail-admin-implementation/tools/check_models.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s docs/mail-admin-implementation/tools/tests -v
```

The following command must reject the untouched template because no application evidence exists:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 docs/mail-admin-implementation/tools/check_release.py docs/mail-admin-implementation/verification/runtime-evidence.template.json
```

Application build/test commands delivered by W01 are documented in the repository-root `README.md`. Documentation and model results do not substitute for application tests. See repository-root `verification/status.md` for recorded checks and remaining qualification, and `verification/linux-continuation.md` for the new workstation. Historical private build artifacts require separate transfer.

To reproduce the complete local check record, including verifier regression tests and Docker Compose/shell examples:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 docs/mail-admin-implementation/tools/verify_plan.py --run-tests --check-examples --write-report
```

The example checks require the existing Docker Compose CLI. They render documentation fixtures and start no containers. After editing the canonical test catalogue, regenerate its Markdown with `--render-catalogue`; generated files are updated by the tool.

## Release claim

A qualified release has complete requirements-to-test coverage, passing case-level reports from a trusted runner, matching artifact/configuration identities, fresh environment evidence, and recorded operator approval. Any required missing, failed, skipped, stale or mismatched record blocks qualification. This is a conditional assurance claim for the specified behaviors and environments, with explicit assumptions in the assurance contract. It is not a claim that every possible future execution is defect-free.

Local implementation and synthetic qualification are in progress. This plan does not authorize production deployment, DNS edits, production mailbox deletion, external mail, new services, or upgrades. Production maintenance requires the operator approval recorded by the rollout gates.
