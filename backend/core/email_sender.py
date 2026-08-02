"""Sends transactional email over generic SMTP — no vendor SDK, so any
provider works: a Gmail app password, a self-hosted mail server, an SES SMTP
endpoint, etc. Configured entirely through env vars (SMTP_HOST/PORT/USER/
PASSWORD/FROM), matching this repo's plain-env-var style (see .env.example)
rather than adding a new third-party API dependency per email type.
"""
import os
import smtplib
from email.message import EmailMessage

from core.observability import get_logger, log_event

LOGGER = get_logger("email_sender")

_MAGIC_LINK_SUBJECT = "Your AlphaPulse sign-in link"
_WATCHLIST_ALERT_SUBJECT = "AlphaPulse watchlist: updates"


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


def _format_alert_line(a: dict) -> str:
    """One line per alert — recommendation-change alerts and price-move
    alerts carry different fields (see watchlist_alerts._detect_change /
    _detect_price_move), so this branches on `kind` rather than assuming a
    single shape. `kind` defaults to "recommendation_change" for alerts
    built before this field existed, so an older in-flight call site (if
    any) still renders the same as before."""
    if a.get("kind") == "price_move":
        direction = "up" if a["change_pct"] >= 0 else "down"
        return (
            f"  {a['symbol']}: price moved {direction} {abs(a['change_pct'])}% "
            f"(₹{a['old_price']} -> ₹{a['new_price']})"
        )
    conf = f" [{a['confidence']} confidence]" if a.get("confidence") else ""
    return f"  {a['symbol']}: {a['old_recommendation']} -> {a['new_recommendation']}{conf}"


def _build_watchlist_alert_message(to_email: str, alerts: list[dict]) -> EmailMessage:
    lines = [
        "Updates on your AlphaPulse watchlist:\n",
    ]
    lines.extend(_format_alert_line(a) for a in alerts)
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

    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    # Defaults on (587/STARTTLS is the common case for real providers). Only
    # meant to be turned off for a local/dev relay that doesn't speak TLS.
    use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() not in ("false", "0", "no")

    try:
        # SMTP_PORT is parsed inside this try block, not before it — a
        # malformed value (e.g. "587," from an operator copy-paste typo)
        # must degrade through the same "log and return False" path as
        # every other SMTP failure below, not raise ValueError straight out
        # of this function. That matters beyond this call site: watchlist_
        # alerts.py's run() has no try/except around its per-user
        # send_watchlist_alert_email() call at all (an unattended daily cron
        # job would otherwise crash mid-batch), and api.py's
        # /api/auth/request-link only catches this to return a 503 instead
        # of the documented always-{"sent": true} response, which itself
        # leaks that something is broken server-side — both consequences
        # this function's own "never raises" docstring promise exists to
        # prevent.
        port = int(os.environ.get("SMTP_PORT", "587"))
        # Port 465 is the IANA-registered implicit-TLS SMTP port (many major
        # providers, e.g. Gmail/Office365, document it as their alternative to
        # 587) — a plaintext connect-then-STARTTLS handshake against a listener
        # already expecting TLS from the first byte fails outright. Without this,
        # an operator setting SMTP_PORT=465 (a very plausible config choice,
        # without also separately setting SMTP_USE_TLS=false) would have every
        # send silently fail — the broad except below swallows it into a log
        # line, so sign-in links and watchlist alerts would simply never arrive
        # with no obvious symptom pointing at the port/TLS-mode mismatch.
        use_implicit_tls = port == 465
        smtp_cls = smtplib.SMTP_SSL if use_implicit_tls else smtplib.SMTP
        with smtp_cls(host, port, timeout=10) as server:
            if use_tls and not use_implicit_tls:
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
    """Best-effort digest email for pipelines/watchlist_alerts.py: one email per user
    per run, listing every alert for that run — a recommendation change
    ({"kind": "recommendation_change", "symbol", "old_recommendation",
    "new_recommendation", "confidence"}, see
    watchlist_alerts._detect_change) and/or a large price move
    ({"kind": "price_move", "symbol", "old_price", "new_price",
    "change_pct"}, see watchlist_alerts._detect_price_move). Never raises;
    the caller only uses the return value for logging, same convention as
    send_magic_link_email."""
    if not alerts:
        return False
    return _send_via_smtp(
        _build_watchlist_alert_message(to_email, alerts), "watchlist_alert_email_failed"
    )
