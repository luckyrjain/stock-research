"""File-based caches for the Market Picks pipeline: the per-source LLM
extraction cache (keyed by source name + article batch, 6 h TTL) and the
final picks-result cache shared by api.py's on-demand SSE endpoint and
market_picks_pipeline.main()'s own CLI entrypoint. Split out of
market_picks_pipeline.py — see that module for the pipeline itself.
"""

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from core.atomic_file import atomic_write_text

# ── Extraction result cache ───────────────────────────────────────────────────
# Avoids re-calling the LLM when the same source serves the same articles
# within a 6-hour window (e.g., on re-runs or cache-bypass rescans).
_EXTRACT_CACHE_DIR = Path("output/_extract_cache")
_EXTRACT_CACHE_TTL = 6 * 3600  # seconds


def _extraction_cache_key(src_name: str, articles: list[dict]) -> str:
    # Content-aware: includes title + url + summary so edits or new articles get a fresh key.
    stable = [
        {"title": a.get("title", ""), "url": a.get("url", ""), "summary": (a.get("summary") or "")[:100]}
        for a in articles
    ]
    text = src_name + json.dumps(stable, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _extraction_cache_get(key: str) -> list[dict] | None:
    path = _EXTRACT_CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if time.time() - data.get("ts", 0) < _EXTRACT_CACHE_TTL:
            return data["picks"]
    except Exception:
        pass
    return None


def _extraction_cache_set(key: str, picks: list[dict]) -> None:
    # Written atomically (tempfile + os.replace), same convention as
    # core/cache.py::save() — _phase_extract runs one of these per source with up
    # to 6 ThreadPoolExecutor workers, and while distinct sources normally
    # write distinct keys, overlapping pipeline runs (e.g. a manual
    # ?force=true firing while a scheduled run is still in flight) can have
    # two writers race on the exact same (source, article-batch) cache key.
    # A direct write_text's interleaved writes could otherwise leave a torn/
    # partial JSON file on disk for the next reader.
    _EXTRACT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps({"ts": time.time(), "picks": picks})
    try:
        atomic_write_text(_EXTRACT_CACHE_DIR / f"{key}.json", serialized)
    except Exception:
        pass


def _prune_extract_cache() -> int:
    """Delete extraction-cache files past their TTL.

    _extraction_cache_get() already treats an expired file as a cache miss on
    read, but nothing removed it from disk — the cache key is content-aware
    (title+url+summary hash), so every distinct batch of articles a source
    ever serves creates a new file, and output/_extract_cache/ grew by one
    file per (source, article-batch) forever. Called once per pipeline run
    (see MarketPicksPipeline.run) rather than per-source, since a directory
    scan is unnecessary overhead to repeat ~20 times in the same run.
    """
    if not _EXTRACT_CACHE_DIR.exists():
        return 0
    removed = 0
    now = time.time()
    for path in _EXTRACT_CACHE_DIR.glob("*.json"):
        try:
            if now - path.stat().st_mtime > _EXTRACT_CACHE_TTL:
                path.unlink()
                removed += 1
        except Exception:
            pass
    return removed


# ── Picks result cache ────────────────────────────────────────────────────────
# Shared by api.py's on-demand SSE endpoint and this module's own CLI
# entrypoint (main(), below) — one source of truth regardless of whether a
# result came from a scheduled run or a user-triggered rescan. The TTL is
# sized to the weekly refresh cadence (see .github/workflows/market-picks-cron.yml)
# plus a day of slack for a delayed run, not to the old "no scheduled job"
# world where 6h was the only staleness bound available.
#
# Note main() is only useful when it runs on the same host/disk as the API
# server (e.g. a self-hosted crontab, same pattern CLAUDE.md documents for
# pipelines/sme_ema_pipeline.py) — market-picks-cron.yml runs on GitHub's own ephemeral
# runners, which don't share a filesystem with wherever the backend is
# actually deployed, so it can't call this script directly and expect the
# result to reach the live site. It instead asks the live backend to run its
# own force-refresh over HTTP; see that workflow file for the full reasoning.
_PICKS_CACHE_PATH = Path("output/_market_picks/picks.json")
_PICKS_CACHE_TTL_HOURS = 192  # 7-day refresh cadence + 24h buffer


def load_picks_cache() -> dict | None:
    if not _PICKS_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(_PICKS_CACHE_PATH.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(data["_meta"]["fetched_at"])
        age_h = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
        return data if age_h <= _PICKS_CACHE_TTL_HOURS else None
    except Exception:
        return None


def picks_cache_status() -> dict:
    """Cache metadata regardless of freshness — last_run_at (the pipeline's own
    generated_at, present whenever the cache file exists at all) and is_fresh
    (per _PICKS_CACHE_TTL_HOURS). Powers api.py's lightweight
    /api/market-picks/status endpoint so the frontend can show a true last-run
    time even once the cache has gone stale — unlike load_picks_cache(), which
    returns None outright once stale since it's used on the actual picks-
    serving path, where "stale" and "absent" should be handled identically.
    """
    if not _PICKS_CACHE_PATH.exists():
        return {"last_run_at": None, "is_fresh": False}
    try:
        data = json.loads(_PICKS_CACHE_PATH.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(data["_meta"]["fetched_at"])
        age_h = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
        return {"last_run_at": data.get("generated_at"), "is_fresh": age_h <= _PICKS_CACHE_TTL_HOURS}
    except Exception:
        return {"last_run_at": None, "is_fresh": False}


def save_picks_cache(picks: list, generated_at: str) -> None:
    try:
        _PICKS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            _PICKS_CACHE_PATH,
            json.dumps({
                "picks":        picks,
                "generated_at": generated_at,
                "_meta":        {"fetched_at": datetime.now(timezone.utc).isoformat()},
            }, indent=2, ensure_ascii=False),
        )
    except Exception:
        pass
