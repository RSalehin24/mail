# Current production state — 27 October 2026

Settings and backup/recovery controls are live as `mail-admin-settings-d7dec30d3de3`.
Recoverable mailbox deletion is enabled; permanent deletion remains disabled.
Read the [current implementation and verification record](mail-admin-completion.md)
for pagination coverage, restore behavior, evidence, rollback state and remaining
full-server recovery/reboot limitations. Statements below are historical.

---

# Redesign implementation checkpoint — 26 October 2026

## Current production state — supersedes historical checkpoints below

Production URL: https://mail.rsalehin24.me. The application update
`mail-admin-ux-afe377a34b41` is live (runtime image
`sha256:8603392c436534c8f726d09965173dab00d0f1e2f338591cb071a68c9871b36d`).
One-second lightweight telemetry, SVG resource charts, distinct security cards and
mailbox-specific sending histories are deployed. All eight credential/storage pairs,
master password, authenticator enrollment and mailbox metadata were compared and
preserved. Favicon returns SVG; private inventory and telemetry reject anonymous access.

The first update attempt failed during deletion of an old stopped Docker container.
Its exception handler then removed the new container and could not restore the already
removed old one. The old immutable image and saved container configuration restored
service, then a retry deployed the update successfully with stopped rollback containers
retained. Failed logs remain at `build/server-ux-deployment.log`; successful evidence
is `build/server-ux-deployment-retry.log`.

A subsequent backup capture completed, but immediate post-start TLS readiness failed.
Both services resumed. Actual served certificates were verified and the exact completed
capture was encrypted, transferred off host and independently restored for integrity
verification. See `build/server-ux-backup-reconciliation.log`; its later nonzero exit
concerns a nonexistent external-monitor timer, after backup success. A separate accurately
named local TLS-observation timer now runs successfully; host monitoring and backup timers
are active. Independent Google-hosted HTTPS/IMAPS alert policies were re-read and verified
enabled in `build/server-ux-independent-monitoring-verification.json`. No new synthetic
test messages were sent. Full production reboot acceptance remains outstanding.

Mailbox table refinements (Name, Mailbox, Creation Date, newest first; row count selector
and first/last pagination), minimal page headings, and distinct browser/application titles
are in preparation. Missing creation timestamps are persisted once on inventory discovery.

Release 2 remains incomplete and is NOT enabled. The root retention policy and recovery
ledger now have tests for 7 days, 30 days, Keep forever, encrypted-point prerequisites,
interrupted operations, durable state and snapshot pinning. These foundations are not
wired to functional mailbox deletion/restoration, API or UI controls. No mailbox has
been deleted. Historical release/checkpoint statements below retain original evidence
and do not describe current production state.

---

The two-release task is **not complete**. This run has not changed production.
A read-only SSH observation found an existing production dashboard image
`6b2d8f0e70d6` and Maddy `e1074e92a452` running. Older handover statements that the
dashboard was not installed are superseded by that observation and the retained
production deployment logs. No live account, credential, message, enrollment,
service, or monitoring policy was changed in this run.

## Implemented for Release 1

- Persistent overview, settings, server, and mailbox details/configuration/connections
  routes with browser history and refresh support; authenticated mailbox detail API.
- Dedicated identity and metadata editing, password flow, and shared connection values.
- Focused desktop table and mobile cards, real address and brand links, centered
  headings, light/dark layouts, SVG branding assets and manifest with correct serving.
- Current-revision sender receipts and readable readiness; sequential bulk verification
  preserves successful senders across reauthentication, with individual progress.
- Server observations separated from mailbox overview. Memory capacity/used/available,
  free disk/inodes, root-configured thresholds, inventory probe timestamp, served
  certificate observations and backup evidence are displayed only when available.
  External monitoring activation/history and a separately timestamped latest recovery
  test remain unavailable through the API and are explicitly identified as such.
- Rejected reauthentication stays in its dialog; Enter submission and email prerequisites;
  server recent-authentication checks remain authoritative across refresh. Transport
  failures during session inspection show a retry state. Browser fill replaces inputs.

## Verification and preserved evidence

- 477 unit and 75 integration checks passed in `build/redesign-unit-qualified.log`
  and `build/redesign-integration-qualified.log`.
- Ruff, strict mypy, frontend lint/type checks, Vite production build and diff whitespace
  checks passed.
