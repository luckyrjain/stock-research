"""Shared lazy Redis client construction for core/cache.py and
core/rate_limiter.py.

Both modules used to independently define identical
_get_redis_client()/_warn_redis_failure() logic (lazy construction with
remembered failure, degrades to None on any error — never raises). This is
that logic, moved here verbatim and made public (no leading underscore, this
is now a real shared module) so it lives in exactly one place. Both
core/cache.py and core/rate_limiter.py keep their own thin private
_get_redis_client()/_warn_redis_failure() wrappers delegating here, so
existing `unittest.mock.patch("core.cache._get_redis_client", ...)`-style
test targets in each module keep working unchanged.

Depends only on core/observability.py (get_logger/log_event) — the same
dependency both cache.py and rate_limiter.py already had for this logic.
"""
import os
import threading

from core.observability import get_logger, log_event

LOGGER = get_logger("redis_client")

_redis_client = None
_redis_client_lock = threading.Lock()
_redis_client_construction_failed = False


def get_redis_client(construction_failed_event: str = "redis_client_construction_failed"):
    """Lazily construct a redis-py client (does not connect until first
    command). Returns None if REDIS_URL isn't set, or if construction itself
    failed (e.g. a malformed URL) — callers then use their own fallback
    directly, same as every other Redis-call-failure path in this codebase.

    A construction failure is remembered rather than retried on every call —
    unlike a later command failure (which every caller already retries
    fresh next time, since Redis being briefly unreachable is transient), a
    bad REDIS_URL is a deterministic configuration problem that won't fix
    itself mid-process, so retrying it on every single call would just be
    repeated, guaranteed-to-fail work.

    `construction_failed_event` lets each caller keep its own distinct log
    event name (cache.py's own construction failure previously logged as
    "cache_redis_client_construction_failed", rate_limiter.py's as
    "redis_client_construction_failed") even though the construction itself
    now happens in one shared place — the client is shared, but which
    subsystem discovered the failure first is still worth telling apart in
    the logs."""
    global _redis_client, _redis_client_construction_failed
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    if _redis_client is not None:
        return _redis_client
    if _redis_client_construction_failed:
        return None
    with _redis_client_lock:
        if _redis_client is not None:
            return _redis_client
        if _redis_client_construction_failed:
            return None
        try:
            import redis
            _redis_client = redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _redis_client_construction_failed = True
            warn_redis_failure(construction_failed_event, exc)
            return None
        return _redis_client


def warn_redis_failure(event: str, exc: Exception) -> None:
    # Logged every time (not just once) — these should be rare in a healthy
    # deployment, and going quiet after the first failure would hide a
    # Redis outage lasting the rest of the process's life.
    log_event(LOGGER, event, level="warning", error=str(exc))
