"""Per-task cache with TTL-based freshness checks.

Each task's output is stored at output/<SYMBOL>/<task_name>.json with a
top-level _meta.fetched_at timestamp — the local-disk JSON store this app
has always used, and still the only store in a single-host deployment.

Optional Redis-backed sharing (`REDIS_URL`, same env var `rate_limiter.py`
already reads): a CTO-lens review flagged this file cache as the platform's
real ceiling on horizontal scale — it's documented elsewhere in this repo
as "the persistent shared state," but that's only true on one host. Past
one host/replica without a shared volume, every cache silently forks per
instance, multiplying scraper load on the exact fragile third-party sources
this app already treats cautiously (Screener.in, NSE, Trendlyne, RBI).

When REDIS_URL is set, `save()` writes through to Redis (the actual
cross-host source of truth) in addition to local disk; `load()`/`is_fresh()`
check Redis first and only fall back to disk when Redis has no entry for
that key at all — a Redis outage or eviction, or a key from before Redis
was configured. A Redis entry that *exists but is stale* is trusted as-is,
never overridden by a possibly-fresher local disk copy: the whole point of
centralizing through Redis is that every host agrees on one freshness
answer, and falling through to disk whenever Redis says "stale" would
silently reintroduce the per-host fork this feature exists to close.
Local disk is always written too, Redis or not — the persistent store
when REDIS_URL is unset, and a fast local mirror / same-instance fallback
when it's set. Same "missing optional infra degrades rather than breaks"
convention as `rate_limiter.py`, DATABASE_URL, and SMTP_HOST elsewhere in
this codebase: a single-host deployment behaves identically with or
without REDIS_URL set.
"""

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from observability import get_logger, log_event

LOGGER = get_logger("cache")

CACHE_DIR = Path("output")

# How long each task's data stays fresh (hours)
TTL_HOURS: dict[str, float] = {
    "stock_info":       1,    # price data — refresh every hour
    "research":         24,   # fundamentals — refresh daily
    "news":             1,    # headlines — refresh every hour
    "shareholding":     168,  # quarterly filings — 7 days
    "mf_holdings":      168,  # quarterly filings — 7 days
    "analysis":         24,   # re-analyse daily (or when any input changes)
    "price_history":    6,    # daily-close series for sparklines — doesn't move fast
    "peers":            24,   # peer comparison table — fundamentals-like, doesn't move fast
    "financials":       24,   # multi-year P&L/balance sheet/cash flow + DCF — fundamentals-like
    "index_history":    24,   # ^NSEI daily closes for the picks-history alpha stat
    "insider_activity": 24,   # insider trades + bulk/block deals — disclosed with lag anyway
    "fii_dii_flow":     24,   # market-wide, published once per trading day
    "macro_context":    24,   # RBI repo rate / CPI — changes at most monthly, daily refresh is plenty
    "street_consensus": 24,   # Trendlyne-cited analyst commentary — a news search, not a live feed
    "shareholding_detail": 168,  # same quarterly NSE XBRL filing as mf_holdings — 7 days
}

_REDIS_KEY_PREFIX = "cache"

_redis_client = None
_redis_client_lock = threading.Lock()
_redis_client_construction_failed = False


def _get_redis_client():
    """Same lazy-construction, remembered-failure pattern as
    rate_limiter.py::_get_redis_client() — duplicated rather than imported.
    cache.py is imported by nearly every other module in this codebase
    (it's the most foundational piece of shared state here), so it
    deliberately doesn't take on a dependency on a sibling module for
    something this small; both modules independently read the same
    REDIS_URL and construct their own client."""
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
            _warn_redis_failure("cache_redis_client_construction_failed", exc)
            return None
        return _redis_client


def _warn_redis_failure(event: str, exc: Exception) -> None:
    # Logged every time (not just once) — same reasoning as
    # rate_limiter.py's own _warn_redis_failure: going quiet after the
    # first failure would hide a Redis outage lasting the rest of this
    # process's life, and these should be rare in a healthy deployment.
    log_event(LOGGER, event, level="warning", error=str(exc))


def _redis_key(symbol: str, task_name: str) -> str:
    return f"{_REDIS_KEY_PREFIX}:{symbol.upper()}:{task_name}"


def cache_path(symbol: str, task_name: str) -> Path:
    return CACHE_DIR / symbol.upper() / f"{task_name}.json"


def _is_failed_payload(data: object) -> bool:
    # `_degraded` marks a safe-fallback payload (e.g. crew._safe_analysis_fallback) —
    # it has no "error" key of its own, but must not be cached as if it were a
    # genuine result, or it'd be served as fresh for the rest of the TTL window.
    return isinstance(data, dict) and bool(data.get("error") or data.get("_degraded"))


def _is_fresh_payload(data: dict, task_name: str) -> bool:
    """Freshness is always re-derived from _meta.fetched_at rather than
    trusted from a store's own expiry mechanism (Redis's EX, a file's mtime)
    — so a change to TTL_HOURS takes effect immediately for entries already
    written, on both backends, without needing them to be rewritten."""
    fetched_at_str = data.get("_meta", {}).get("fetched_at")
    if not fetched_at_str:
        return False
    try:
        fetched_at = datetime.fromisoformat(fetched_at_str)
    except (TypeError, ValueError):
        return False
    age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
    return age_hours <= TTL_HOURS.get(task_name, 1)