- Local browser pass: `build/redesign-browser-eighth.log`, including metadata refresh,
  history, logo, favicon, bulk selection and receipt confirmation separated from admin
  sender verification. Six light/dark overview screenshots at 360/768/1280 were inspected;
  the discovered inline mobile name layout was corrected and checked again.
- Actual HTTPS/Unix-helper browser pass:
  `build/https-browser-03309319-d9ec-4e3a-b793-3804c55234e0/observation.json`.
  Earlier failures remain in their original `https-browser-*` directories and driver logs.
  They include test navigation assumptions and a transient 503; none is promoted to a pass.
- Frozen dashboard image: `sha256:32d5912bf87ba53d8e0c5534dfa7379ed468c02d3756266bce2d22f4942e9785`.
- `build/release-candidate-redesign-r1/` is preserved but superseded: the catalogue
  rejected its source-file inventory because the new public assets were omitted from
  the verifier's source-member predicate. Archive, image and source inventory now include
  these files. The replacement bundle is
  `build/release-candidate-redesign-r1-public-assets/`.
- Full catalogue execution: `build/redesign-frozen-catalogue-public-assets.log`.
  An in-progress or failed catalogue does not qualify this candidate.

## Outstanding task requirements

Release 1 still requires complete matching frozen catalogue/host/replacement evidence,
production upgrade and acceptance, preserved inventory/authentication verification,
backup recovery, service restart/reboot and monitoring activation checks. No newly
implemented feature is deployed by this checkpoint.

Release 2 is not implemented. The existing deletion is guarded permanent deletion,
disabled by the selected production policy. There is no seven-day recoverable delete,
admin policy/protection editing, deleted-mailbox list, isolated mailbox extraction/import,
pinned recovery snapshots, schema migration or restore reconciliation workflow.
The interface states that seven-day mailbox recovery is unavailable. Whole-host recovery
must not be presented as individual-mailbox restore, and a shared mail database must
never be replaced to restore an individual address.

## Production-profile qualification continuation

The complete prior production deployment profile was compared with current selected
operator inputs; every selected input matched. A separate fully configured bundle
was frozen at `build/release-candidate-redesign-r1-production/`, artifact set
`e15e091a228a21befa7f4962a74101119848b3237eba88c1b7eb09b34211e259`.
Its candidate/source identity is in `build/redesign-production-candidate.json`.
The generic-profile catalogue was deliberately interrupted as superseded; its
original reports, controller state, retained volume and interruption/cleanup error
remain preserved. It is not a complete or production-qualifying run. The complete
production-profile run is `build/redesign-production-catalogue.log`.

The frozen generic-profile HTTPS check passed separately at
`build/release-candidate-redesign-r1-public-assets/https-browser-28315a59-7ac4-4077-ac0c-24e47fe1f761/observation.json`.
Supplemental deep-link login, all mailbox tabs in both themes and all three widths,
and keyboard focus/order checks passed in
`build/redesign-supplemental-page-audit.json`. Its initial incorrect mouse-modality
focus expectation remains recorded separately. These supplemental checks do not
supply missing full production-profile qualification.

The production-profile frozen HTTPS check passed at
`build/release-candidate-redesign-r1-production/https-browser-c49b77c7-cba9-4032-bc44-18bfae8bd9d1/observation.json`.
The owned source guest `7f8a95a0-0ff9-40ba-a554-064f8e265288` was checksum-verified,
upgraded to the frozen image/helper code with its previous state retained, and
actually rebooted from boot ID `cffef2ec-cdb6-429f-9ec4-568ca094e87a` to
`d39194a7-4b67-4ec7-8fd9-2521745ea4c5`. This is disposable-host evidence, not a
production reboot. Matching T08 checks passed. T22's host retention assertion passed,
while its overall group remained blocked on the non-host cases; the wrapper's
nonzero-group exit is preserved and is not a failed retention assertion.
Raw host reports were collected unchanged in `build/redesign-host-observations-first.tar`.
The upgrade and host driver logs are `build/redesign-host-upgrade.log` and
`build/redesign-host-lifecycle.log`. Remaining host scenarios and actual replacement
recovery have not been rerun for this artifact set. The full production-profile local
catalogue remains in progress in `build/redesign-production-catalogue.log`.
