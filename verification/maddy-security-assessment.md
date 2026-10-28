# Maddy security-release assessment

Fixture selection: pinned Maddy 0.9.5 amd64 manifest
`sha256:2b0c7e0e06b5e6e9e0738a2d6a826dc03b945e864db69288efd7fad4a62ee1e8`.
The existing live container still identifies its image as
`sha256:de42151adff6388edb5e4ee88f60334fa1ab85e309485193ecb1c2db20203315`.
Manifest/index/config identities are not interchangeable; qualify the selected
loaded image and its actual container identity before rollout.

The [upstream 0.9.6 security release](https://github.com/foxcpp/maddy/releases/tag/v0.9.6)
lists fixes involving PROXY-protocol trust, a missing-password authentication path,
SMTP TLS-required handling, a limiter deadlock, and the IMAP SQL dependency.
Those changes require an applicability review against the actual Maddy configuration.
Existing 0.9.5 fixture passes do not establish that 0.9.5 is secure or qualify 0.9.6.

No upgrade was selected or applied. A 0.9.6 upgrade requires explicit selection,
a pinned image, repeated compatibility/recovery tests and a matched rollback set.
The deployment record's security disposition remains an operator input; this draft
assessment does not satisfy that release gate or authorize accepting the risks.

The real 0.9.5 fixture also demonstrated a SQL COMMIT error after message-body
removal with process exit code zero. The CLI logs `app.Run failed` for ordinary
errors without setting a nonzero process status in its
[versioned entry point](https://github.com/foxcpp/maddy/blob/v0.9.5/internal/cli/app.go).
The helper checks that error marker separately, records the actual exit code and
requires postcondition reconciliation. Special-folder failures can also return
zero and use `Failed to create … folder` text; they must not count as fully
provisioned accounts. These compatibility guards do not fix the upstream storage
semantics or replace the security-release decision.

## Production security-upgrade selection — 26 October 2026

The operator explicitly selected **qualify a security upgrade first**. Upstream
[0.9.6](https://github.com/foxcpp/maddy/releases/tag/v0.9.6) is the selected security
release. Its downloaded amd64 image is pinned through
`ghcr.io/foxcpp/maddy@sha256:e1074e92a452b84d1135ebe8ab831c5f541d33dcbd39e38fab740a53bbf2f97c`;
the binary reports `0.9.6 linux/amd64 go1.26.8`. The previous assessment above is
retained as historical context, not the current selection.

The helper now supports an explicit, exact 0.9.6 CLI selection while retaining the
original image/version for the pre-upgrade baseline. Unselected and suffix/lookalike
versions are rejected. Eight isolated real-Maddy compatibility checks passed:
eight matching mailbox pairs and delivery, password reset/session behavior,
existing-only guards, permissions/umask, UTF-8 password bounds, failure handling,
and an offline 0.9.5 database upgrade with its independent rollback copy readable.
The first offline-restart observation failed because its fixture reused a dynamic
host port after Docker restart; the test now observes the reassigned port. That
failed report remains preserved and the fresh eight-test run passed.

This is development compatibility evidence. New source/artifacts still require
frozen qualification and production backup/restore, deployment and target acceptance.
Production continues running its unchanged 0.9.5 image until those prerequisites
pass. No security upgrade has yet been applied to production.
