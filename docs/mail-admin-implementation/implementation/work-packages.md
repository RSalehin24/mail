# Implementation work packages

Each package ends with reviewable code, deterministic checks and a recorded outcome. Use repository conventions and the coding rules. Keep the web/helper boundary injectable and share pure validators. Add no dependency or external service beyond the authorized stack without approval. Finish all implementation work and isolated qualification before G03 production maintenance.

The T-group references below identify owned acceptance coverage. Local completion requires unit/component checks for the delivered code. Cross-component and host cases run in full during W08 after their implementations exist; an upstream package never waits for a downstream package's full suite. G01 still requires every isolated case to pass before production maintenance.

| Package | Depends on | Deliverable | Owned acceptance coverage |
| :--- | :--- | :--- | :--- |
| W01 Foundation | None | Frontend/backend/helper layout, pinned Linux/amd64 build, dependency manifest and exact build/lint/type-check/unit/integration commands in the application README | Clean build from locked inputs; checks are reproducible outside the VM |
| W02 Persistent state | W01 | Explicit provisioning, schema/version migrations, root marker/key references, repositories, retention, safe transactions and error mapping | T02, T06, T18, T20, T22 |
| W03 Authentication | W02 | Bootstrap, password policy, factor challenge engine, sessions, recent authentication, replacement and offline utility | T02 through T06; authentication model conformance |
| W04 Host helper | W01 | Pure validators, allowlisted protocol, fixed argv/stdin and umask launcher, image/mount checks, journal, request outcome lookup and lifecycle | T07 through T12, T19, T20; creation model conformance |
| W05 Application services | W03, W04 | Central route authorization, bounded work, idempotent mailbox orchestration, diagnostics and safe outcomes | T03, T07, T10 through T13, T20, T21 |
| W06 Relay and frontend | W05 | Saved-revision relay tests, fixed-recipient receipt confirmation, masked configuration, mobile/tablet/desktop flows | T03, T04, T14 and applicable UI assertions in every affected group |
| W07 Host operations | W02, W03, W04 | Certificate generations, lock/intent handling, backup/restore, boot units, alerts, recovery wrapper and rollback package | T08, T09, T15 through T20, T22; coordination model conformance |
| W08 Candidate qualification | W06, W07 | Real Maddy fixture harness, fault injection, bounded load, traceable runner reports and exact artifact archive identities | Every T01 through T22 case passes; candidate evidence checker passes |
| W09 Deployment qualification | W08 | Completed deployment inputs, approved runbook checkpoints and target/external evidence | G01 through G08 and applicable P01 through P08 reports pass; release evidence checker passes |

## Required project structure

Keep compiled frontend, backend domain logic, data access and the host helper separate. The implementation may choose small idiomatic modules under `frontend/`, `backend/`, `helper/`, `host/` and `tests/`; record the final paths in its README. Reuse pure validation, authentication invalidation and relay-sending logic. The helper and recovery tools use the tested versions/protocol rather than a second drifting implementation.

W01 defines real application commands and test runner adapters. Each adapter emits case-level reports in the assurance format from actual test results; it cannot fabricate a passing group or treat skipped cases as passing. An implementation/API/state-machine change invalidates affected evidence. Freeze the final artifacts/configuration, rerun the complete qualification suite, and bind reports to that artifact set before preflight.

## Implementation review rules

- Every contract case becomes a real assertion against implemented behavior; unit tests alone do not qualify host/CLI behavior.
- Use real Maddy 0.9.5 fixtures for the verified baseline semantics and repeat against the selected upgraded version if one is approved.
- Fixtures contain no production mail/passwords. Real production fault injection and deletion of existing accounts are prohibited.
- Do not merge build, preflight and live acceptance into an unchecked shell sequence. Each failed gate has the documented abort path.
- An unresolved test failure or unknown destructive outcome remains a blocker, even when inventory looks normal.
