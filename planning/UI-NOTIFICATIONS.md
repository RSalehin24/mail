# UI, dark mode and notification mail plan (D-048)

Constraints: no new dependencies (MUI 9 already ships `colorSchemes` and `useColorScheme`; mail uses stdlib `email`); $0 cost; no change to API shapes. Each stage is its own commit and release tier.

## Stage A: notification mail (T3, host alert path; restore point and rollback first)
Current: `shared/mail_templates.operational_alert` builds plain text and appends `json.dumps(event)` as "Technical details"; `shared/smtp.OutboundMessage` carries only `body`; `host/alerts.SmtpAlertTransport` sends it. `HttpsAlertTransport` posts JSON to a webhook by design and stays as is.
1. `shared/smtp.py`: add optional `html` to `OutboundMessage`; `send_message` uses `set_content(body)` then `add_alternative(html, subtype="html")`.
2. `shared/mail_templates.py`: one small renderer (title, status badge, facts table, hint, footer) used by all three mails (alert, delivery test, verification code). Inline CSS and a table layout, light and dark safe (`color-scheme` meta, no background images), every value HTML-escaped.
3. Alert content: subject `[Mail Admin] Action required: Backup - failed` (unchanged shape); body = what happened in one sentence, facts (Check, Status, Observed, Service, Reference), "What to do" hint per check name with a generic fallback, link to the dashboard. No JSON. The full event stays in the host log.
4. Tests: unit tests for plain and HTML parts (escaping, no `{`/JSON, every known check has a hint, recovered vs action-required), update `test_external_alert_delivery.py` and `test_host_alert_delivery.py` only for the new body, log it in DECISIONS. Preview by rendering sample mails to `/tmp`-free scratch files and opening them.
5. Verify: `make check`, `make linux-check`; the live test mail (Open item 4) needs your TOTP and approval.

## Stage B: design system and dark mode (T1)
1. One theme module (`frontend/src/theme.ts`) with `colorSchemes: { light, dark }`, tokens for surface, border, accent, radius (8), spacing and type scale; system font stack; high-contrast status colours (success, warning, error) that pass 4.5:1 in both modes.
2. Mode: `system | light | dark`, default system, toggle in the header cycling the three, saved in `localStorage` (try/catch); an inline script in `index.html` sets the scheme before paint so there is no flash.
3. Move hard-coded colours out of `styles.css` (brand mark, auth card, stat cards, chart colours in `ResourceCharts.tsx`) onto theme variables; delete rules that only repeat MUI defaults.
4. Minimal look: fewer borders and chips, no shadows, one accent, calmer spacing, visible focus rings, `prefers-reduced-motion` respected.

## Stage C: structure and UX (T1)
1. Navigation: a persistent left rail on desktop, a bottom bar or drawer on mobile, replacing the header buttons plus the duplicated drawer list; one `navigation.ts` item list drives both. Items: Mailboxes, Forwarding, Deleted, Server, Settings (merge or rename after reviewing Settings and Server content).
2. Shared page pieces: page header, section card, empty state, skeleton loading, inline error with Retry; replace the "Checking your session…" text and ad hoc alerts.
3. Feedback: one toast pattern (success and error), confirm dialogs only for destructive actions, forms with inline validation and helper text.
4. Mailboxes: single search/filter bar, status as a dot and text, row actions in one menu, mobile cards kept.
5. Accessibility: labelled controls, 48 px touch targets on mobile, keyboard-complete dialogs, contrast check in both modes.

## Verification (each stage)
`make lint typecheck reads unit`, `make browser` (extend tests/browser for light, dark and mobile screenshots), then deploy with rollback retained (image tag kept). Stage A also integration and Maddy runtime suites.

## Status
All three stages are coded locally (D-049 to D-052); deploy is pending approval.

## Order and effort
A first (fixes the visible complaint, isolated), then B, then C. Deploys of B and C need your approval each (VM change).
