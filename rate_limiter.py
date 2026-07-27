"""Shared-state rate limiting, named-slot concurrency ceilings, and single-run
locks — usable across multiple backend workers/replicas via Redis.

Replaces the single-process in-memory guards api.py used to hold directly
(`_RATE_LIMIT_CALLS`, `_llm_concurrency_count`, `_SME_REFRESHING`) — see
docs/deployment.md's "Scaling" section, which documented this exact gap:
each worker previously got its own counter, silently multiplying the
effective per-IP rate limit and letting two workers both start an SME
refresh at once.

Every function here degrades gracefully to the same in-memory behavior this
app had before Redis support existed, whenever `REDIS_URL` is unset or a
Redis call fails — the same "missing optional infra degrades rather than
breaks" convention as DATABASE_URL/SMTP_HOST elsewhere in this codebase. A
single-process deployment works identically with or without REDIS_URL set;
only a multi-worker deployment actually needs it for the limits to mean
what they say — see docs/deployment.md.

Note: if Redis flaps mid-flight (fails during release_slot but was reachable
during the matching try_acquire_slot, or vice versa), the Redis-side and
in-memory-side counters can drift out of sync for that one slot. This is
accepted as a rare edge case consistent with this being a coarse capacity
backstop, not a precise accounting system — the same tolerance the original
single-process implementation already had for a crashed request that never
reached its `finally` block.
"""
import os
import threading
import time
import uuid

from observability import get_logger, log_event

LOGGER = get_logger("rate_limiter")

_redis_client = None
_redis_client_lock = threading.Lock()
_redis_client_construction_failed = False
_redis_warned = False


def _get_redis_client():
    """Lazily construct a redis-py client (does not connect until first
    command). Returns None if REDIS_URL isn't set, or if construction itself
    failed (e.g. a malformed URL) — callers then use the in-memory fallback
    directly, same as every other Redis-call-failure path in this module.

    A construction failure is remembered rather than retried on every call —
    unlike a later command failure (which every caller already retries
    fresh next time, since Redis being briefly unreachable is transient), a
    bad REDIS_URL is a deterministic configuration problem that won't fix
    itself mid-process, so retrying it on every single rate-limited request
    would just be repeated, guaranteed-to-fail work."""
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
            _warn_redis_failure("redis_client_construction_failed", exc)
            return None
        return _redis_client


def _warn_redis_failure(event: str, exc: Exception) -> None:
    # Logged every time (not just once) — these should be rare in a healthy
    # deployment, and going quiet after the first failure would hide a
    # Redis outage lasting the rest of the process's life.
    log_event(LOGGER, event, level="warning", error=str(exc))


# ── Sliding-window rate limiting ─────────────────────────────────────────────

_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local max_calls = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count >= max_calls then
    redis.call('EXPIRE', key, math.ceil(window) + 1)
    return 0
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, math.ceil(window) + 1)
return 1
"""

_memory_calls: dict[str, list[float]] = {}
_memory_calls_lock = threading.Lock()


def _is_allowed_memory(key: str, max_calls: int, window_seconds: float) -> bool:
    now = time.monotonic()
    with _memory_calls_lock:
        calls = [t for t in _memory_calls.get(key, []) if now - t < window_seconds]
        if len(calls) >= max_calls:
            _memory_calls[key] = calls
            return False
        calls.append(now)
        _memory_calls[key] = calls
        return True


def is_allowed(key: str, max_calls: int, window_seconds: float) -> bool:
    """True if this call is within the sliding-window limit for `key` (and
    counts towards future checks), False if the limit is already reached."""
    client = _get_redis_client()
    if client is not None:
        try:
            now = time.time()
            member = f"{now}:{uuid.uuid4().hex}"
            result = client.eval(
                _SLIDING_WINDOW_SCRIPT, 1, f"ratelimit:{key}", now, window_seconds, max_calls, member,
            )
            return bool(result)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _warn_redis_failure("redis_rate_limit_failed", exc)
    return _is_allowed_memory(key, max_calls, window_seconds)


_USAGE_COUNT_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
return redis.call('ZCARD', key)
"""


def _usage_count_memory(key: str, window_seconds: float) -> int:
    now = time.monotonic()
    with _memory_calls_lock:
        calls = [t for t in _memory_calls.get(key, []) if now - t < window_seconds]
        _memory_calls[key] = calls
        return len(calls)


def get_usage_count(key: str, window_seconds: float) -> int:
    """Non-mutating peek at how many calls already count toward `key`'s
    sliding window (does NOT itself count as a call, unlike is_allowed) — for
    surfacing "N calls used this window" in a usage dashboard without
    perturbing the count it's reporting on."""
    client = _get_redis_client()
    if client is not None:
        try:
            now = time.time()
            return int(client.eval(_USAGE_COUNT_SCRIPT, 1, f"ratelimit:{key}", now, window_seconds))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _warn_redis_failure("redis_usage_count_failed", exc)
    return _usage_count_memory(key, window_seconds)


