# Plan: versions that fix the design gaps (2026-11-23)

> Superseded by [PLAN-SIMPLE](PLAN-SIMPLE.md) (owner asked for the whole application to be straightforward, D-075). Kept for the gap list.

Goal: deleting, restoring and backing up never freeze or queue behind each other, one failed unit never silently stops the workers, and the frontend is rebuilt. Numbers in brackets are the gap numbers from the 2026-11-23 audit: 1 backup stops Maddy; 2 relay change restarts Maddy; 3 restore and remove use the queue; 4 recovery path unit retriggers itself; 5 backup-control path unit (unchecked); 6 account actions blocked by recovery; 7 interrupted job needs manual reconcile; 8 failed units unreported; 9 qualified-file proofs; 10 state split over many files; 11 test cost.

Rules for every version: tier gates from CLAUDE.md, no new dependencies, restore point before any T3 deploy, rollback written before deploy, decisions recorded in `DECISIONS.md` the same day, docs updated in the same change. Each version is deployed separately and watched before the next starts.

## v1.1 Worker hotfix (T3, host units only)
- [4] Recovery service start limit raised (done in the tree, not deployed).
- [5] Check `mail-admin-backup-control.path` live; give its service the same limit and a test.
- [8] Dashboard warns when a worker `.path` unit is failed (read `systemctl is-failed` through the existing monitor, show it under Needs attention).
- Deploy: copy unit files, `daemon-reload`, `reset-failed`, start both path units. No image change. Rollback: restore the old unit files.
- Done when: both path units active for 24 h, a test delete runs within seconds, no failed units.

## v1.2 Spike: live consistent copy (no deploy)
- Prove on a scratch copy of the Maddy data (not production) that an SQLite online backup of `imapsql.db` plus a copy of one account's message files is consistent while Maddy writes, and find how to keep a message that arrives mid-copy (refuse mail for that account first, or re-check after the copy).
- Output: a decision (D-0xx) stating the method and its test. If it cannot be made safe, fall back to option B below.
  - B: keep the short pause but only around the final delete step, not the archive.
- Gate: new integration test that writes mail during the copy and checks nothing is lost.

## v1.3 Direct delete, restore and remove (T3)
- [3] The dashboard runs delete in-process: verify archive, remove the account, return the result in one request. Restore and permanent remove take the same path. No Docker socket, no Maddy stop (if v1.2 passes).
- [6] Account create and password change no longer wait for recovery jobs of other mailboxes; they only conflict on the same mailbox.
- [7] An interrupted job retries safely from its last recorded phase instead of waiting for a manual reconcile.
- Retire `mail-admin-recovery.*` units and the queue limit; keep `recovery.db` as the record of deleted mailboxes.
- [9] Re-attest once for the new code; no further proof changes needed for later versions.
- Gate: unit, integration, runtime (real Maddy) delete/restore tests, restore point, owner attestation, rollback to the previous image and units.
- Done when: five deletes in a row work, mail keeps flowing during a delete, a deleted mailbox restores with its messages.

## v1.4 Backups and relay without downtime (T3)
- [1] Backup captures a consistent copy with the v1.2 method instead of stopping Maddy; manual backup and restore no longer freeze mail.
- [2] Relay changes reload Maddy configuration instead of a container restart if Maddy supports it, otherwise keep the restart but show a clear status while it happens.
- Gate: T3 suites, a real backup verified with 9 accounts, a restore into a scratch folder, one night of the 03:00 run watched.

## v1.5 Simplify state and tests (T2/T3, owner approval per step)
- [10] One status location for workers (a single table or file) instead of separate intents, markers and lock files, only where v1.3 and v1.4 left dead code.
- [9] Review whether every file in `QUALIFIED_FILES` still needs a hash proof; keep the proof for backup and archive code only.
- [11] Remove tests that only covered retired paths (each removal logged in `DECISIONS.md`); no test is weakened for code that remains.

## v2.0 New frontend (T1, parallel track)
- Owner designs in Figma from [FIGMA-BRIEF](FIGMA-BRIEF.md); then build React with plain CSS, remove MUI, same features as today.
- Starts when the Figma link arrives; independent of v1.x except that the Needs-attention list gets the failed-unit item from v1.1 and the inline "processing" state matches the v1.3 direct delete.
- Gate: lint, typecheck, unit, `make browser` (light and dark, phone, tablet, desktop), bundle size measured against today's, deploy with image rollback.

## Order and dependencies
v1.1 first (breaks things now) -> v1.2 spike -> v1.3 -> v1.4 -> v1.5. v2.0 runs alongside after the design arrives. v1.4 and v1.5 depend on the v1.2 result.

## Open decisions (ask before each version starts)
1. v1.2: if the live copy cannot be made safe, accept a short pause around the final delete only?
2. v1.3: keep the 7-day recovery archive for every delete (default), or offer a "delete without recovery copy" choice?
3. v1.4: is a manual backup allowed to pause mail for about 20 s if the live copy fails the spike?
4. v1.5: how much of the qualified-file proof may be dropped? This is a security control, so it needs your answer first.

## Not covered
Cost, DNS, firewall and certificate setup do not change in any version.