def load(symbol: str, task_name: str) -> dict | None:
    """Return cached data if it exists, succeeded, and is within TTL, else None."""
    client = _get_redis_client()
    if client is not None:
        try:
            raw = client.get(_redis_key(symbol, task_name))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _warn_redis_failure("cache_redis_read_failed", exc)
            raw = None
        if raw is not None:
            # Redis has an opinion on this key — trust it either way (a
            # stale/failed entry here is NOT a cue to fall through to disk;
            # see the module docstring for why that would defeat the point
            # of sharing cache state across hosts in the first place).
            try:
                data = json.loads(raw)
            except Exception:  # pylint: disable=broad-exception-caught
                data = None
            if data is not None and not _is_failed_payload(data) and _is_fresh_payload(data, task_name):
                return data
            return None

    p = cache_path(symbol, task_name)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if _is_failed_payload(data):
            return None
        return data if _is_fresh_payload(data, task_name) else None
    except Exception:
        return None


def save(symbol: str, task_name: str, data: dict) -> dict | None:
    """Write a copy of data to cache, stamping _meta.fetched_at. Returns the
    `_meta` dict on success, or None if `data` was a failed payload (nothing
    was written).

    Deliberately does NOT mutate the caller's own `data` object — several
    call sites (GET /api/peers/{symbol}, /financials, /insider-activity,
    /street-consensus, /shareholding-detail, the analysis-result cache
    write) build `result`, call save(symbol, task, result), and then return
    that same `result` object straight to an HTTP client; an in-place
    mutation here would leak the internal-only `_meta` field into those
    JSON responses. A caller that DOES want `_meta` reflected onto its own
    object (main.py's CLI, building `data_freshness` from
    `freshly_fetched`/`all_data`, mirrored in api.py's SSE handler) stamps
    it explicitly from this return value instead — see `_fetched_at()` in
    main.py for why that matters: `data_freshness` is read from `_meta` on
    the SAME dict this run, not from a separate cache.load() round trip.

    Written atomically to disk (tempfile + os.replace) rather than a direct
    write_text — every other cache write is symbol-scoped (one writer per
    file in practice), but the market-wide pseudo-symbol tasks
    (fii_dii_flow/macro_context, see signals/macro.py) can have several
    ThreadPoolExecutor workers race to fill the same cache file on a
    concurrent miss, and a direct write_text's interleaved writes could
    otherwise leave invalid JSON on disk for the next reader.

    Also writes through to Redis when REDIS_URL is set (see module
    docstring) — always in addition to disk, never instead of it, so a
    single-host deployment's persistent state is unaffected either way.
    """
    if _is_failed_payload(data):
        return None
    meta = {"fetched_at": datetime.now(timezone.utc).isoformat()}
    payload = dict(data)
    payload["_meta"] = meta
    serialized = json.dumps(payload, indent=2, ensure_ascii=False)

    client = _get_redis_client()
    if client is not None:
        try:
            ttl_seconds = max(1, int(TTL_HOURS.get(task_name, 1) * 3600))
            client.set(_redis_key(symbol, task_name), serialized, ex=ttl_seconds)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _warn_redis_failure("cache_redis_write_failed", exc)

    p = cache_path(symbol, task_name)
    p.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=p.parent, prefix=f".{task_name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialized)
        os.replace(tmp_path, p)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise
    return meta


def is_fresh(symbol: str, task_name: str) -> bool:
    client = _get_redis_client()
    if client is not None:
        try:
            raw = client.get(_redis_key(symbol, task_name))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _warn_redis_failure("cache_redis_read_failed", exc)
            raw = None
        if raw is not None:
            try:
                data = json.loads(raw)
            except Exception:  # pylint: disable=broad-exception-caught
                return False
            if _is_failed_payload(data):
                return False
            return _is_fresh_payload(data, task_name)

    p = cache_path(symbol, task_name)
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if _is_failed_payload(data):
            return False
    except Exception:
        return False
    return load(symbol, task_name) is not None


def status(symbol: str) -> dict[str, str]:
    """Return a human-readable freshness label for every task."""
    result = {}
    client = _get_redis_client()
    for name in TTL_HOURS:
        raw_text = None
        if client is not None:
            try:
                raw = client.get(_redis_key(symbol, name))
                if raw is not None:
                    raw_text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            except Exception as exc:  # pylint: disable=broad-exception-caught
                _warn_redis_failure("cache_redis_read_failed", exc)

        p = cache_path(symbol, name)
        if raw_text is None and not p.exists():
            result[name] = "missing"
            continue

        data = load(symbol, name)
        try:
            raw_obj = json.loads(raw_text) if raw_text is not None else json.loads(p.read_text(encoding="utf-8"))
            ts = raw_obj.get("_meta", {}).get("fetched_at", "")[:16]
        except Exception:
            ts = "?"
        result[name] = f"fresh  (at {ts})" if data is not None else f"stale  (at {ts})"
    return result
