# Frontend behavior

### Component 1: React + Material UI Frontend (Multi-Device Optimized)

Built with Vite, Material UI (`@mui/material`, `@mui/icons-material`, `@emotion/react`, `@emotion/styled`), and responsive breakpoint hooks (`useMediaQuery`). Include compatible `react`/`react-dom` versions and follow the [MUI installation requirements](https://mui.com/material-ui/getting-started/installation/); verify the complete dependency manifest in a clean build.

#### Responsive Design Breakdown:

| Feature | Android Phone (`< 600px`) | Laptop & Desktop (`≥ 960px`) |
| :--- | :--- | :--- |
| **Mailbox List** | Touch-friendly card view with expand/collapse details | Full data table with sorting, search, and pagination |
| **2FA Code Input** | Auto-focuses native numeric pad (`inputMode="numeric"`) | Standard keyboard navigation with auto-advance |
| **Modals / Dialogs** | Fullscreen mobile dialogs for effortless reading | Centered elevation cards with side-by-side tabs |
| **Copy Actions** | Large touch targets with bottom snackbar confirmation | Compact copy buttons with floating hover tooltips |
| **Navigation** | Sticky mobile header with drawer menu | Desktop top app bar with direct tab switching |

For 600–959px widths, retain the card layout and allow dialogs to expand within the viewport. Configure the 960px transition explicitly rather than assuming a framework default. Support keyboard focus, accessible field/error labels, whole-code paste/autofill, and a separate text input for alphanumeric recovery codes. Clear password, OTP, QR, and revealed-secret UI state on dismissal/logout; never persist these in browser storage.

#### [NEW] `/opt/mail-admin/frontend/src/`

- **`theme.ts`**:
  - Responsive typography scaling (fluid header sizes on mobile vs desktop).
  - Minimalist palette (slate dark/light modes, crisp contrast, Inter font).
  - Minimum 48px touch targets on mobile viewports.
- **`views/LoginView.tsx`**:
  - Step 1: Admin email (`rsalehin24@gmail.com`) and password.
  - Step 2: 6-box auto-advancing 2FA input. Configured with `inputMode="numeric"` and `autoComplete="one-time-code"` so Android keyboards display number pads and offer 1-tap OTP autofill.
  - Backup option: "Send 6-digit code to rsalehin24@gmail.com" with a countdown timer, available only when the current relay revision has confirmed OTP delivery. Otherwise explain its unavailable state and retain TOTP/recovery-code access.
  - Emergency fallback: "Use Recovery Code" option.
- **`views/SetupView.tsx`**:
  - Require the private bootstrap token before retrieving or submitting any setup secrets.
  - Display the TOTP QR code and secret during enrollment; initialize the admin only after a valid TOTP code is confirmed.
  - Display 5 one-time recovery codes once and require acknowledgment that they were saved.
- **`views/DashboardView.tsx`**:
  - Live Diagnostic Bar: Status pills for **Server Health**, **Brevo Relay**, and **DNS MX Record**.
  - Include certificate expiry, last renewal outcome, and discrepancies between credential and IMAP accounts. Show Cloudflare as the current inbound route; distinguish TCP/TLS reachability from successful authentication and actual delivery.
  - Dynamic display: Renders responsive **Cards** on Android/mobile, and a **Data Table** on laptop/desktop.
  - Search bar and filtering.
  - Quick action buttons: **Configure**, **Reset Password**, **Delete**.
  - Permanent deletion requires recent password + 2FA verification, typed confirmation of the exact email address, and a qualifying backup covering that account. Show the trusted backup timestamp/recovery cutoff and require acknowledgment that newer mail is not recoverable from it. Explain that stored messages will also be removed; show why protected operational recipients cannot be deleted.
  - If storage deletion fails or its outcome is unknown, show "Reconciliation required: some messages may already have been removed." Retain that warning even if the IMAP account still appears in inventory; do not offer automatic access restoration or a blind deletion retry.
  - Show inbound provisioning and Brevo outbound readiness separately. Password-reset results explain that existing IMAP sessions may remain active and offer the documented operator procedure when immediate revocation is needed.
- **`components/NewMailboxDialog.tsx`**:
  - Modal to create a new `@rsalehin24.me` address.
  - Fields: Username prefix, Display Name, Notes/Purpose.
  - One-click password generator using browser cryptographic randomness, producing 24 ASCII characters, with explicit clipboard copy. Apply the backend's password validation and show a new password only for the current create/reset flow.
  - After Maddy creation, show outbound status as unverified until the address is authorized and tested through Brevo; provide the manual sender setup steps.
- **`components/ClientConfigDialog.tsx`**:
  - Dedicated configuration modal for every mailbox. Fullscreen on phone, elegant centered dialog on desktop.
  - Tabbed views:
    - 🍏 **Apple Mail (macOS & iOS)**
    - 📱 **Google Gmail (Android / iOS / Web "Send As")**
    - 💻 **Mozilla Thunderbird / Microsoft Outlook**
    - ☁️ **Nextcloud Mail**
    - ⚙️ **Raw Connection Details**
  - Explicit separation:
    - **Incoming (IMAP)**: `mail.rsalehin24.me` | Port 993 | SSL | Username | Account Password.
    - **Outgoing (SMTP)**: `smtp-relay.brevo.com` | Port 587 | STARTTLS | Brevo Login | Brevo Key.
  - Existing mailbox passwords cannot be recovered from Maddy hashes. Explain how to use the known password or reset it; never retain plaintext mailbox passwords for later display.
  - Mask relay secrets by default. Explicit reveal/copy requires recent reauthentication, returns no cacheable response, and never appears in audit logs.
  - Instant one-click copy buttons for every field.
- **`views/SettingsView.tsx`**:
  - **Brevo & Relay Settings**:
    - SMTP Host, Port (587/465), SMTP Username, SMTP Password / Key.
    - Fixed OTP From address: `mail@rsalehin24.me`, configured by the operator and enabled only after sender authorization and delivery are verified.
    - Optional Brevo REST API Key (to display daily quota: e.g. *280/300 remaining*).
    - **"Test Connection" button**: Test saved settings over verified TLS and SMTP authentication; offer an explicit test email to the fixed admin address, followed by "I received this test" confirmation identifying that test. Require saving and retesting after edits. A successful TCP connection or SMTP acceptance alone does not enable Email OTP.
  - **Security Settings**:
    - Change Master Password.
    - Replace the TOTP authenticator after recent password + an existing valid second factor, then verify the new seed before activation. On success, invalidate the old seed, existing sessions/challenges, and recovery codes; display replacement recovery codes once.
    - Regenerate 2FA Backup Recovery Codes after recent password + 2FA verification. Previous codes become invalid; stored code hashes cannot be revealed.

Use one reauthentication dialog for sensitive actions: verify the current password and an existing second factor, obtain the session-bound five-minute grant, then resume the requested action. Expired grants prompt again. Password/factor changes that revoke the current session clear its cookie/UI state and require a fresh login.

---
