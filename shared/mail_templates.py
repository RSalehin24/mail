"""Notification messages: a readable plain-text part and a matching HTML part."""

from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from typing import Any

from shared.validation import ADMIN_EMAIL, MAIL_HOST

DASHBOARD_URL = f"https://{MAIL_HOST}/"
NOT_PROVIDED = "Not provided"

# Plain-language next steps for the checks the host monitors; unknown names get the fallback.
ALERT_HINTS = {
    "backup": "Open Mail Admin > Server and check the latest backup. "
              "Run a backup if the last verified one is old.",
    "host_capacity": "The mail disk is nearly full. Delete old data or free space "
                     "before mail delivery is affected.",
    "external_tls_993": "The mail certificate is expiring or unreachable. "
                        "Check that Maddy renews it (Let's Encrypt via Cloudflare DNS).",
    "external_tls_443": "The dashboard certificate is expiring or unreachable. "
                        "Check that Maddy renews it and the dashboard restarted.",
}
FALLBACK_HINT = "Open Mail Admin and review the Server page and the host journal."
RECOVERED_HINT = "No action is needed. The check is healthy again."

HEALTHY_COLOR = "#1b7f5c"
ATTENTION_COLOR = "#b3261e"
NEUTRAL_COLOR = "#3b5b73"


@dataclass(frozen=True)
class Notice:
    title: str
    summary: str
    facts: tuple[tuple[str, str], ...]
    badge: str
    color: str
    footer: str = f"Sent by Mail Admin on {MAIL_HOST}."


def render_text(notice: Notice) -> str:
    rows = "\n".join(f"{label}: {value}" for label, value in notice.facts)
    return f"{notice.title}\n\n{notice.summary}\n\n{rows}\n\n{notice.footer}\n"


def render_html(notice: Notice) -> str:
    rows = "".join(
        '<tr><td style="padding:6px 16px 6px 0;color:#5f6b73;white-space:nowrap">'
        f'{escape(label)}</td><td style="padding:6px 0;color:#1d252b">{escape(value)}</td></tr>'
        for label, value in notice.facts
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light"></head>'
        '<body style="margin:0;padding:24px 12px;background:#f2f5f4;'
        'font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">'
        '<table role="presentation" width="100%" style="max-width:560px;margin:0 auto;'
        'background:#ffffff;border-radius:12px;border:1px solid #dde4e1"><tr>'
        '<td style="padding:28px">'
        f'<span style="display:inline-block;padding:4px 10px;border-radius:999px;'
        f'background:{notice.color};color:#ffffff;font-size:12px;font-weight:600">'
        f'{escape(notice.badge)}</span>'
        f'<h1 style="margin:16px 0 8px;font-size:20px;color:#1d252b">{escape(notice.title)}</h1>'
        f'<p style="margin:0 0 20px;font-size:15px;line-height:1.5;color:#33414a">'
        f'{escape(notice.summary)}</p>'
        f'<table role="presentation" style="font-size:14px">{rows}</table>'
        f'<p style="margin:24px 0 0;font-size:12px;color:#7a868d">{escape(notice.footer)}</p>'
        '</td></tr></table></body></html>'
    )


def _compose(subject: str, notice: Notice) -> tuple[str, str, str]:
    return subject, render_text(notice), render_html(notice)


def _readable(value: str) -> str:
    return value.replace("_", " ").capitalize()


def _observed(event: dict[str, Any]) -> str:
    timestamp = event.get("observed_at", event.get("created"))
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d %H:%M UTC")
    return NOT_PROVIDED


def delivery_test(identifier: str) -> tuple[str, str, str]:
    return _compose("[Mail Admin] Email delivery test", Notice(
        title="Email delivery test",
        summary="This message confirms that Mail Admin can send email. "
                "Enter the test ID in Mail Admin to finish the check.",
        facts=(("Service", MAIL_HOST), ("Recipient", ADMIN_EMAIL), ("Test ID", identifier)),
        badge="Test", color=NEUTRAL_COLOR))


def verification_code(code: str) -> tuple[str, str, str]:
    return _compose("[Mail Admin] Your verification code", Notice(
        title="Your verification code",
        summary="Enter this code in Mail Admin. It expires in 5 minutes. "
                "If you did not request it, ignore this message and do not share the code.",
        facts=(("Code", code),),
        badge="Verification", color=NEUTRAL_COLOR))


def operational_alert(event: dict[str, Any]) -> tuple[str, str, str]:
    check = _readable(event["name"])
    status = _readable(event["condition"])
    recovered = event["condition"] == "recovered"
    outcome = "Recovered" if recovered else "Action required"
    hint = RECOVERED_HINT if recovered else ALERT_HINTS.get(event["name"], FALLBACK_HINT)
    reference = event.get("id", event.get("event_id", NOT_PROVIDED))
    return _compose(f"[Mail Admin] {outcome}: {check} - {status}", Notice(
        title=f"{check}: {status.lower()}",
        summary=hint,
        facts=(("Check", check), ("Status", status), ("Observed", _observed(event)),
               ("Service", MAIL_HOST), ("Dashboard", DASHBOARD_URL), ("Reference", str(reference))),
        badge=outcome, color=HEALTHY_COLOR if recovered else ATTENTION_COLOR))
