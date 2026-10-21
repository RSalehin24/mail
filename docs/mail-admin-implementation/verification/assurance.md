# Verification and release assurance contract

## What can be established

Expected behavior is defined by R01 through R22, the design contracts, and every case in the machine-readable test catalogue. Qualification is specific to an immutable dashboard image, host-tool bundle, deployment configuration, selected Maddy version and measured environment. A release must meet the same conditions that were tested. No document/model result is reported as application execution evidence.

Three independent checks are required:

1. The plan validator checks document links, requirement coverage, identifiers, gate/work dependencies and the generated acceptance catalogue.
2. The model checker enumerates every reachable state of three finite safety models: authentication revision races, create/rollback preservation and maintenance/ACME exclusion. Negative regression tests deliberately remove guards and must find counterexamples.
3. The release-evidence checker checks actual artifact/report hashes, required case results, configuration/profile consistency, evidence timestamps and approval. Candidate/preflight checks precede production; release checks also require target/external evidence and gate checkpoints.

## Scope of model results

A model pass proves its declared invariant for the reachable states of that finite model under its transition assumptions. It does not prove Python/FastAPI/Docker/Maddy implements those transitions. W08 must map model actions to real integration operations, inject the modeled race/crash boundaries, and compare the observed outcomes. Any changed state transition requires model review, mutant checks and renewed conformance evidence.

The models abstract message contents, timing, network behavior and unbounded state. Timing, limits, filesystem/SQL failures, cryptography, permissions and actual delivery are established by the acceptance tests and environment checks. Operating system, runtime and upstream dependencies are trusted only to their tested/configured boundaries; security-release review is mandatory.

| Model bounds | Checked safety invariant | Required implementation conformance |
| :--- | :--- | :--- |
| One in-flight authentication worker, two password revisions and one session | A valid issued session always belongs to the current revision, including delayed verification after a password change | T06.03 and T06.04 |
| One fresh account, credential/storage existence, journal ownership and known/unknown completion | Once storage exists, creation recovery cannot remove its credentials | T10.02 and T12.01 through T12.04 |
| One helper, renewal and backup participant, including surviving CLI/ACME children | Backup never overlaps writers or unresolved completion; activation never overlaps the helper CLI | T19.01 through T19.05 |

These are bounded safety proofs. Progress after an unknown outcome requires operator reconciliation by design. More actors, account incarnations and destructive SQL/filesystem behavior remain explicit real integration tests. The release checker also enforces checkpoint chronology; the modeled guards must appear in the implemented code and be exercised by the conformance tests.

## Evidence bundle format

Copy [the pending template](runtime-evidence.template.json) into an operator-owned release bundle. Place immutable artifacts and runner reports under that bundle; paths escaping it or following a symlink outside it are rejected. Fill real inputs rather than using documentation-rendering budgets. No secret values belong in the bundle; use root-owned credential/key references.

Required artifacts are `dashboard_image`, `host_bundle`, `deployment_config`, and `qualification_suite`. The configuration artifact contains the same `inputs` object as the evidence record. The artifact-set identity is SHA-256 of canonical JSON mapping those artifact IDs to their SHA-256 values. The runner revision must equal the qualification-suite archive digest. A helper, configuration, suite or image change therefore invalidates prior reports.

The evidence record and every report also contain `specification_sha256`: the digest of the current planning contracts, catalogue, design/runbooks and verification-tool source. Generated check reports, server snapshots and the empty runtime template are excluded to avoid circular identities. A specification/tool change requires renewed evidence.

Each test record identifies its catalogue group, relative report path and report SHA-256. Each report contains `schema_version: 1`, `test_id`, `artifact_set_sha256`, `specification_sha256`, `environment`, `runner_revision`, `started_at_utc`, `completed_at_utc`, `result: pass`, and one passing result for every exact case ID. Unknown/duplicate/missing cases and future or over-age timestamps fail. Required environment names come from the catalogue. The runner revision identifies the reviewed test/adapter bundle.

Each forward phase checkpoint G01 through G08 references a matching report/hash in the same format, with the exact gate-case IDs from contracts.json. A prerequisite must finish before the dependent gate starts. Candidate evidence precedes G01 completion; target evidence follows G07 startup and precedes G08 completion. Release approval records `approved_by`, `approved_at_utc`, and the same artifact-set identity after all evidence completes. Preflight additionally validates real operator inputs. P06 is required when Email OTP is enabled, P07 when permanent deletion is enabled, and P08 for direct-MX mode. Their isolated T-group tests remain mandatory when the production capability is disabled.

## Trust and enforcement

The checker verifies completeness, content integrity and binding; hashes do not authenticate who ran a test. Only reports emitted by the reviewed trusted runner are qualifying evidence. Protect the build/report store and deployment credentials; the deployment entry point must require the qualified artifact-set ID and green gates. A hand-edited passing JSON file, manual root bypass, a fixture report or a model pass cannot establish application correctness.

Future report timestamps and stale evidence are rejected using a synchronized UTC clock and the policy's 24-hour maximum evidence age. Changing that policy changes the specification identity. A report must be regenerated after an artifact/configuration/environment change; expired production evidence is rerun before promotion. Secret rotation, restore, interrupted operations and stopped-consumer maintenance intent retain their separate runtime checks.

## Commands

```sh
PYTHONDONTWRITEBYTECODE=1 python3 docs/mail-admin-implementation/tools/check_release.py /absolute/path/to/release/evidence.json --phase candidate
PYTHONDONTWRITEBYTECODE=1 python3 docs/mail-admin-implementation/tools/check_release.py /absolute/path/to/release/evidence.json --phase preflight
PYTHONDONTWRITEBYTECODE=1 python3 docs/mail-admin-implementation/tools/check_release.py /absolute/path/to/release/evidence.json --phase release
```

The default is `release`, the strictest gate. These commands inspect records; they do not deploy or authorize production changes. All application evidence starts as `not_run`. Qualification is blocked until real implementation evidence exists.
