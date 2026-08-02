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

Unlike analytics/verdict_history.py / core/state_store.py (best-effort, swallow-and-log),
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

from core.observability import get_logger, log_event

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
        # Opportunistic prune of expired rows on this write path — mirrors
        # _prune_extract_cache()'s "delete stale entries on the next write"
        # convention rather than a separate scheduled job. Both tables only
        # grow from auth traffic, so a request-link call is a natural trigger.
        conn.execute(text("DELETE FROM magic_links WHERE expires_at < NOW()"))
        conn.execute(text("DELETE FROM sessions WHERE expires_at < NOW()"))
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
    get-or-creates the `users` row for its email. Returns
    {"id":, "email":, "tier":} on success, or None if the token is missing,
    expired, or already used (an UPDATE ... WHERE used_at IS NULL ...
    RETURNING is inherently race-safe — two concurrent clicks of the same
    link can't both win). Includes `tier` for the same reason
    get_user_for_session()/get_user_for_api_key() do — this dict flows out
    through GET /api/auth/verify as the same {"user": {...}} shape
    GET /api/auth/me returns, and frontend/lib/auth.ts's AuthUser type
    declares tier as required."""
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
            RETURNING id, email, tier
        """), {"email": email}).mappings().first()
    return {"id": user["id"], "email": user["email"], "tier": user["tier"]}


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
    """Returns {"id":, "email":, "tier":} for a valid, non-expired session
    token, else None. Never raises — a missing/invalid/expired session or a
    DB hiccup should all just look like "not signed in", not a 500."""
    if not token:
        return None
    try:
        from sqlalchemy import text

        engine = _get_engine()
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT u.id, u.email, u.tier
                FROM sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = :token_hash AND s.expires_at > NOW()
            """), {"token_hash": _hash_token(token)}).mappings().first()
        return {"id": row["id"], "email": row["email"], "tier": row["tier"]} if row else None
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


# ── Programmatic API access (api_keys) ──────────────────────────────────────
# A key is a bearer credential like a session token, but scoped to external/
# scripted callers instead of a browser: it has no fixed expiry (a script
# can't "re-sign-in" the way a browser redirects through a magic link), so it
# stays valid until its owner explicitly revokes it. Same hash-only-storage
# convention as magic_links/sessions — create_api_key is the only place the
# raw key ever exists outside process memory, and it's returned to the
# caller exactly once.
_API_KEY_PREFIX = "apk_"


def create_api_key(user_id: int, label: str | None = None) -> dict:
    """Issues a fresh API key for an already-known user_id. Returns the full
    row including the raw key (present only in this return value — never
    persisted, never retrievable again after this call)."""
    from sqlalchemy import text

    raw_key = _API_KEY_PREFIX + secrets.token_urlsafe(32)
    key_prefix = raw_key[:12]
    engine = _get_engine()
    with engine.begin() as conn:
        row = conn.execute(text("""
            INSERT INTO api_keys (user_id, key_hash, key_prefix, label)
            VALUES (:user_id, :key_hash, :key_prefix, :label)
            RETURNING id, label, created_at
        """), {
            "user_id": user_id,
            "key_hash": _hash_token(raw_key),
            "key_prefix": key_prefix,
            "label": label,
        }).mappings().first()
    return {
        "id": row["id"],
        "key": raw_key,
        "key_prefix": key_prefix,
        "label": row["label"],
        "created_at": row["created_at"],
        # A freshly-created key is never used or revoked yet — included
        # explicitly (rather than omitted) so this return value has the same
        # shape as list_api_keys()' rows, plus the one-time `key` field.
        "last_used_at": None,
        "revoked_at": None,
    }


def list_api_keys(user_id: int) -> list[dict]:
    """Returns metadata for every key belonging to user_id, newest first —
    never the raw key or its hash, only what a management UI needs to let a
    user tell keys apart and revoke the right one."""
    from sqlalchemy import text

    engine = _get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, key_prefix, label, created_at, last_used_at, revoked_at
            FROM api_keys
            WHERE user_id = :user_id
            ORDER BY created_at DESC
        """), {"user_id": user_id}).mappings().all()
    return [dict(row) for row in rows]


def revoke_api_key(user_id: int, key_id: int) -> bool:
    """Revokes one of user_id's own keys. Returns False (not an error) if
    key_id doesn't exist, isn't owned by user_id, or was already revoked —
    the caller (api.py) turns that into a 404."""
    from sqlalchemy import text

    engine = _get_engine()
    with engine.begin() as conn:
        row = conn.execute(text("""
            UPDATE api_keys
            SET revoked_at = NOW()
            WHERE id = :key_id AND user_id = :user_id AND revoked_at IS NULL
            RETURNING id
        """), {"key_id": key_id, "user_id": user_id}).first()
    return row is not None


def get_user_for_api_key(raw_key: str) -> dict | None:
    """Returns {"user_id", "tier"} for a valid, non-revoked key and
    opportunistically stamps last_used_at, else None. `tier` comes along in
    the same query (no second round trip) since the only thing it's used for
    is picking the per-user rate limit on the same request. Never raises —
    same "a DB hiccup looks like an invalid credential, not a 500" convention
    as get_user_for_session."""
    if not raw_key:
        return None
    try:
        from sqlalchemy import text

        engine = _get_engine()
        with engine.begin() as conn:
            row = conn.execute(text("""
                UPDATE api_keys
                SET last_used_at = NOW()
                WHERE key_hash = :key_hash AND revoked_at IS NULL
                RETURNING user_id, (SELECT tier FROM users WHERE users.id = api_keys.user_id) AS tier
            """), {"key_hash": _hash_token(raw_key)}).mappings().first()
        return {"user_id": row["user_id"], "tier": row["tier"]} if row else None
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "api_key_lookup_failed", level="warning", error=str(exc))
        return None
