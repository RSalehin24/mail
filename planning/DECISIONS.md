# Decisions

Owner decisions, newest last. Record every answer here the same day it is given.

| ID | Date | Decision | Why / consequence |
|---|---|---|---|
| D-001 | 2026-11-20 | Level 2: Maddy forwards `mail@` and `website@` to Gmail; Cloudflare forwarding is removed. | Removes the Cloudflare hop and its sync code. Needs a forwarding design that survives DMARC (see PLAN phase 2, spike first). |
| D-002 | 2026-11-20 | UI refinement goes as far as it can; the backend may be refined in Rust if needed. | Full redesign on React/MUI. Rust is gated on measurements (PLAN phase 4); any new dependency or toolchain still needs approval. |
| D-003 | 2026-11-20 | Release rigor depends on the change; old tests may not work. | Tiered gate T0–T3; Phase 0 re-baselines the test suites. Tests are updated with intentional changes, never skipped. |
| D-004 | 2026-11-20 | The VM's external IP uses the STANDARD network tier. | Premium egress was the only cost (about $0.02). Standard gets the free 1 GB per month in us-east1. |
| D-005 | 2026-11-20 | `mail.rsalehin24.me` A record points to 35.211.79.14 (changed from 35.237.68.175). | New IP after the tier change; Phase 1 automates this on every IP change. |
| D-006 | 2026-11-20 | Claude works without stopping unless a decision is needed, then continues after the answer. Approving a plan step covers its commands. | `/continue` skill and the Autonomy section in `CLAUDE.md`. Destructive actions, new dependencies/secrets and unplanned live changes still ask. |