# ── Named-slot concurrency ceilings ──────────────────────────────────────────

_ACQUIRE_SLOT_SCRIPT = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local count = redis.call('INCR', key)
if count > limit then
    redis.call('DECR', key)
    redis.call('EXPIRE', key, ttl)
    return 0
end
redis.call('EXPIRE', key, ttl)
return 1
"""

_RELEASE_SLOT_SCRIPT = """
local key = KEYS[1]
local count = tonumber(redis.call('GET', key) or '0')
if count > 0 then
    redis.call('DECR', key)
end
return 1
"""

# Crash-recovery safety net, not expected to fire in normal operation — a
# held slot whose owning request died before calling release_slot() would
# otherwise stay claimed forever. Comfortably above how long any single
# analyst/pipeline call in this app should ever take. Refreshed (EXPIRE) on
# every acquire attempt — not just the first one that creates the key — so
# a key under sustained traffic never expires out from under slots that are
# still legitimately held, which would otherwise silently reset the counter
# and let the ceiling be exceeded.
_SLOT_TTL_SECONDS = 600

_memory_slots: dict[str, int] = {}
_memory_slots_lock = threading.Lock()


def _try_acquire_slot_memory(name: str, limit: int) -> bool:
    with _memory_slots_lock:
        count = _memory_slots.get(name, 0)
        if count >= limit:
            return False
        _memory_slots[name] = count + 1
        return True


def _release_slot_memory(name: str) -> None:
    with _memory_slots_lock:
        _memory_slots[name] = max(0, _memory_slots.get(name, 0) - 1)


def try_acquire_slot(name: str, limit: int) -> bool:
    """Non-blocking: claims one of `limit` named slots and returns True if
    one was free, else False. Every True must be paired with exactly one
    release_slot(name) call."""
    client = _get_redis_client()
    if client is not None:
        try:
            result = client.eval(_ACQUIRE_SLOT_SCRIPT, 1, f"slot:{name}", limit, _SLOT_TTL_SECONDS)
            return bool(result)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _warn_redis_failure("redis_slot_acquire_failed", exc)
    return _try_acquire_slot_memory(name, limit)


def release_slot(name: str) -> None:
    client = _get_redis_client()
    if client is not None:
        try:
            client.eval(_RELEASE_SLOT_SCRIPT, 1, f"slot:{name}")
            return
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _warn_redis_failure("redis_slot_release_failed", exc)
    _release_slot_memory(name)


# ── Single-run locks ──────────────────────────────────────────────────────────

_memory_locks: dict[str, bool] = {}
_memory_locks_lock = threading.Lock()


def _try_acquire_lock_memory(name: str) -> bool:
    with _memory_locks_lock:
        if _memory_locks.get(name):
            return False
        _memory_locks[name] = True
        return True


def _release_lock_memory(name: str) -> None:
    with _memory_locks_lock:
        _memory_locks[name] = False


def try_acquire_lock(name: str, ttl_seconds: float) -> bool:
    """Non-blocking: True if `name` wasn't already locked (and now is),
    False if another caller already holds it. `ttl_seconds` bounds how long
    a Redis-held lock can survive a crash that skips release_lock() — the
    in-memory fallback has no such expiry (a process crash there already
    resets all in-memory state, so a TTL would be redundant)."""
    client = _get_redis_client()
    if client is not None:
        try:
            return bool(client.set(f"lock:{name}", "1", nx=True, ex=int(ttl_seconds)))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _warn_redis_failure("redis_lock_acquire_failed", exc)
    return _try_acquire_lock_memory(name)


def release_lock(name: str) -> None:
    client = _get_redis_client()
    if client is not None:
        try:
            client.delete(f"lock:{name}")
            return
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _warn_redis_failure("redis_lock_release_failed", exc)
    _release_lock_memory(name)


def is_locked(name: str) -> bool:
    client = _get_redis_client()
    if client is None:
        return _memory_locks.get(name, False)
    try:
        return bool(client.exists(f"lock:{name}"))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        _warn_redis_failure("redis_lock_check_failed", exc)
        # Unlike try_acquire_lock()/release_lock(), which fall back to
        # _memory_locks on their OWN failure (keeping that dict internally
        # consistent), this is a read-only peek: when Redis is configured,
        # it's the one actually tracking this lock's real state, so
        # _memory_locks was very likely never populated by whatever call
        # genuinely acquired/released it. Falling back to that stale dict
        # here would silently report "not locked" while a run is genuinely
        # in progress via Redis. Fail closed (assume locked) instead --
        # worst case a caller sees a spurious "already running" for the one
        # request that lands during a transient Redis blip, self-correcting
        # on the next successful read. That's safer than the reverse
        # (reporting "not running" while one actually is, which could
        # prompt a duplicate/overlapping run).
        return True
