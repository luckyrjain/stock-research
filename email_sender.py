"""Sends the magic-link sign-in email over generic SMTP — no vendor SDK, so
any provider works: a Gmail app password, a self-hosted mail server, an SES
SMTP endpoint, etc. Configured entirely through env vars (SMTP_HOST/PORT/
USER/PASSWORD/FROM), matching this repo's plain-env-var style (see
.env.example) rather than adding a new third-party API dependency for one
transactional email.
"""
import os
import smtplib
from email.message import EmailMessage

from observability import get_logger, log_event

LOGGER = get_logger("email_sender")

_SUBJECT = "Your AlphaPulse sign-in link"


def _build_message(to_email: str, login_url: str) -> EmailMessage:
    from_email = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or "noreply@alphapulse.local"

    msg = EmailMessage()
    msg["Subject"] = _SUBJECT
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(
        "Click the link below to sign in to AlphaPulse.\n\n"
        f"{login_url}\n\n"
        "This link expires in 15 minutes and can only be used once. "
        "If you didn't request this, you can safely ignore this email."
    )
    return msg


def send_magic_link_email(to_email: str, login_url: str) -> bool:
    """Best-effort: returns True/False, never raises. A missing SMTP_HOST or
    any SMTP failure degrades to "the link never arrives" rather than a 500
    on the sign-in request — the caller (api.py) still tells the browser a
    link was sent either way, so this return value is only used to log the
    failure server-side, never to change what the user sees (that would leak
    whether an address is configured/reachable to whoever's probing)."""
    host = os.environ.get("SMTP_HOST")
    if not host:
        log_event(LOGGER, "smtp_not_configured", level="warning")
        return False

    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    # Defaults on (587/STARTTLS is the common case for real providers). Only
    # meant to be turned off for a local/dev relay that doesn't speak TLS.
    use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() not in ("false", "0", "no")
    msg = _build_message(to_email, login_url)

    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            if use_tls:
                server.starttls()
            if user and password:
                server.login(user, password)
            server.send_message(msg)
        return True
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "magic_link_email_failed", level="error", error=str(exc))
        return False
