---
name: continue
description: Resume the project and keep working through the plan without stopping, asking the owner only when a decision is needed.
disable-model-invocation: true
---
Autonomous work loop. Do not stop to report progress or to ask "should I continue".

1. Read `planning/STATUS.md` only, then the current phase section of `planning/PLAN.md` by line range. Give a 3-line start note (phase, step you are taking, blockers), then begin working in the same message. Do not wait for a reply.
2. Work through the steps of the current phase in order. For each step: read what you need, make the change, run its checks per the release tier in `CLAUDE.md`, fix failures at the cause, and move straight to the next step. Run independent work in parallel.
3. Keep the files current as you go, not at the end: `planning/STATUS.md` (stage, next action), `planning/DECISIONS.md` (every answer), and the docs your change affects.
4. **Stop only for a decision** (see `CLAUDE.md`). Ask with `AskUserQuestion`: one decision per question, 2-4 options, recommended first. When the answer arrives, record it in `DECISIONS.md` and continue immediately from where you stopped, with no recap and no re-confirmation. Batch every decision you can foresee in the current phase into one call (max 4 questions) so the owner is interrupted once, not repeatedly.
5. An owner approval of a plan step covers every command needed to carry out that step; do not ask again per command. Ask again only for anything outside the approved step, anything destructive or irreversible, new dependencies, new secrets, or a change in mail routing, DNS, firewall, the VM, backups, or the live dashboard that the approved step did not name.
6. If a check fails and the cause is unclear after two attempts, or a tool is denied, stop that step, note it in STATUS under blockers, and move to the next independent step. Never work around a denial and never weaken a test.
7. Finish a phase by running its exit check, writing a 5-line summary into `planning/STATUS.md`, then start the next phase. When the whole plan is done, or only blocked items remain, give a final summary: changed, only inspected, not verified.
