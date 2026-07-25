"""Sends transactional email over generic SMTP — no vendor SDK, so any
provider works: a Gmail app password, a self-hosted mail server, an SES SMTP
endpoint, etc. Configured entirely through env vars (SMTP_HOST/PORT/USER/
PASSWORD/FROM), matching this repo's plain-env-var style (see .env.example)
rather than adding a new third-party API dependency per email type.
"""
import os
import smtplib
from email.message import EmailMessage

from observability import get_logger, log_event

LOGGER = get_logger("email_sender")

_MAGIC_LINK_SUBJECT = "Your AlphaPulse sign-in link"
_WATCHLIST_ALERT_SUBJECT = "AlphaPulse watchlist: recommendation change"


def _from_address() -> str:
    return os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or "noreply@alphapulse.local"


def _build_message(to_email: str, login_url: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = _MAGIC_LINK_SUBJECT
    msg["From"] = _from_address()
    msg["To"] = to_email
    msg.set_content(
        "Click the link below to sign in to AlphaPulse.\n\n"
        f"{login_url}\n\n"
        "This link expires in 15 minutes and can only be used once. "
        "If you didn't request this, you can safely ignore this email."
    )
    return msg


def _build_watchlist_alert_message(to_email: str, alerts: list[dict]) -> EmailMessage:
    lines = [
        "A stock on your AlphaPulse watchlist has a new recommendation:\n",
    ]
    for a in alerts:
        conf = f" [{a['confidence']} confidence]" if a.get("confidence") else ""
        lines.append(
            f"  {a['symbol']}: {a['old_recommendation']} -> {a['new_recommendation']}{conf}"
        )
    lines.append(
        "\nOpen AlphaPulse and search the symbol to see the full updated analysis."
    )

    msg = EmailMessage()
    msg["Subject"] = _WATCHLIST_ALERT_SUBJECT
    msg["From"] = _from_address()
    msg["To"] = to_email
    msg.set_content("\n".join(lines))
    return msg


def _send_via_smtp(msg: EmailMessage, failure_event: str) -> bool:
    """Shared SMTP send: connect, optionally STARTTLS + login, send, close.
    Best-effort — returns True/False, never raises. A missing SMTP_HOST or
    any SMTP failure just means the email never arrives; callers must not
    treat that as a hard failure of whatever triggered the send (see
    send_magic_link_email's docstring for why)."""
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

    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            if use_tls:
                server.starttls()
            if user and password:
                server.login(user, password)
            server.send_message(msg)
        return True
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, failure_event, level="error", error=str(exc))
        return False


def send_magic_link_email(to_email: str, login_url: str) -> bool:
    """Best-effort: returns True/False, never raises. A missing SMTP_HOST or
    any SMTP failure degrades to "the link never arrives" rather than a 500
    on the sign-in request — the caller (api.py) still tells the browser a
    link was sent either way, so this return value is only used to log the
    failure server-side, never to change what the user sees (that would leak
    whether an address is configured/reachable to whoever's probing)."""
    return _send_via_smtp(_build_message(to_email, login_url), "magic_link_email_failed")


def send_watchlist_alert_email(to_email: str, alerts: list[dict]) -> bool:
    """Best-effort digest email for watchlist_alerts.py: one email per user
    per run, listing every symbol whose recommendation changed since the
    prior stored verdict. `alerts` is a list of
    {"symbol", "old_recommendation", "new_recommendation", "confidence"}
    dicts (see watchlist_alerts._detect_change). Never raises; the caller
    only uses the return value for logging, same convention as
    send_magic_link_email."""
    if not alerts:
        return False
    return _send_via_smtp(
        _build_watchlist_alert_message(to_email, alerts), "watchlist_alert_email_failed"
    )
