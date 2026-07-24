"""Minimal magic-link authentication: no passwords, no OAuth. A user requests
a link by email; clicking it creates an account (on first use) and a session
in one step — there is no separate signup flow.

Both magic-link tokens and session tokens are opaque, high-entropy strings
(secrets.token_urlsafe(32)) handed to the caller once and never stored raw —
only their SHA-256 hash is persisted, the same "never store what you don't
have to" instinct as everywhere else in this codebase. The frontend's Next.js
proxy routes hold the raw session token in an httpOnly cookie and forward it
to this API as `Authorization: Bearer <token>`; this module never sees a
cookie, only a token string.

Unlike verdict_history.py / signals/store.py (best-effort, swallow-and-log),
create_magic_link/verify_magic_link/create_session raise on a DB failure —
a broken auth write must surface as an error to the caller (api.py), not
silently pretend to have sent a link or created a session. Read-path lookups
(get_user_for_session) degrade to "not logged in" on any failure instead,
since a DB hiccup here must not turn into a 500 on every authenticated page
load.
"""
import hashlib
import secrets
import threading
from datetime import datetime, timedelta, timezone

from observability import get_logger, log_event

LOGGER = get_logger("auth")

_ENGINE = None
_ENGINE_LOCK = threading.Lock()

MAGIC_LINK_TTL = timedelta(minutes=15)
SESSION_TTL = timedelta(days=30)


def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:  # re-check: another thread may have won the race
                from db.models import get_engine
                _ENGINE = get_engine()
    return _ENGINE


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_magic_link(email: str) -> str:
    """Stores a fresh single-use token for `email` (creating no user row yet —
    that only happens once the link is actually clicked) and returns the raw
    token. Caller is responsible for emailing it; this function never sees a
    login URL, just the token to embed in one."""
    from sqlalchemy import text

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + MAGIC_LINK_TTL
    engine = _get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO magic_links (email, token_hash, expires_at)
            VALUES (:email, :token_hash, :expires_at)
        """), {
            "email": email.lower().strip(),
            "token_hash": _hash_token(token),
            "expires_at": expires_at,
        })
    return token


def verify_magic_link(token: str) -> dict | None:
    """Atomically consumes a magic-link token: marks it used, then
    get-or-creates the `users` row for its email. Returns {"id":, "email":}
    on success, or None if the token is missing, expired, or already used
    (an UPDATE ... WHERE used_at IS NULL ... RETURNING is inherently
    race-safe — two concurrent clicks of the same link can't both win)."""
    from sqlalchemy import text

    engine = _get_engine()
    with engine.begin() as conn:
        row = conn.execute(text("""
            UPDATE magic_links
            SET used_at = NOW()
            WHERE token_hash = :token_hash
              AND used_at IS NULL
              AND expires_at > NOW()
            RETURNING email
        """), {"token_hash": _hash_token(token)}).mappings().first()
        if row is None:
            return None
        email = row["email"]

        user = conn.execute(text("""
            INSERT INTO users (email) VALUES (:email)
            ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email
            RETURNING id, email
        """), {"email": email}).mappings().first()
    return {"id": user["id"], "email": user["email"]}


def create_session(user_id: int) -> str:
    """Issues a fresh session token for an already-known user_id and returns
    the raw token."""
    from sqlalchemy import text

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + SESSION_TTL
    engine = _get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO sessions (user_id, token_hash, expires_at)
            VALUES (:user_id, :token_hash, :expires_at)
        """), {
            "user_id": user_id,
            "token_hash": _hash_token(token),
            "expires_at": expires_at,
        })
    return token


def get_user_for_session(token: str) -> dict | None:
    """Returns {"id":, "email":} for a valid, non-expired session token, else
    None. Never raises — a missing/invalid/expired session or a DB hiccup
    should all just look like "not signed in", not a 500."""
    if not token:
        return None
    try:
        from sqlalchemy import text

        engine = _get_engine()
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT u.id, u.email
                FROM sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = :token_hash AND s.expires_at > NOW()
            """), {"token_hash": _hash_token(token)}).mappings().first()
        return {"id": row["id"], "email": row["email"]} if row else None
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "session_lookup_failed", level="warning", error=str(exc))
        return None


def delete_session(token: str) -> None:
    """Best-effort sign-out — a failure here just means the session outlives
    its TTL instead of being revoked early, not a broken logout button."""
    if not token:
        return
    try:
        from sqlalchemy import text

        engine = _get_engine()
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM sessions WHERE token_hash = :token_hash"),
                {"token_hash": _hash_token(token)},
            )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "session_delete_failed", level="warning", error=str(exc))
