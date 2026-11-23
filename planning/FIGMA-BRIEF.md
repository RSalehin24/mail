# Figma brief: Mail Admin (rsalehin24.me)

Paste the "Prompt" section into Figma (Make / AI, or give it to a designer). The rest is reference. Decision: D-067.

## Prompt

Design a minimalist, clean, lightweight Material Design 3 admin dashboard called **Mail Admin** for a one-person self-hosted mail server (9 mailboxes, one administrator). It is a calm tool used a few times a month, mostly on a laptop and sometimes on a phone. Design desktop (1440), tablet (834) and mobile (390), in light and dark.

**Feel**: quiet, spacious, text-first. No illustrations, no gradients, no shadows beyond one subtle elevation for dialogs. Few words: labels are short, no explanatory paragraphs. Show a note only when it states a consequence or a warning.

**Foundations** (export as Figma variables so they map to CSS custom properties)
- Color: Material 3 tonal palette from one calm blue-teal seed. Roles: primary, on-primary, surface, surface-container (low/high), outline, outline-variant, error, plus semantic success, warning and info that pass WCAG AA (4.5:1 text, 3:1 controls and borders) in both themes. Dark mode is not inverted light: darker surface containers, desaturated primary.
- Type: system font stack (no web font). Scale 12/14/16/20/24/32, 400 and 500 weights only.
- Shape and space: 8 px grid, radius 12 for cards and dialogs, 8 for fields, full pill for chips and buttons. Max content width 1080.
- Components: top app bar (mobile) / navigation rail (desktop, icon plus label), filled / tonal / text buttons, outlined text fields with visible 1 px borders, switch, segmented control, chips, status dot with text (never colour alone), list rows, data table that turns into stacked rows on mobile, tabs, dialog (bottom sheet on mobile), snackbar, empty state, skeleton loader, inline error with retry.
- Touch targets at least 44 px. Visible focus ring on every control. Support keyboard-only use.

**Screens** (one frame each, plus states)
1. **Sign in**: administrator name, password, then a second step with a 6-digit code (authenticator or emailed code) or a recovery code. First-run variant asks for a private bootstrap token. Errors inline under the field.
2. **Overview** (home): four stat tiles (Mailboxes, Backup, Certificate expiry, Disk) and a "Needs attention" list. Empty state: "Everything looks healthy."
3. **Mailboxes**: summary strip (Mailboxes, Inbox ready, Sending verified, Awaiting receipt), search, refresh, "Create mailbox" button, and a table with Name, Mailbox, status dot, row actions (open, reset password, delete). Show a protected mailbox row (lock, reason on one line).
4. **Mailbox detail** with tabs Details / Configuration / Connection details: edit display name and notes, mail-client connection settings with copy buttons, a "Delete" action.
5. **Deleted mailboxes** with tabs Deleted / Protected / Configure: retention countdown per row, Restore, Remove permanently (disabled with a reason). Show a **queued / processing** row state with a progress label that continues after the dialog closes.
6. **Forwarding** with tabs Rules / Incoming delivery: mailbox to address rows, enable switch, status (synced, pending, failed with Retry).
7. **Server**: CPU and memory history charts with a range switch (1 h / 24 h / 7 d), disk used vs capacity, uptime. Charts use two tones from the palette, labelled directly, with a text summary under each.
8. **Sending history**: paged list of delivery tests with status chip and time.
9. **Settings** with tabs Security / Outgoing provider / Backup & Recovery. Security: master password change, email codes switch, recovery codes. Outgoing provider: SMTP host, security, login, key, optional API key, "Test connection", "Verify outgoing email". Backup & Recovery: sub-tabs Backups / Logs, snapshot list, "Back up now", restore, deletion policy (enable, default retention, allow permanent deletion).
10. **Dialogs and sheets**: Create mailbox (prefix with `@rsalehin24.me` suffix, display name, notes, password with show/hide and strength), Reset password, Delete mailbox (retention choice, type the address to confirm, destructive button disabled until it matches), Verify it's you (re-authentication), Save your recovery codes (5 codes, checkbox before closing), Restore snapshot (type the snapshot ID), Verify outgoing email (code entry).
11. **States** for every screen: loading skeleton, empty, error with retry, and offline.

**UX rules**
- Destructive actions use the error colour, say what happens in one line (for example "Kept 7 days, then removed"), and need typed confirmation only when data could be lost for good.
- Long operations (delete, restore, backup) show a persistent inline status ("Queued", "Processing"), never a blocking spinner; the user can keep working and several can run at once.
- Success is a snackbar with Undo where possible. Never rely on colour alone.
- Theme control: System / Light / Dark in the app bar or settings.
- Deliver as a component library with variants (default, hover, focus, pressed, disabled, error) and a one-page style guide with the tokens.

## Hand-off I need back
1. A shareable Figma link (view access) with the variables, the component library and all frames above.
2. Optionally a short list of anything you changed or removed from the screen list.

## Constraints for the build (so the design stays buildable)
- Built with React and plain CSS custom properties, no UI library, small bundle. Icons: a small inline SVG set (no icon font). Charts: simple inline SVG.
- Keep every function the current app has; nothing is dropped without your say.
