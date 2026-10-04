"""Outbound email.

Everything the app knew how to remind you about — weanings, overdue flips,
tasks — only appeared if you happened to have the page open. This is the
delivery half.

Configured entirely from the environment, so nothing secret lands in the
repo:

    BIOMANAGER_SMTP_HOST      e.g. smtp.gmail.com
    BIOMANAGER_SMTP_PORT      587 (STARTTLS) or 465 (implicit TLS)
    BIOMANAGER_SMTP_USER
    BIOMANAGER_SMTP_PASSWORD  an app password, not your account password
    BIOMANAGER_MAIL_FROM      defaults to SMTP_USER
    BIOMANAGER_BASE_URL       used for links in the mail, e.g. http://lab-server:5055

With no host configured, `send` logs the message instead of sending it.
That keeps the reminder job runnable — and testable — before any mail
server exists.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from .i18n import gettext

log = logging.getLogger("biomanager.mail")


def config() -> dict:
    host = os.environ.get("BIOMANAGER_SMTP_HOST", "").strip()
    user = os.environ.get("BIOMANAGER_SMTP_USER", "").strip()
    return {
        "host": host,
        "port": int(os.environ.get("BIOMANAGER_SMTP_PORT", "587") or 587),
        "user": user,
        "password": os.environ.get("BIOMANAGER_SMTP_PASSWORD", ""),
        "sender": os.environ.get("BIOMANAGER_MAIL_FROM", "").strip() or user,
        "base_url": os.environ.get("BIOMANAGER_BASE_URL", "http://127.0.0.1:5055").rstrip("/"),
        "configured": bool(host),
    }


def is_configured() -> bool:
    return config()["configured"]


def status_line() -> str:
    """One sentence for the settings page."""
    cfg = config()
    if not cfg["configured"]:
        return gettext("Not configured — reminders are written to the log instead of sent.")
    return gettext("Sending through %(host)s:%(port)s as %(sender)s.", host=cfg["host"], port=cfg["port"],
                   sender=cfg["sender"] or gettext("unset sender"))


def send(to: str, subject: str, text: str, html: str | None = None) -> bool:
    """Send one message. Returns True when it actually left the building.

    A mail failure must never take down the job that triggered it, so
    everything is caught and logged — a missed reminder is an annoyance, a
    crashed nightly script is a silent outage.
    """
    to = (to or "").strip()
    if not to:
        return False

    cfg = config()
    if not cfg["configured"]:
        log.warning("MAIL (not configured, would have sent)\n  to: %s\n  subject: %s\n%s",
                    to, subject, text)
        return False

    message = EmailMessage()
    message["From"] = formataddr(("BioManager", cfg["sender"]))
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")

    try:
        context = ssl.create_default_context()
        if cfg["port"] == 465:
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=context, timeout=20) as server:
                if cfg["user"]:
                    server.login(cfg["user"], cfg["password"])
                server.send_message(message)
        else:
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                if cfg["user"]:
                    server.login(cfg["user"], cfg["password"])
                server.send_message(message)
        log.info("mail sent to %s: %s", to, subject)
        return True
    except Exception as exc:
        log.error("mail to %s failed: %s", to, exc)
        return False


# ---------------------------------------------------------------------------
# Digest rendering
# ---------------------------------------------------------------------------

STYLE = (
    "font-family:-apple-system,Segoe UI,Roboto,sans-serif;"
    "font-size:14px;line-height:1.55;color:#15191d;"
)


def render_digest(display_name: str, sections: list[dict], base_url: str) -> tuple[str, str]:
    """Plain-text and HTML bodies for one person's digest.

    `sections` is a list of {title, icon, items:[{label, detail, urgency, url}]}.
    Both formats are produced from the same data — some people read mail in
    a terminal, and the plain part is also what lands in the log when SMTP
    is unconfigured.
    """
    # In the reader's language when called inside i18n.using (scripts/send-reminders.py).
    hello = gettext("Hello %(name)s,", name=display_name)
    lines = [hello, ""]
    html = [f'<div style="{STYLE}">', f"<p>{hello}</p>"]

    for section in sections:
        if not section["items"]:
            continue
        lines.append(section["title"].upper())
        html.append(f'<h3 style="margin:18px 0 6px;font-size:14px;">{section["title"]}</h3>')
        html.append('<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;">')
        for item in section["items"]:
            flag = {"overdue": gettext("OVERDUE"), "today": gettext("today"), "soon": ""}.get(item.get("urgency", ""), "")
            prefix = f"[{flag}] " if flag else "  "
            lines.append(f"{prefix}{item['label']} — {item['detail']}")
            colour = {"overdue": "#91352f", "today": "#7e5a08"}.get(item.get("urgency", ""), "#6b757d")
            link = f'{base_url}{item["url"]}' if item.get("url") else base_url
            html.append(
                '<tr>'
                f'<td style="padding:3px 12px 3px 0;white-space:nowrap;">'
                f'<a href="{link}" style="color:#14567f;text-decoration:none;font-weight:600;">{item["label"]}</a></td>'
                f'<td style="padding:3px 0;color:{colour};">{item["detail"]}</td>'
                '</tr>'
            )
        html.append("</table>")
        lines.append("")

    lines += ["", gettext("Open BioManager: %(url)s", url=base_url)]
    html.append(
        f'<p style="margin-top:22px;"><a href="{base_url}" '
        'style="background:#14567f;color:#fff;padding:8px 14px;border-radius:6px;'
        f'text-decoration:none;display:inline-block;">{gettext("Open BioManager")}</a></p>'
    )
    html.append('<p style="color:#8b959c;font-size:12px;margin-top:20px;">'
                + gettext("You are receiving this because your account has an email address. Clear it in Settings to stop.")
                + '</p></div>')
    return "\n".join(lines), "\n".join(html)
