# Relay readiness and inbound routing

#### Brevo Sender Onboarding and Outbound Readiness

Use `mail@rsalehin24.me` as the fixed OTP From address and `rsalehin24@gmail.com` as the only OTP/test recipient. Verify that sender, the actual SMTP login/key, domain authentication, and successful delivery before enabling Email OTP. Settings connection tests use this fixed sender; per-mailbox sender tests use only an existing validated `@rsalehin24.me` account and still deliver only to the fixed admin address.

Creating a Maddy mailbox does not create a Brevo sender. For each new sending address, document the manual Brevo sender setup and verification step, then run an explicit delivery test from that address. Domain authentication can verify a sender automatically, but must not be inferred merely from successful SMTP login; follow [Brevo sender requirements](https://help.brevo.com/hc/en-us/articles/208836149-Create-a-new-sender-From-name-and-From-email). Record inbound and outbound outcomes separately, including test time and whether delivery was confirmed. SMTP acceptance alone is not proof of inbox delivery; keep delivery unconfirmed until the operator observes receipt.

Persist each delivery test's server-generated identifier, saved relay revision, sender, fixed recipient, SMTP result, and confirmation state. Include the identifier in the test email so the operator can confirm the right message through the shared receipt-confirmation endpoint. Only confirmed delivery from the fixed OTP sender on the **current saved revision** enables Email OTP; per-mailbox tests establish readiness only for that mailbox. Connection-only tests, SMTP acceptance, an optional quota API response, and an older successful revision cannot enable it. Throttle explicit test sends and never send email from routine dashboard health polling.

Relay credential/domain changes and dashboard restoration invalidate previous outbound-readiness evidence until retested. Mailbox password reset/deletion affects Maddy only; removal of a Brevo sender or rotation/revocation of a shared SMTP key is a separate action with its own impact on other clients. Do not describe a Maddy deletion as revoking all outbound access. A shared Brevo key authorizes more than one mailbox identity; show that scope when revealing/copying it and require a separately designed credential boundary before sharing access with untrusted mailbox users.

#### Inbound Routing Decision and Optional MX Cutover

Preserve Cloudflare Email Routing until the admin explicitly selects direct Maddy delivery. The dashboard reports the current route accurately and does not modify DNS automatically. Keeping Cloudflare is a valid decision, but the dashboard must not claim that Maddy receives domain mail directly through the current MX records.

If direct Maddy delivery is selected:

1. Record the current MX records, priorities, TTLs, forwarding rules, aliases, and required recipients. Confirm all recipients and intentional forwarding behavior have a destination after cutover; the existing eight mailbox accounts may not cover every Cloudflare route.
   Verify that both bare `postmaster` and `postmaster@rsalehin24.me` resolve to a working, intentionally selected mailbox/alias; the current rewrite and inventory do not establish this. Record other operational recipients/alias targets in the protected-recipient policy. Any required Maddy routing changes are explicit operator actions and must be tested before cutover.
2. Verify the DNS-only A record for `mail.rsalehin24.me`, external inbound SMTP on 25, IMAPS on 993, valid TLS, and delivery to a dedicated test mailbox using the target server directly.
3. At the agreed cutover, replace the domain MX records with `mail.rsalehin24.me` at priority 10. Preserve the recorded Cloudflare configuration for rollback; do not leave a mixed set of Cloudflare and Maddy MX records as an assumed forwarding mechanism.
4. Verify authoritative/public MX resolution, receipt from an independent external sender, authenticated IMAP access, and replies through the authorized Brevo sender. Check representative former forwarding routes and monitor for failures through DNS propagation.
5. If acceptance fails, restore the recorded MX records and routing configuration. Check both receiving systems for mail accepted during the transition; rolling back DNS does not move stored messages automatically.
