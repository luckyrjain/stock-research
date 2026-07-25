import asyncio
import json
import os
import statistics
import threading
import time
import uuid
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from error_tracking import init_error_tracking
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from observability import get_logger, log_event
from pydantic import BaseModel, Field
from signals.interpreter import interpret

load_dotenv()

# ── Market picks cache ────────────────────────────────────────────────────────
# Defined in market_picks_pipeline.py (the module that owns this pipeline's
# output) so its own cron entrypoint and this file's on-demand SSE endpoint
# read/write the exact same cache — re-exported here under the historical
# names so existing call sites (and test patches targeting api._load_picks_cache
# / api._save_picks_cache) keep working unchanged.
from market_picks_pipeline import load_picks_cache as _load_picks_cache
from market_picks_pipeline import save_picks_cache as _save_picks_cache
import rate_limiter


# ── Global LLM concurrency ceiling ───────────────────────────────────────────
# The per-IP rate limiters (_rate_limit below) only slow down a single IP — they
# don't stop N different IPs (e.g. rotated) from each driving a concurrent,
# expensive LLM pipeline at the same time: a single-stock analyst call, or a
# full market-picks run (up to 35 stocks, dozens of LLM calls). Cap how many
# such pipelines can run at once, regardless of caller or IP. Backed by
# rate_limiter.py — Redis-shared across workers when REDIS_URL is set, an
# in-memory per-process counter otherwise (see docs/deployment.md).
_LLM_CONCURRENCY_LIMIT = int(os.getenv("LLM_CONCURRENCY_LIMIT", "4"))
_LLM_SLOT_NAME = "llm_concurrency"


def _acquire_llm_slot() -> bool:
    """Non-blocking: claims a slot and returns True if one is free, else False.
    Every True must be paired with exactly one _release_llm_slot() call."""
    return rate_limiter.try_acquire_slot(_LLM_SLOT_NAME, _LLM_CONCURRENCY_LIMIT)


def _release_llm_slot() -> None:
    rate_limiter.release_slot(_LLM_SLOT_NAME)


# ── SME signals: shared engine + refresh state ───────────────────────────────
# The SME refresh guard (see refresh_sme_signals below) is now a
# rate_limiter.py lock — Redis-shared across workers when REDIS_URL is set,
# so two workers can no longer both start a refresh at once (previously a
# single-process-only guard; see docs/deployment.md).
_DB_ENGINE = None
_DB_ENGINE_LOCK = threading.Lock()
_SME_REFRESH_LOCK_NAME = "sme_refresh"
_SME_REFRESH_LOCK_TTL_SECONDS = 3600  # generous upper bound on one pipeline run


def _get_db_engine():
    global _DB_ENGINE
    if _DB_ENGINE is None:
        with _DB_ENGINE_LOCK:
            if _DB_ENGINE is None:  # re-check: another thread may have won the race
                from db.models import get_engine
                _DB_ENGINE = get_engine()
    return _DB_ENGINE


# ── Rate limiting ─────────────────────────────────────────────────────────────
# Sliding-window limiter, keyed by (bucket, client IP). Only guards the
# expensive/abusable routes (fresh LLM calls, forced full rescans, forced SME
# pipeline runs). Backed by rate_limiter.py — Redis-shared across workers when
# REDIS_URL is set, an in-memory per-process counter otherwise.


def _check_rate_limit(key: str, max_calls: int, window_seconds: float) -> None:
    if not rate_limiter.is_allowed(key, max_calls, window_seconds):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {max_calls} requests per {int(window_seconds)}s on this endpoint. Try again later.",
        )


def _rate_limit(request: Request, bucket: str, max_calls: int, window_seconds: float) -> None:
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(f"{bucket}:{client_ip}", max_calls, window_seconds)


# A dedicated, explicitly-sized executor for this app's blocking work (LLM
# calls, scraping, yfinance, etc.), set as the event loop's default executor at
# startup. Every `run_in_executor(None, ...)` call in this file then uses it.
# asyncio's own default executor is implicitly sized min(32, cpu_count + 4) —
# fine on a beefy box, but on a small/constrained container that can be just a
# handful of threads shared by *everything* the server does. Sizing it
# explicitly makes capacity predictable. This is deliberately larger than
# _LLM_CONCURRENCY_LIMIT (below) so quick requests (validate, prices,
# sme-signals queries) always have headroom even when every LLM slot is busy.
_EXECUTOR_MAX_WORKERS = int(os.getenv("EXECUTOR_MAX_WORKERS", "16"))


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    executor = ThreadPoolExecutor(max_workers=_EXECUTOR_MAX_WORKERS, thread_name_prefix="api-worker")
    asyncio.get_running_loop().set_default_executor(executor)
    try:
        yield
    finally:
        executor.shutdown(wait=False)


app = FastAPI(title="AlphaPulse", lifespan=_lifespan)
LOGGER = get_logger("api")
init_error_tracking()

# CORS: the browser only ever talks to this backend through the Next.js
# proxy routes (server-to-server fetch, same-origin from the browser's
# perspective) — this is defense in depth against a direct cross-origin
# browser request, not something normal operation relies on. Configure via
# ALLOWED_ORIGINS (comma-separated) for non-default deployments; defaults to
# the local Next.js dev server.
_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.nseindia.com",
    "Accept": "application/json",
}

# ── ISIN resolution (NSE equity master, cached 1 h) ──────────────────────────

_ISIN_CACHE: tuple[float, dict] | None = None


def _is_isin(s: str) -> bool:
    return bool(re.match(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$", s))


_TICKER_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _load_isin_map() -> dict:
    global _ISIN_CACHE
    now = time.monotonic()
    if _ISIN_CACHE and now - _ISIN_CACHE[0] < 3600:
        return _ISIN_CACHE[1]
    import requests
    try:
        r = requests.get(
            "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        mapping: dict = {}
        for line in r.text.splitlines()[1:]:
            parts = line.split(",")
            if len(parts) >= 7:
                sym, company, isin = parts[0].strip(), parts[1].strip(), parts[6].strip()
                if isin and sym:
                    mapping[isin] = {"symbol": sym, "company": company}
        _ISIN_CACHE = (now, mapping)
        return mapping
    except Exception:
        return _ISIN_CACHE[1] if _ISIN_CACHE else {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def _is_name_match(query: str, company: str) -> bool:
    q = _normalize(query)
    c = _normalize(company)
    return q in c or c.startswith(q)


def _quote_meta_sync(symbol: str) -> dict:
    import requests
    try:
        s = requests.Session()
        s.get("https://www.nseindia.com", headers=_NSE_HEADERS, timeout=6)
        r = s.get(
            f"https://www.nseindia.com/api/quote-equity?symbol={symbol}",
            headers=_NSE_HEADERS,
            timeout=6,
        )
        info = r.json().get("info", {})
        return {
            "company": (info.get("companyName") or "").strip(),
            "isin": (info.get("isin") or "").strip(),
        }
    except Exception:
        return {"company": "", "isin": ""}

def _screener_search_sync(query: str) -> list[dict]:
    import requests
    try:
        r = requests.get(
            "https://www.screener.in/api/company/search/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=6,
        )
        return r.json() or []
    except Exception:
        return []


def _screener_company_page_sync(slug: str) -> dict:
    import requests
    import re

    try:
        url = f"https://www.screener.in/company/{slug}/"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
        html = r.text

        def extract(pattern):
            m = re.search(pattern, html)
            return m.group(1).strip() if m else ""

        return {
            "company": extract(r"<h1[^>]*>(.*?)</h1>"),
            "isin": extract(r"isin[/\"].*?([A-Z]{2}[A-Z0-9]{10})"),
            "nse": extract(r"nseindia\.com/get-quotes/equity\?symbol=([A-Z0-9&%-]+)"),
            "bse": extract(r"bseindia\.com/stock-share-price/[^/]+/([A-Z0-9&%-]+)/\d+/"),
        }

    except Exception:
        return {}

def _bse_search_by_isin(isin: str) -> dict:
    import requests
    try:
        r = requests.get(
            "https://api.bseindia.com/BseIndiaAPI/api/GetDataByISIN/w",
            params={"isin": isin},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=6,
        )
        data = r.json()
        return {
            "symbol": data.get("ShortName") or data.get("scripShortName") or str(data.get("ScripCode") or ""),
            "company": data.get("CompanyName") or "",
            "exchange": "BSE",
        }
    except Exception:
        return {}


def _bse_autocomplete_sync(query: str) -> list[dict]:
    """Search for BSE-listed stocks via Screener.in (BSE API returns HTML, not JSON)."""
    import requests
    try:
        r = requests.get(
            "https://www.screener.in/api/company/search/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=6,
        )
        results = r.json() or []
        output = []
        for item in results[:8]:
            # URL is /company/{slug}/ or /company/{slug}/consolidated/ — take second segment
            parts = (item.get("url") or "").strip("/").split("/")
            slug = parts[1] if len(parts) >= 2 else ""
            if not slug:
                continue
            output.append({
                "symbol": slug,
                "company": item.get("name", ""),
                "exchange": "BSE",
                "activeSeries": True,
            })
        return output
    except Exception:
        return []


def _autocomplete_sync(query: str) -> list[dict]:
    import requests
    try:
        s = requests.Session()
        s.get("https://www.nseindia.com", headers=_NSE_HEADERS, timeout=6)
        r = s.get(
            f"https://www.nseindia.com/api/search/autocomplete?q={query}",
            headers=_NSE_HEADERS, timeout=6,
        )
        return r.json().get("symbols", [])
    except Exception:
        return []


def _company_name_from_result(result: dict) -> str:
    for value in (
        result.get("symbol_info"),
        result.get("company"),
        result.get("company_name"),
        result.get("displayName"),
        result.get("name"),
        (result.get("meta") or {}).get("companyName"),
        (result.get("info") or {}).get("companyName"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _heartbeat() -> str:
    return ": heartbeat\n\n"


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return {
        "service": "AlphaPulse API",
        "status": "ok",
        "message": "Use /health for a simple health check, /api/validate/{symbol} to validate a symbol, and /api/analyse/{symbol} to stream analysis events.",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/api/validate/{symbol}")
async def validate_symbol(symbol: str, request: Request, exchange: str = ""):
    _rate_limit(request, "validate", max_calls=30, window_seconds=60)
    sym = symbol.upper().strip()
    loop = asyncio.get_running_loop()

    # ── ISIN PATH ─────────────────────────────────────────────
    if _is_isin(sym):
        # Look up NSE symbol from the equity master CSV
        isin_map = await loop.run_in_executor(None, _load_isin_map)
        nse_entry = isin_map.get(sym)
        if nse_entry:
            # Re-run as a normal NSE symbol lookup to get full metadata
            sym = nse_entry["symbol"]
        else:
            # Not in NSE CSV — try yfinance (supports ISIN lookup natively)
            def _yf_isin_lookup(isin: str) -> dict:
                try:
                    import yfinance as yf
                    info = yf.Ticker(isin).info
                    yf_sym = info.get("symbol", "")
                    if not yf_sym:
                        return {}
                    # yfinance returns "SYMBOL.NS" or "SYMBOL.BO"
                    ticker, _, suffix = yf_sym.partition(".")
                    exchange = "NSE" if suffix == "NS" else "BSE" if suffix == "BO" else "NSE"
                    return {
                        "symbol": ticker,
                        "company": info.get("longName") or info.get("shortName") or "",
                        "exchange": exchange,
                    }
                except Exception:
                    return {}

            isin_str = sym
            yf_result = await loop.run_in_executor(None, _yf_isin_lookup, isin_str)
            if yf_result.get("symbol"):
                sym = yf_result["symbol"]
                # For BSE-only stocks fall through won't hit NSE autocomplete; return directly
                if yf_result["exchange"] == "BSE":
                    return {
                        "found": True,
                        "valid": True,
                        "symbol": sym,
                        "company": yf_result["company"],
                        "exchange": "BSE",
                        "isin": isin_str,
                        "suspended": False,
                        "suggestions": [],
                    }
                # NSE-listed — fall through with resolved symbol for full metadata
            else:
                return {"found": False, "valid": False, "symbol": isin_str, "company": "", "suggestions": []}
        # Fall through with resolved NSE symbol

    # ── BSE-FORCED PATH (user selected a BSE suggestion) ─────
    # sym may be a Screener slug (e.g. "505685" or "TAPARIA-TOOLS") — resolve it
    # to the actual NSE/BSE ticker via the Screener company page.
    if exchange.upper() == "BSE":
        details = await loop.run_in_executor(None, _screener_company_page_sync, sym)
        if details and (details.get("nse") or details.get("bse")):
            proper_sym = details.get("nse") or details.get("bse") or sym
            proper_exchange = "NSE" if details.get("nse") else "BSE"
            return {
                "found": True,
                "valid": True,
                "symbol": proper_sym,
                "company": details.get("company", ""),
                "exchange": proper_exchange,
                "isin": details.get("isin"),
                "suspended": False,
                "suggestions": [],
            }
        return {"found": False, "valid": False, "symbol": sym, "company": "", "suggestions": []}

    # ── STEP 1: NSE autocomplete + BSE autocomplete in parallel ──
    nse_results, bse_results = await asyncio.gather(
        loop.run_in_executor(None, _autocomplete_sync, sym),
        loop.run_in_executor(None, _bse_autocomplete_sync, sym),
    )

    exact_nse = next(
        (r for r in nse_results if r.get("symbol", "").upper() == sym),
        None
    )

    # ── CASE 1: NSE FOUND ────────────────────────────────────
    if exact_nse:
        meta = await loop.run_in_executor(None, _quote_meta_sync, sym)

        company = meta.get("company") or _company_name_from_result(exact_nse)
        isin = meta.get("isin")
        active = bool(exact_nse.get("activeSeries"))

        result = {
            "found": True,
            "valid": active,
            "symbol": exact_nse["symbol"],
            "company": company,
            "exchange": "NSE",
            "isin": isin,
            "suspended": not active,
        }

        if isin:
            bse_data = await loop.run_in_executor(None, _bse_search_by_isin, isin)
            if bse_data.get("symbol"):
                result["bse_symbol"] = bse_data["symbol"]

        # NSE alternatives first, then BSE-only alternatives
        nse_symbols = {r.get("symbol", "").upper() for r in nse_results}
        nse_others = [
            {"symbol": r.get("symbol"), "company": _company_name_from_result(r), "exchange": "NSE"}
            for r in nse_results if r.get("symbol", "").upper() != sym
        ]
        bse_others = [
            {"symbol": r["symbol"], "company": r.get("company", ""), "exchange": "BSE"}
            for r in bse_results
            if r.get("symbol", "").upper() not in nse_symbols
        ]
        result["suggestions"] = (nse_others + bse_others)[:6]
        return result

    # ── CASE 2: NSE FAILED → BSE ─────────────────────────────
    if bse_results:
        match = next(
            (r for r in bse_results if _is_name_match(sym, r.get("company", ""))),
            bse_results[0],
        )
        # Resolve Screener slug (e.g. "505685") to proper ticker (e.g. "TAPARIA")
        slug = match["symbol"]
        details = await loop.run_in_executor(None, _screener_company_page_sync, slug)
        proper_sym = (details.get("nse") or details.get("bse") or slug) if details else slug
        proper_exchange = "NSE" if (details or {}).get("nse") else "BSE"
        company = (details or {}).get("company") or match.get("company", "")

        others = [
            {"symbol": r["symbol"], "company": r.get("company", ""), "exchange": "BSE"}
            for r in bse_results if r.get("symbol", "").upper() != slug.upper()
        ]
        return {
            "found": True,
            "valid": True,
            "symbol": proper_sym,
            "company": company,
            "exchange": proper_exchange,
            "isin": (details or {}).get("isin") or None,
            "suspended": False,
            "suggestions": others[:5],
        }

    # ── CASE 3: SCREENER FALLBACK ─────────────────────────────
    search_results = await loop.run_in_executor(None, _screener_search_sync, sym)

    if search_results:
        best = search_results[0]
        parts = best.get("url", "").strip("/").split("/")
        slug = parts[1] if len(parts) >= 2 else parts[0] if parts else ""
        details = await loop.run_in_executor(None, _screener_company_page_sync, slug)

        if details:
            return {
                "found": True,
                "valid": True,
                "symbol": details.get("nse") or details.get("bse") or sym,
                "company": details.get("company"),
                "exchange": "NSE" if details.get("nse") else "BSE",
                "isin": details.get("isin"),
                "bse_symbol": details.get("bse"),
                "suspended": False,
                "suggestions": [],
            }

    # ── FINAL: NOTHING WORKED ─────────────────────────────────
    return {"found": False, "valid": False, "symbol": sym, "company": "", "suggestions": []}

@app.get("/api/analyse/{symbol}")
async def analyse(symbol: str, request: Request, force: bool = False):
    _rate_limit(request, "analyse", max_calls=20, window_seconds=300)
    sym = symbol.upper().strip()
    if not _TICKER_RE.match(sym):
        raise HTTPException(status_code=422, detail="Invalid symbol.")
    run_id = uuid.uuid4().hex[:12]

    async def stream():
        loop = asyncio.get_running_loop()
        llm_slot_acquired = False
        try:
            import cache
            from crew import ALL_DATA_TASKS
            from main import _fetch_task, _build_report
            from schemas import normalize as schema_normalize, validate as schema_validate
            from signals.engine import run_signal_engine
            from signals.store import save_signal
            from verdict_history import save_snapshot as save_verdict_snapshot

            # ── Determine what needs fetching ─────────────────────────────
            stale = [n for n in ALL_DATA_TASKS if force or not cache.is_fresh(sym, n)]
            cached_data = {n: cache.load(sym, n) for n in ALL_DATA_TASKS if n not in stale}

            log_event(LOGGER, "api_analysis_started", run_id=run_id, symbol=sym, force_refresh=force, stale_tasks=stale)
            yield _sse({"event": "start", "stale": stale, "cached": list(cached_data.keys())})

            # ── Fetch stale tasks concurrently, stream per-task events ────
            fetched: dict = {}
            if stale:
                q: asyncio.Queue = asyncio.Queue()

                async def fetch_one(name: str):
                    try:
                        raw = await loop.run_in_executor(None, _fetch_task, name, sym, run_id)
                        normed = schema_normalize(name, raw)
                        cache.save(sym, name, normed)
                        await q.put({"event": "task_done", "task": name, "ok": True})
                        return name, normed
                    except Exception as exc:
                        await q.put({"event": "task_done", "task": name, "ok": False, "error": str(exc)})
                        return name, {"error": str(exc), "symbol": sym}

                tasks = [asyncio.create_task(fetch_one(n)) for n in stale]
                for _ in stale:
                    yield _sse(await q.get())

                fetched = dict(await asyncio.gather(*tasks))

            all_data = {**cached_data, **fetched}

            # ── Validate ──────────────────────────────────────────────────
            ok, err = schema_validate("stock_info", all_data.get("stock_info", {}))
            if not ok:
                yield _sse({"event": "error", "message": f"Symbol not valid: {err}"})
                return

            # run_signal_engine now does its own (cached) network I/O for the
            # technical signal — must not run directly on the event loop.
            signal_result = await loop.run_in_executor(None, run_signal_engine, sym, all_data)
            signal_insight = interpret(signal_result)
            save_signal(signal_result)
            signal_context = {
                "final_score": signal_result.final_score,
                "verdict": signal_result.verdict,
                "insight": signal_insight,
                "signals": {k: v.__dict__ for k, v in signal_result.signals.items()},
            }

            # ── Run analyst (LLM, slow) ───────────────────────────────────
            run_analysis = bool(stale) or not cache.is_fresh(sym, "analysis")
            analysis: dict = {}

            if run_analysis:
                if not _acquire_llm_slot():
                    log_event(LOGGER, "analyst_capacity_rejected", level="warning", run_id=run_id, symbol=sym)
                    yield _sse({
                        "event": "error",
                        "message": "The AI analyst is at capacity right now — please try again in a moment.",
                    })
                    return
                llm_slot_acquired = True

                yield _sse({"event": "analysing"})

                def _run_analyst():
                    from crew import run_analysis_with_fallback
                    return run_analysis_with_fallback(
                        sym, all_data, signal_context=signal_context, run_id=run_id
                    )

                # Run analyst in thread; send heartbeats so the connection stays alive
                done_q: asyncio.Queue = asyncio.Queue()

                async def _run_and_signal():
                    try:
                        result = await loop.run_in_executor(None, _run_analyst)
                    except Exception as exc:
                        # Never let the background task fail silently; convert it into
                        # a structured payload so the SSE loop can respond cleanly.
                        # str(exc) is logged (not returned to the client) — see the
                        # api_analysis_failed-style logging convention used elsewhere.
                        log_event(
                            LOGGER, "analyst_background_task_failed", level="error",
                            run_id=run_id, symbol=sym, error=str(exc),
                        )
                        result = {
                            "symbol": sym,
                            "recommendation": "HOLD",
                            "confidence": "LOW",
                            "_degraded": True,
                            "summary": (
                                f"Automated analysis for {sym} failed while running in the background. "
                                "A safe fallback response was returned instead of terminating the stream."
                            ),
                            "valuation": {
                                "verdict": "Fairly Valued",
                                "comment": "Analyst execution failed before structured valuation output was produced.",
                            },
                            "business_quality": "Structured business-quality commentary was unavailable because the analyst task failed.",
                            "bull_factors": [
                                "Fetched stock data remains available.",
                                "The request completed without crashing the SSE stream.",
                                "A safe fallback recommendation was generated automatically.",
                            ],
                            "bear_factors": [
                                "The analyst task raised an exception before returning valid JSON.",
                                "Recommendation confidence is reduced because structured reasoning was unavailable.",
                            ],
                            "key_risks": [
                                "Analyst execution failed during response generation.",
                                "Qualitative conclusions may be incomplete until the analysis is rerun.",
                                "Manual review is recommended before acting on the fallback output.",
                            ],
                            "news_sentiment": "Neutral",
                            "news_highlights": "News commentary was unavailable because the analyst task failed.",
                            "institutional_trend": "Institutional trend commentary was unavailable because the analyst task failed.",
                        }
                    await done_q.put(result)

                asyncio.create_task(_run_and_signal())

                try:
                    while True:
                        try:
                            analysis = await asyncio.wait_for(done_q.get(), timeout=15)
                            break
                        except asyncio.TimeoutError:
                            yield _heartbeat()
                finally:
                    _release_llm_slot()
                    llm_slot_acquired = False

                cache.save(sym, "analysis", analysis)
            else:
                analysis = cache.load(sym, "analysis") or {}

            report = _build_report(sym, all_data, analysis, signal_context)
            # Fire-and-forget: verdict_history is a best-effort side effect (it
            # already logs and swallows its own failures) that the client isn't
            # waiting on, so it must not add a DB round-trip to the response's
            # critical path — not awaited here.
            loop.run_in_executor(
                None, save_verdict_snapshot, sym, analysis, signal_context, all_data.get("stock_info") or {}
            )
            log_event(LOGGER, "api_analysis_completed", run_id=run_id, symbol=sym)
            yield _sse({"event": "done", "report": report})

        except Exception as exc:
            log_event(LOGGER, "api_analysis_failed", level="error", run_id=run_id, symbol=sym, error=str(exc))
            yield _sse({"event": "error", "message": str(exc)})
        finally:
            if llm_slot_acquired:
                _release_llm_slot()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/market-picks")
async def market_picks(request: Request, force: bool = Query(default=False)):
    """Stream market-picks pipeline events as SSE.

    ?force=true  — skip cache and run a fresh pipeline.
    """
    if force:
        # Only the cache-bypassing path is rate-limited — normal cached reads are cheap.
        _rate_limit(request, "market_picks_force", max_calls=3, window_seconds=3600)
    run_id = uuid.uuid4().hex[:12]

    async def stream():
        # ── Serve from cache if fresh and caller didn't force a rescan ──
        if not force:
            cached = _load_picks_cache()
            if cached:
                log_event(LOGGER, "market_picks_cache_hit", run_id=run_id)
                yield _sse({
                    "event":        "done",
                    "picks":        cached["picks"],
                    "generated_at": cached["generated_at"],
                    "total_picks":  len(cached["picks"]),
                    "from_cache":   True,
                })
                return

        # ── Full pipeline run ─────────────────────────────────────────────
        # A market-picks run makes dozens of LLM calls (one per source's extraction,
        # plus batched analysis) — it takes one global slot for its whole duration,
        # same as a single-stock analyst call, so it counts against the same ceiling.
        if not _acquire_llm_slot():
            log_event(LOGGER, "market_picks_capacity_rejected", level="warning", run_id=run_id)
            yield _sse({
                "event": "error",
                "message": "The server is at capacity for AI-driven pipelines right now — please try again in a moment.",
            })
            return

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        def on_event(payload: dict):
            loop.call_soon_threadsafe(q.put_nowait, payload)

        def run_pipeline():
            try:
                from market_picks_pipeline import MarketPicksPipeline
                pipeline = MarketPicksPipeline()
                picks = pipeline.run(on_event=on_event)
                generated_at = datetime.now(timezone.utc).isoformat()
                # A pipeline run that completes without raising but yields zero picks, or
                # that pipeline.healthy flags as substantially degraded (e.g. most scrape
                # sources failed simultaneously), must not be cached — caching it would
                # serve that broken result as "fresh" for 6h and mask the outage from
                # every subsequent visitor.
                if picks and pipeline.healthy:
                    _save_picks_cache(picks, generated_at)
                else:
                    log_event(
                        LOGGER, "market_picks_degraded_result_not_cached", level="warning",
                        run_id=run_id, picks=len(picks), healthy=pipeline.healthy,
                    )
                loop.call_soon_threadsafe(q.put_nowait, {
                    "event":        "done",
                    "picks":        picks,
                    "generated_at": generated_at,
                    "total_picks":  len(picks),
                    "from_cache":   False,
                })
            except Exception as exc:
                log_event(LOGGER, "market_picks_failed", level="error", run_id=run_id, error=str(exc))
                loop.call_soon_threadsafe(q.put_nowait, {"event": "error", "message": str(exc)})
            finally:
                _release_llm_slot()

        log_event(LOGGER, "market_picks_started", run_id=run_id)

        async def _launch():
            await loop.run_in_executor(None, run_pipeline)

        asyncio.create_task(_launch())

        while True:
            try:
                payload = await asyncio.wait_for(q.get(), timeout=20.0)
                yield _sse(payload)
                if payload.get("event") in ("done", "error"):
                    break
            except asyncio.TimeoutError:
                yield _heartbeat()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Mirrors the schedule in .github/workflows/market-picks-cron.yml
# ('30 1 * * 1' — Monday 01:30 UTC). Kept in sync by hand; there's no way to
# share one source of truth between a GitHub Actions cron expression and this
# Python computation.
_MARKET_PICKS_CRON_WEEKDAY     = 0   # Monday, per datetime.weekday()'s convention (Mon=0..Sun=6)
_MARKET_PICKS_CRON_HOUR_UTC    = 1
_MARKET_PICKS_CRON_MINUTE_UTC  = 30


def _next_scheduled_market_picks_run(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    candidate = now.replace(
        hour=_MARKET_PICKS_CRON_HOUR_UTC, minute=_MARKET_PICKS_CRON_MINUTE_UTC,
        second=0, microsecond=0,
    )
    days_ahead = (_MARKET_PICKS_CRON_WEEKDAY - now.weekday()) % 7
    candidate += timedelta(days=days_ahead)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


@app.get("/api/market-picks/status")
async def get_market_picks_status(request: Request):
    """Cache metadata only — no pipeline run. Lets the idle /market-picks hero
    show a true last-run time and the next scheduled refresh instead of an
    unverifiable "every week" claim with no way to know whether anything
    actually ran on schedule versus just being served from cache.
    """
    _rate_limit(request, "market_picks_status", max_calls=60, window_seconds=60)

    from market_picks_pipeline import picks_cache_status

    loop = asyncio.get_running_loop()
    status = await loop.run_in_executor(None, picks_cache_status)
    return {
        "last_run_at":       status["last_run_at"],
        "cache_fresh":       status["is_fresh"],
        "next_scheduled_at": _next_scheduled_market_picks_run().isoformat(),
    }


_PICKS_HISTORY_DIR = Path("output/_history")


def _fetch_nifty_closes(start_date: str, end_date: str) -> dict[str, float]:
    """^NSEI daily closes covering [start_date, end_date], keyed by ISO date.
    One yfinance request for the whole range rather than one per snapshot
    date — cheaper and just as complete, since a ranged history() call already
    returns every trading day's close in between. Cached via cache.py using
    'NSEI' as a pseudo-symbol (there's no real per-symbol subject here, just
    one shared index series) — 24h TTL, keyed to the exact range requested so
    a newly-added snapshot date (which grows the range) forces a fresh fetch
    rather than serving a stale range that doesn't cover it yet.
    Best-effort: a yfinance outage degrades to {} so the endpoint still
    returns — the alpha column/stat is simply omitted, same as any other
    "a hiccup in one section must not fail the rest" section in this file.
    """
    import cache

    range_key = f"{start_date}:{end_date}"
    cached = cache.load("NSEI", "index_history")
    if cached and cached.get("range") == range_key:
        return cached.get("closes", {})

    try:
        import yfinance as yf
        end_exclusive = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        hist = yf.Ticker("^NSEI").history(start=start_date, end=end_exclusive, interval="1d")
        closes = {
            idx.strftime("%Y-%m-%d"): round(float(close), 2)
            for idx, close in hist["Close"].items()
        }
    except Exception:
        return {}

    if closes:
        cache.save("NSEI", "index_history", {"range": range_key, "closes": closes})
    return closes


def _nifty_close_on_or_before(closes: dict[str, float], date_str: str) -> float | None:
    """Nearest available Nifty close at or before `date_str` (a snapshot date
    could fall on a weekend/market holiday) — never a later, look-ahead date."""
    candidates = [d for d in closes if d <= date_str]
    return closes[max(candidates)] if candidates else None


@app.get("/api/market-picks/history")
async def get_market_picks_history(request: Request, date: str | None = Query(None)):
    """Aggregate output/_history/<date>.json daily snapshots into a per-symbol
    track record: first/last seen, confidence trend, and price performance
    since first seen, benchmarked against the Nifty50 over the same window.
    Price/recommendation were only added to the snapshot schema recently —
    older snapshot files won't have them, so change_pct (and anything derived
    from it) is null wherever price_then or price_now is missing rather than
    guessed at.

    With ?date=YYYY-MM-DD: skips aggregation entirely and returns that single
    day's full pick list verbatim (same shape market_picks_pipeline._save_history
    wrote it in) — the aggregated view above only ever surfaces a first/last-seen
    roll-up per symbol, never a specific day's complete list. 404 if no snapshot
    was taken that day (weekend, holiday, or before this feature existed).
    """
    _rate_limit(request, "market_picks_history", max_calls=60, window_seconds=60)

    if date is not None:
        if not _DATE_RE.match(date):
            raise HTTPException(status_code=422, detail="date must be in YYYY-MM-DD format")

        def _load_day_sync() -> dict | None:
            path = _PICKS_HISTORY_DIR / f"{date}.json"
            if not path.exists():
                return None
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
            return {"date": data.get("date", date), "picks": data.get("picks", [])}

        loop = asyncio.get_running_loop()
        day_result = await loop.run_in_executor(None, _load_day_sync)
        if day_result is None:
            raise HTTPException(status_code=404, detail=f"No market-picks snapshot found for {date}")
        return day_result

    def _load_sync() -> dict:
        empty = {
            "symbols": [], "snapshot_count": 0,
            "win_rate": None, "tier_stats": {}, "avg_alpha_pct": None,
            "available_dates": [],
        }
        if not _PICKS_HISTORY_DIR.exists():
            return empty

        by_symbol: dict[str, list[dict]] = {}
        snapshot_count = 0
        min_date: str | None = None
        max_date: str | None = None
        available_dates: list[str] = []
        for path in sorted(_PICKS_HISTORY_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            date_str = data.get("date", path.stem)
            snapshot_count += 1
            available_dates.append(date_str)
            min_date = date_str if min_date is None else min(min_date, date_str)
            max_date = date_str if max_date is None else max(max_date, date_str)
            for row in data.get("picks", []):
                sym = row.get("symbol")
                if not sym:
                    continue
                by_symbol.setdefault(sym, []).append({**row, "date": date_str})

        if not by_symbol:
            return {**empty, "snapshot_count": snapshot_count, "available_dates": available_dates}

        nifty_closes = _fetch_nifty_closes(min_date, max_date)

        symbols = []
        tier_buckets: dict[str, list[float]] = {}
        alphas: list[float] = []
        for sym, rows in by_symbol.items():
            rows.sort(key=lambda r: r["date"])
            first, last = rows[0], rows[-1]
            price_then = first.get("current_price")
            price_now = last.get("current_price")
            change_pct = (
                round((price_now - price_then) / price_then * 100, 2)
                if price_then and price_now
                else None
            )

            nifty_then = _nifty_close_on_or_before(nifty_closes, first["date"])
            nifty_now = _nifty_close_on_or_before(nifty_closes, last["date"])
            nifty_change_pct = (
                round((nifty_now - nifty_then) / nifty_then * 100, 2)
                if nifty_then and nifty_now
                else None
            )
            alpha_pct = (
                round(change_pct - nifty_change_pct, 2)
                if change_pct is not None and nifty_change_pct is not None
                else None
            )
            if alpha_pct is not None:
                alphas.append(alpha_pct)

            recommendation_then = first.get("recommendation")
            if change_pct is not None and recommendation_then:
                tier_buckets.setdefault(recommendation_then, []).append(change_pct)

            symbols.append({
                "symbol":              sym,
                "first_seen":          first["date"],
                "last_seen":           last["date"],
                "times_picked":        len(rows),
                "recommendation_then": recommendation_then,
                "recommendation_now":  last.get("recommendation"),
                "price_then":          price_then,
                "price_now":           price_now,
                "change_pct":          change_pct,
                "confidence_then":     first.get("confidence"),
                "confidence_now":      last.get("confidence"),
                "nifty_change_pct":    nifty_change_pct,
                "alpha_pct":           alpha_pct,
            })

        # Symbols with a computed change_pct first (best performers first), then
        # the rest (no price data yet) grouped at the end.
        symbols.sort(key=lambda s: (s["change_pct"] is None, -(s["change_pct"] or 0)))

        changes_with_data = [s["change_pct"] for s in symbols if s["change_pct"] is not None]
        win_rate = (
            round(sum(1 for c in changes_with_data if c > 0) / len(changes_with_data) * 100, 1)
            if changes_with_data else None
        )
        tier_stats = {
            tier: {
                "count":          len(vals),
                "avg_change_pct": round(sum(vals) / len(vals), 2),
                "win_rate":       round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
            }
            for tier, vals in tier_buckets.items()
        }
        avg_alpha_pct = round(sum(alphas) / len(alphas), 2) if alphas else None

        return {
            "symbols":         symbols,
            "snapshot_count":  snapshot_count,
            "win_rate":        win_rate,
            "tier_stats":      tier_stats,
            "avg_alpha_pct":   avg_alpha_pct,
            "available_dates": available_dates,
        }

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _load_sync)


def _fetch_live_price_sync(sym: str) -> dict:
    """LTP + day change% for one NSE/BSE symbol via yfinance, trying the .NS
    then .BO suffix. Returns {} (never raises) if neither resolves — shared by
    GET /api/prices (bulk) and GET /api/verdict-history/{symbol} (single-symbol,
    for scoring past verdicts against today's price)."""
    try:
        import yfinance as yf
        for suffix in (".NS", ".BO"):
            fi = yf.Ticker(sym + suffix).fast_info
            price = getattr(fi, "last_price", None)
            prev  = getattr(fi, "previous_close", None)
            if price and price > 0:
                chg = round((price - prev) / prev * 100, 2) if prev else 0.0
                return {"price": round(price, 2), "change_pct": chg}
    except Exception:
        pass
    return {}


@app.get("/api/prices")
async def get_prices(request: Request, symbols: str = Query(...)):
    """Return LTP + day change% for a comma-separated list of NSE/BSE symbols."""
    # Up to 50 symbols means up to 50 yfinance calls per request — the biggest
    # amplification factor of any endpoint here, so it gets the tightest limit.
    _rate_limit(request, "prices", max_calls=30, window_seconds=60)
    sym_list = [s.strip().upper() for s in symbols.split(",") if _TICKER_RE.match(s.strip())][:50]
    loop = asyncio.get_running_loop()

    def _fetch_one(sym: str) -> tuple[str, dict]:
        return sym, _fetch_live_price_sync(sym)

    results = await asyncio.gather(
        *[loop.run_in_executor(None, _fetch_one, s) for s in sym_list]
    )
    return {"prices": dict(results)}


@app.get("/api/prices/history/{symbol}")
async def get_price_history(request: Request, symbol: str, days: int = Query(180, ge=7, le=365)):
    """Return a daily-close series for sparklines. Cached like the six data
    slices (6 h TTL) but intentionally outside ALL_DATA_TASKS — this is a
    standalone, on-demand series, not part of the six-task analysis pipeline.
    """
    _rate_limit(request, "prices_history", max_calls=60, window_seconds=60)
    sym = symbol.upper().strip()
    if not _TICKER_RE.match(sym):
        raise HTTPException(status_code=422, detail="Invalid symbol.")
    from tools.price_history_tools import get_price_series

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_price_series, sym, days)


def _parse_peer_numeric(value) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError):
        return None


def _compute_peer_percentiles(self_row: dict | None, peer_rows: list[dict]) -> dict[str, float]:
    """For every ratio column present in self_row's values AND at least one peer's
    values, return self's percentile rank (0-100) among [self] + peers for that
    column — a plain rank-based percentile, not a distribution fit. Columns
    Screener doesn't expose for this sector (or that no peer reports) are simply
    absent from the result, never guessed or backfilled."""
    if not self_row or not peer_rows:
        return {}

    percentiles: dict[str, float] = {}
    for col, raw_self in self_row.get("values", {}).items():
        self_num = _parse_peer_numeric(raw_self)
        if self_num is None:
            continue
        peer_nums = [
            n for n in (_parse_peer_numeric(p.get("values", {}).get(col)) for p in peer_rows)
            if n is not None
        ]
        if not peer_nums:
            continue
        all_values = peer_nums + [self_num]
        below = sum(1 for v in all_values if v < self_num)
        equal = sum(1 for v in all_values if v == self_num)
        percentiles[col] = round((below + 0.5 * equal) / len(all_values) * 100, 1)
    return percentiles


def _compute_valuation_anchor(self_row: dict | None, valuation_band: dict) -> dict | None:
    """Absolute valuation anchor: where the stock's current P/E sits within its
    OWN last 3-5 years of Screener-published Price to Earning values — a real
    band from real yearly data, not a sector benchmark this codebase has no
    business inventing (config/analyst.json already forbids the LLM from doing
    that; this closes the same gap for the peer-percentile card, which only
    ever said "cheap/expensive vs. peers," never "cheap/expensive vs. its own
    history"). None when `self_row` has no parseable current P/E, or
    `valuation_band` has fewer than 3 years on record (see
    tools/screener_tools.py::_extract_valuation_band) — never guessed.

    Percentile math mirrors `_compute_peer_percentiles`' mean-rank formula,
    but the population it ranks against differs on purpose: `current_pe` is
    today's live snapshot, not itself one of the historical yearly
    observations, so it is ranked against `pe_values` alone rather than
    folded into that population the way `self` is folded into the peer set
    below."""
    if not self_row or not valuation_band:
        return None
    pe_values = valuation_band.get("pe") or []
    if len(pe_values) < 3:
        return None

    current_pe = None
    for col, raw in self_row.get("values", {}).items():
        normalized = col.lower().replace(" ", "")
        if "p/e" in normalized or normalized == "pe":
            current_pe = _parse_peer_numeric(raw)
            break
    if current_pe is None:
        return None

    below = sum(1 for v in pe_values if v < current_pe)
    equal = sum(1 for v in pe_values if v == current_pe)
    percentile = round((below + 0.5 * equal) / len(pe_values) * 100, 1)

    return {
        "current_pe": current_pe,
        "years": valuation_band.get("years", []),
        "pe_history": pe_values,
        "low": min(pe_values),
        "median": statistics.median(pe_values),
        "high": max(pe_values),
        "percentile": percentile,
    }


@app.get("/api/peers/{symbol}")
async def get_peers(request: Request, symbol: str):
    """Peer comparison table scraped from Screener.in (the company plus 3-5 sector
    peers, and Screener's own sector-median row where present), with percentile
    badges for whichever ratio columns are present for both the company and its
    peers, plus an `absolute_anchor` (see _compute_valuation_anchor) showing
    where the stock's current P/E sits within its own last 3-5 years of
    Screener-published P/E — null when that history isn't available. Cached
    like the six data slices (24 h TTL) but intentionally outside
    ALL_DATA_TASKS — this is a standalone, on-demand comparison, not part of the
    six-task analysis pipeline.
    """
    _rate_limit(request, "peers", max_calls=30, window_seconds=60)
    sym = symbol.upper().strip()
    if not _TICKER_RE.match(sym):
        raise HTTPException(status_code=422, detail="Invalid symbol.")

    def _fetch_sync() -> dict:
        import cache

        cached = cache.load(sym, "peers")
        if cached is not None:
            # A response cached before absolute_anchor existed won't have the
            # key at all (vs. a fresh response's explicit None) — backfill so
            # every response has a consistent, self-describing shape rather
            # than relying on the frontend treating undefined == null.
            return {"absolute_anchor": None, **{k: v for k, v in cached.items() if k != "_meta"}}

        from tools.screener_tools import get_peer_comparison

        raw = json.loads(get_peer_comparison.run(symbol=sym))
        if raw.get("error"):
            return {
                "symbol": sym, "self": None, "peers": [], "sector_median": None,
                "percentiles": {}, "absolute_anchor": None,
            }

        result = {
            "symbol":          raw.get("symbol", sym),
            "self":            raw.get("self"),
            "peers":           raw.get("peers", []),
            "sector_median":   raw.get("sector_median"),
            "percentiles":     _compute_peer_percentiles(raw.get("self"), raw.get("peers", [])),
            "absolute_anchor": _compute_valuation_anchor(raw.get("self"), raw.get("valuation_band") or {}),
        }
        cache.save(sym, "peers", result)
        return result

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_sync)


@app.get("/api/insider-activity/{symbol}")
async def get_insider_activity(request: Request, symbol: str):
    """Structured promoter/director insider trades and bulk/block deals for
    one symbol — the same NSE feeds tools/nse_insider_trades.py and
    tools/nse_bulk_block_deals.py already scrape for the Market Picks
    discovery pipeline, wired here as their own on-demand endpoint so a
    researcher looking up one specific stock can see this activity directly
    instead of it only ever surfacing (via LLM extraction) when a stock
    happens to make the weekly picks list. Cached like peers/price-history
    (24 h TTL) but intentionally outside ALL_DATA_TASKS — standalone and
    on-demand, not part of the six-task analysis pipeline. Absent rather than
    guessed: most stocks simply have no recent insider/bulk activity, which
    is the expected common case, not an error.
    """
    _rate_limit(request, "insider_activity", max_calls=30, window_seconds=60)
    sym = symbol.upper().strip()
    if not _TICKER_RE.match(sym):
        raise HTTPException(status_code=422, detail="Invalid symbol.")

    def _load_cached() -> dict | None:
        import cache

        cached = cache.load(sym, "insider_activity")
        return {k: v for k, v in cached.items() if k != "_meta"} if cached is not None else None

    loop = asyncio.get_running_loop()
    cached = await loop.run_in_executor(None, _load_cached)
    if cached is not None:
        return cached

    def _fetch_insider() -> list[dict]:
        # Both underlying tool functions are documented to never raise, but
        # this endpoint doesn't lean on that alone — a future change to
        # either one shouldn't be able to take down the *other* section, the
        # same per-section isolation _consolidated_payload uses.
        try:
            from tools.nse_insider_trades import fetch_insider_trades_for_symbol

            return fetch_insider_trades_for_symbol(sym).get("trades", [])
        except Exception as exc:
            log_event(LOGGER, "insider_trades_fetch_failed", level="warning", symbol=sym, error=str(exc))
            return []

    def _fetch_bulk_block() -> list[dict]:
        try:
            from tools.nse_bulk_block_deals import fetch_bulk_block_deals_for_symbol

            return fetch_bulk_block_deals_for_symbol(sym).get("deals", [])
        except Exception as exc:
            log_event(LOGGER, "bulk_block_deals_fetch_failed", level="warning", symbol=sym, error=str(exc))
            return []

    # Two independent NSE endpoints — fetch concurrently rather than one
    # after the other, same spirit as _consolidated_payload's parallel lookups.
    insider_trades, bulk_block_deals = await asyncio.gather(
        loop.run_in_executor(None, _fetch_insider),
        loop.run_in_executor(None, _fetch_bulk_block),
    )
    result = {"symbol": sym, "insider_trades": insider_trades, "bulk_block_deals": bulk_block_deals}

    def _save_cache() -> None:
        import cache

        cache.save(sym, "insider_activity", result)

    await loop.run_in_executor(None, _save_cache)
    return result


@app.get("/api/street-consensus/{symbol}")
async def get_street_consensus(request: Request, symbol: str):
    """Recent Trendlyne-cited analyst commentary for one symbol —
    tools/trendlyne_agent.py already scrapes this (via targeted GNews
    queries, not trendlyne.com directly) for the Market Picks pipeline;
    this is the same per-stock, on-demand pattern as
    GET /api/insider-activity/{symbol}. Deliberately a list of real
    article titles/links/dates, never a fabricated consensus rating or
    target price number — this module doesn't have that data, only
    articles that mention it. Cached (24 h TTL) but intentionally outside
    ALL_DATA_TASKS. Empty articles list (never an error) is the expected
    common case for most stocks on most days.
    """
    _rate_limit(request, "street_consensus", max_calls=30, window_seconds=60)
    sym = symbol.upper().strip()
    if not _TICKER_RE.match(sym):
        raise HTTPException(status_code=422, detail="Invalid symbol.")

    def _fetch_sync() -> dict:
        import cache

        cached = cache.load(sym, "street_consensus")
        if cached is not None:
            return {k: v for k, v in cached.items() if k != "_meta"}

        from tools.trendlyne_agent import fetch_trendlyne_consensus_for_symbol

        result = fetch_trendlyne_consensus_for_symbol(sym)
        cache.save(sym, "street_consensus", result)
        return result

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_sync)


def _score_verdict_history(history: list[dict], live_price: float | None) -> list[dict]:
    """Attaches return_since_pct/outcome to each stored verdict, scored against
    today's live price — "how has AlphaPulse's own call on this stock done so
    far", the single-stock analogue of what /api/market-picks/history already
    computes for the weekly picks list.

    Only BUY/SELL entries are scored: a HOLD makes no directional claim, so
    grading it against a price move would be inventing a judgment the verdict
    itself never made — same "never invent" instinct as the rest of this
    codebase, just applied to a derived field instead of a scraped one.
    `return_since_pct` is still populated for every entry with a stored price,
    regardless of recommendation, since that's an observed fact, not a
    verdict-dependent judgment."""
    scored = []
    for entry in history:
        entry_price = entry.get("current_price")
        return_pct = None
        if entry_price and live_price:
            return_pct = round((live_price - entry_price) / entry_price * 100, 2)

        outcome = None
        rec = entry.get("recommendation")
        if return_pct is not None and rec in ("BUY", "SELL"):
            if return_pct == 0:
                outcome = None  # perfectly flat — no directional signal to grade
            elif rec == "BUY":
                outcome = "win" if return_pct > 0 else "loss"
            else:  # SELL
                outcome = "win" if return_pct < 0 else "loss"

        scored.append({**entry, "return_since_pct": return_pct, "outcome": outcome})
    return scored


@app.get("/api/verdict-history/{symbol}")
async def get_verdict_history(request: Request, symbol: str):
    """The per-day verdict/price snapshots verdict_history.save_snapshot() writes
    after every analysis run (CLI or web), letting the frontend show a "how has
    our call on this stock changed over time" strip without any new LLM or
    scraping work. Read-only aggregation, same spirit as /api/consolidated: an
    empty history (DATABASE_URL unset, no prior runs for this symbol, or a DB
    hiccup) degrades to an empty list rather than a 500/503 — the hero's
    timeline strip just doesn't render, the same as peers/price-history when
    their own data isn't available yet.

    Each entry is additionally scored against today's live price
    (return_since_pct/outcome — see _score_verdict_history) so the timeline
    shows not just what AlphaPulse called, but whether it was right so far.
    A live-price fetch failure degrades those two fields to null on every
    entry rather than failing the whole response — the un-scored history is
    still useful on its own.
    """
    _rate_limit(request, "verdict_history", max_calls=60, window_seconds=60)
    sym = symbol.upper().strip()
    if not _TICKER_RE.match(sym):
        raise HTTPException(status_code=422, detail="Invalid symbol.")

    from verdict_history import load_history

    loop = asyncio.get_running_loop()
    history = await loop.run_in_executor(None, load_history, sym)
    # The frontend's VerdictTimeline needs at least 2 entries to render at
    # all — skip the extra yfinance round trip entirely below that, since
    # nothing would consume the scored fields anyway.
    live_price = None
    if len(history) >= 2:
        live = await loop.run_in_executor(None, _fetch_live_price_sync, sym)
        live_price = live.get("price")
    scored = _score_verdict_history(history, live_price)
    win_count = sum(1 for e in scored if e["outcome"] == "win")
    scored_count = sum(1 for e in scored if e["outcome"] is not None)
    win_rate = round(win_count / scored_count * 100, 1) if scored_count else None
    return {
        "symbol": sym,
        "history": scored,
        "win_rate": win_rate,
        "scored_count": scored_count,
    }


_HIT_RATE_LOOKBACK_DAYS       = 90   # window of golden crosses considered
_HIT_RATE_FORWARD_TRADING_DAYS = 20  # "follow-through" horizon, matching the
                                      # +20d window used elsewhere (cross_events)


@app.get("/api/sme-signals")
async def get_sme_signals(
    request: Request,
    lookback:  int = Query(5, ge=1, le=30, description="Days back to check for crosses"),
    direction: str = Query("all", description="all | golden | death"),
    view:      str = Query("crosses", description="crosses | regime"),
):
    """Return SME stocks with an EMA20/EMA50 golden or death cross in the last N days
    (view=crosses, the default), or the latest row for every monitored stock regardless
    of whether it has crossed recently (view=regime) — the "golden-now" stat in the
    default view has no way to say which specific stocks make it up without this;
    lookback/direction are ignored in regime view since there's no cross-event window
    to filter by.
    """
    import os

    _rate_limit(request, "sme_signals", max_calls=60, window_seconds=60)

    if direction not in ("all", "golden", "death"):
        raise HTTPException(status_code=422, detail="direction must be one of: all, golden, death")
    if view not in ("crosses", "regime"):
        raise HTTPException(status_code=422, detail="view must be one of: crosses, regime")
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured. Run the SME pipeline first.")

    def _query_sync() -> dict:
        from sqlalchemy import text as _text

        engine = _get_db_engine()
        with engine.connect() as conn:
            if view == "regime":
                rows = conn.execute(_text("""
                    SELECT DISTINCT ON (s.symbol)
                        s.symbol,
                        s.name,
                        s.exchange,
                        s.isin,
                        s.avg_volume_20d::float   AS avg_volume_20d,
                        s.avg_turnover_20d::float AS avg_turnover_20d,
                        s.market_cap_cr::float    AS market_cap_cr,
                        e.trade_date::text   AS trade_date,
                        e.close_price::float AS close_price,
                        e.ema20::float       AS ema20,
                        e.ema50::float       AS ema50,
                        e.rsi14::float       AS rsi14,
                        e.volume_spike       AS volume_spike,
                        e.cross_type         AS "cross",
                        (e.ema20 > e.ema50)  AS in_golden_cross
                    FROM ema_signals e
                    JOIN sme_stocks s USING (symbol)
                    ORDER BY s.symbol, e.trade_date DESC
                """)).mappings().fetchall()
            else:
                rows = conn.execute(_text("""
                    WITH latest AS (
                        SELECT DISTINCT ON (symbol) symbol, (ema20 > ema50) AS in_golden_cross
                        FROM ema_signals
                        ORDER BY symbol, trade_date DESC
                    )
                    SELECT
                        s.symbol,
                        s.name,
                        s.exchange,
                        s.isin,
                        s.avg_volume_20d::float   AS avg_volume_20d,
                        s.avg_turnover_20d::float AS avg_turnover_20d,
                        s.market_cap_cr::float    AS market_cap_cr,
                        e.trade_date::text   AS trade_date,
                        e.close_price::float AS close_price,
                        e.ema20::float       AS ema20,
                        e.ema50::float       AS ema50,
                        e.rsi14::float       AS rsi14,
                        e.volume_spike       AS volume_spike,
                        e.cross_type         AS "cross",
                        COALESCE(l.in_golden_cross, FALSE) AS in_golden_cross
                    FROM ema_signals e
                    JOIN sme_stocks  s USING (symbol)
                    LEFT JOIN latest l USING (symbol)
                    WHERE e.cross_type IS NOT NULL
                      AND (:direction = 'all' OR e.cross_type = :direction)
                      AND e.trade_date >= CURRENT_DATE - (:lookback * INTERVAL '1 day')
                    ORDER BY e.trade_date DESC, s.symbol
                """), {"lookback": lookback, "direction": direction}).mappings().fetchall()

            total_monitored = conn.execute(
                _text("SELECT COUNT(*) FROM sme_stocks")
            ).scalar() or 0

            golden_now = conn.execute(_text("""
                SELECT COUNT(*) FROM (
                    SELECT DISTINCT ON (symbol) (ema20 > ema50) AS ig
                    FROM ema_signals
                    ORDER BY symbol, trade_date DESC
                ) t WHERE t.ig
            """)).scalar() or 0

            last_run = conn.execute(
                _text("SELECT MAX(run_at)::text FROM ema_signals")
            ).scalar()

            # Aggregate hit-rate: of golden crosses in the last 90 days that have
            # had _HIT_RATE_FORWARD_TRADING_DAYS trading days to play out, what
            # share closed higher N trading days later? One SQL pass — LEAD()
            # over each symbol's own trade_date-ordered series finds the close
            # N trading-day rows later, exactly like _compute_cross_events's
            # index-based forward return, just aggregated across every stock at
            # once instead of one symbol's stored series. A cross too recent to
            # have resolved yet (LEAD returns NULL) is excluded from the sample,
            # not counted as a loss.
            hit_rate_row = conn.execute(_text("""
                WITH ordered AS (
                    SELECT
                        trade_date, close_price, cross_type,
                        LEAD(close_price, :forward_days) OVER (
                            PARTITION BY symbol ORDER BY trade_date
                        ) AS close_later
                    FROM ema_signals
                )
                SELECT
                    COUNT(*) FILTER (WHERE close_later IS NOT NULL) AS sample_size,
                    COUNT(*) FILTER (WHERE close_later > close_price) AS wins
                FROM ordered
                WHERE cross_type = 'golden'
                  AND trade_date >= CURRENT_DATE - (:lookback_days * INTERVAL '1 day')
            """), {
                "forward_days": _HIT_RATE_FORWARD_TRADING_DAYS,
                "lookback_days": _HIT_RATE_LOOKBACK_DAYS,
            }).mappings().first()

        sample_size = int(hit_rate_row["sample_size"] or 0)
        wins = int(hit_rate_row["wins"] or 0)
        win_rate = round(wins / sample_size * 100, 1) if sample_size else None

        return {
            "signals":          [dict(r) for r in rows],
            "total_monitored":  int(total_monitored),
            "golden_now":       int(golden_now),
            "last_run":         last_run,
            "refreshing":       rate_limiter.is_locked(_SME_REFRESH_LOCK_NAME),
            "golden_hit_rate_90d": {
                "sample_size": sample_size,
                "win_rate":    win_rate,
                "lookback_days": _HIT_RATE_LOOKBACK_DAYS,
                "forward_days":  _HIT_RATE_FORWARD_TRADING_DAYS,
            },
        }

    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _query_sync)
    except Exception as exc:
        log_event(LOGGER, "sme_signals_query_failed", level="error", error=str(exc))
        raise HTTPException(status_code=503, detail="Database error. See server logs.")


_CROSS_OUTCOME_WINDOWS = (10, 20)


def _compute_cross_events(series: list[dict]) -> list[dict]:
    """For every cross-flagged row in an ascending (trade_date ASC) series,
    compute close-price forward returns N trading days later. A bare "golden
    cross on date X" says nothing about what past crosses on this stock
    actually did; this gives it outcome context. Since `series` is already
    the full stored window (sme_ema_pipeline._STORE_DAYS, ~3 months), a
    forward return can only be computed if that many trading days have
    actually elapsed since the cross within this same window — otherwise the
    return field is None (never guessed at, same "never invent" convention as
    everywhere else in this pipeline). Most-recent cross first.
    """
    events = []
    for i, row in enumerate(series):
        if not row.get("cross"):
            continue
        close_at_cross = row.get("close_price")
        event = {
            "trade_date":     row["trade_date"],
            "cross":          row["cross"],
            "close_at_cross": close_at_cross,
        }
        for window in _CROSS_OUTCOME_WINDOWS:
            future_idx = i + window
            ret = None
            if close_at_cross and future_idx < len(series):
                future_close = series[future_idx].get("close_price")
                if future_close is not None:
                    ret = round((future_close - close_at_cross) / close_at_cross * 100, 2)
            event[f"ret_{window}d_pct"] = ret
        events.append(event)
    events.reverse()
    return events


@app.get("/api/sme-signals/{symbol}/history")
async def get_sme_signal_history(symbol: str):
    """Return the stored EMA20/EMA50/close series for one SME stock, for charting
    around a golden/death cross. Up to ~63 trading days (sme_ema_pipeline._STORE_DAYS).
    Also returns cross_events: every cross in that window with its forward
    return (see _compute_cross_events) — e.g. "last 3 golden crosses: +12%,
    -4%, +22% over 20d" instead of a bare "golden cross on date X."
    """
    import os

    sym = symbol.upper().strip()
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured. Run the SME pipeline first.")

    def _query_sync() -> dict:
        from sqlalchemy import text as _text

        engine = _get_db_engine()
        with engine.connect() as conn:
            rows = conn.execute(_text("""
                SELECT
                    e.trade_date::text   AS trade_date,
                    e.close_price::float AS close_price,
                    e.ema20::float       AS ema20,
                    e.ema50::float       AS ema50,
                    e.cross_type         AS "cross"
                FROM ema_signals e
                WHERE e.symbol = :symbol
                ORDER BY e.trade_date ASC
            """), {"symbol": sym}).mappings().fetchall()

            stock = conn.execute(_text("""
                SELECT name, exchange FROM sme_stocks WHERE symbol = :symbol
            """), {"symbol": sym}).mappings().first()

        series = [dict(r) for r in rows]
        return {
            "symbol":       sym,
            "name":         stock["name"] if stock else None,
            "exchange":     stock["exchange"] if stock else None,
            "series":       series,
            "cross_events": _compute_cross_events(series),
        }

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, _query_sync)
    except Exception as exc:
        log_event(LOGGER, "sme_signal_history_failed", level="error", symbol=sym, error=str(exc))
        raise HTTPException(status_code=503, detail="Database error. See server logs.")

    if not result["series"]:
        raise HTTPException(status_code=404, detail=f"No stored EMA history for {sym}.")
    return result


@app.post("/api/sme-signals/refresh", status_code=202)
async def refresh_sme_signals(request: Request):
    """Run the SME EMA pipeline in the background. 409 if a run is in progress."""
    import os

    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured.")
    # Atomic claim (Redis-shared across workers when REDIS_URL is set, so two
    # workers can no longer both pass this check and both start a refresh —
    # unlike the old plain-bool guard this replaced). Checked before the rate
    # limit, same order the old code used (409 takes priority over 429 when
    # both would apply).
    if not rate_limiter.try_acquire_lock(_SME_REFRESH_LOCK_NAME, _SME_REFRESH_LOCK_TTL_SECONDS):
        raise HTTPException(status_code=409, detail="A refresh is already running.")
    try:
        _rate_limit(request, "sme_refresh", max_calls=3, window_seconds=3600)
    except HTTPException:
        rate_limiter.release_lock(_SME_REFRESH_LOCK_NAME)
        raise

    loop = asyncio.get_running_loop()

    def _run_pipeline():
        try:
            from sme_ema_pipeline import run as run_sme_pipeline
            healthy = run_sme_pipeline()
            if not healthy:
                log_event(
                    LOGGER, "sme_refresh_unhealthy", level="warning",
                    detail="Pipeline ran but reported an unhealthy result (empty stock list or "
                           "too high an OHLCV fetch error rate) — see sme_ema_pipeline logs above.",
                )
        except Exception as exc:
            log_event(LOGGER, "sme_refresh_failed", level="error", error=str(exc))
        finally:
            rate_limiter.release_lock(_SME_REFRESH_LOCK_NAME)

    async def _launch():
        await loop.run_in_executor(None, _run_pipeline)

    asyncio.create_task(_launch())
    log_event(LOGGER, "sme_refresh_started")
    return {"started": True}


# ── Custom screener ───────────────────────────────────────────────────────────
# Stored-metrics table over the NIFTY 500 universe (screener_pipeline.py,
# see CLAUDE.md's "Custom screener flow"), generalizing SME Signals' filter-
# chip pattern to the main NSE/BSE market instead of SME/Emerge stocks.

_SCREENER_REFRESH_LOCK_NAME = "screener_refresh"
_SCREENER_REFRESH_LOCK_TTL_SECONDS = 3600  # generous upper bound on one pipeline run

# Whitelisted sort columns — interpolated into the SQL ORDER BY clause below,
# since column names can't be bind parameters; validated against this set
# before interpolation, same "closed enum, not raw user text" safety as
# `direction`/`view` on /api/sme-signals.
_SCREENER_SORT_COLUMNS = {
    "symbol", "current_price", "pe_ratio", "market_cap_cr", "avg_volume_10d", "rsi14",
}


@app.get("/api/screener")
async def get_screener(
    request: Request,
    industry:       str = Query("all", description="NSE industry filter (from the NIFTY 500 constituent list), or 'all'"),
    ema_trend:      str = Query("all", description="all | bullish | bearish"),
    pe_max:         float | None = Query(None, ge=0, description="Max P/E ratio"),
    market_cap_min: float | None = Query(None, ge=0, description="Min market cap, in ₹ Cr"),
    rsi_min:        float | None = Query(None, ge=0, le=100),
    rsi_max:        float | None = Query(None, ge=0, le=100),
    sort:           str = Query("market_cap_cr"),
    order:          str = Query("desc", description="asc | desc"),
    limit:          int = Query(100, ge=1, le=500),
    offset:         int = Query(0, ge=0),
):
    """Filterable/sortable view over the NIFTY 500 screener_stocks table
    (screener_pipeline.py). Every numeric filter is optional and additive
    (AND-ed together); a filter Screener/yfinance didn't have for a stock
    (NULL in the DB) excludes that stock from that filter rather than
    guessing a value for it. `industries` in the response is the real,
    currently-populated set of nse_industry values — the frontend's filter
    chips are built from this, not a hardcoded/guessed list.
    """
    _rate_limit(request, "screener", max_calls=60, window_seconds=60)

    if ema_trend not in ("all", "bullish", "bearish"):
        raise HTTPException(status_code=422, detail="ema_trend must be one of: all, bullish, bearish")
    if sort not in _SCREENER_SORT_COLUMNS:
        raise HTTPException(status_code=422, detail=f"sort must be one of: {', '.join(sorted(_SCREENER_SORT_COLUMNS))}")
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=422, detail="order must be asc or desc")
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured. Run the screener pipeline first.")

    def _query_sync() -> dict:
        from sqlalchemy import text as _text

        engine = _get_db_engine()

        where_clauses = []
        params: dict = {}
        if industry != "all":
            where_clauses.append("nse_industry = :industry")
            params["industry"] = industry
        if ema_trend != "all":
            where_clauses.append("ema_trend = :ema_trend")
            params["ema_trend"] = ema_trend
        if pe_max is not None:
            where_clauses.append("pe_ratio IS NOT NULL AND pe_ratio <= :pe_max")
            params["pe_max"] = pe_max
        if market_cap_min is not None:
            where_clauses.append("market_cap_cr IS NOT NULL AND market_cap_cr >= :market_cap_min")
            params["market_cap_min"] = market_cap_min
        if rsi_min is not None:
            where_clauses.append("rsi14 IS NOT NULL AND rsi14 >= :rsi_min")
            params["rsi_min"] = rsi_min
        if rsi_max is not None:
            where_clauses.append("rsi14 IS NOT NULL AND rsi14 <= :rsi_max")
            params["rsi_max"] = rsi_max

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        order_sql = "ASC" if order == "asc" else "DESC"

        with engine.connect() as conn:
            rows = conn.execute(_text(f"""
                SELECT
                    symbol, company_name, exchange, nse_industry, sector,
                    current_price::float  AS current_price,
                    pe_ratio::float       AS pe_ratio,
                    market_cap_cr::float  AS market_cap_cr,
                    avg_volume_10d::float AS avg_volume_10d,
                    rsi14::float          AS rsi14,
                    ema_trend,
                    fetched_at::text      AS fetched_at
                FROM screener_stocks
                {where_sql}
                ORDER BY {sort} {order_sql} NULLS LAST, symbol ASC
                LIMIT :limit OFFSET :offset
            """), {**params, "limit": limit, "offset": offset}).mappings().fetchall()

            total = conn.execute(
                _text(f"SELECT COUNT(*) FROM screener_stocks {where_sql}"), params
            ).scalar() or 0
            total_monitored = conn.execute(
                _text("SELECT COUNT(*) FROM screener_stocks")
            ).scalar() or 0
            industries = conn.execute(_text("""
                SELECT DISTINCT nse_industry FROM screener_stocks
                WHERE nse_industry IS NOT NULL ORDER BY nse_industry
            """)).scalars().all()
            last_run = conn.execute(
                _text("SELECT MAX(fetched_at)::text FROM screener_stocks")
            ).scalar()

        return {
            "stocks":           [dict(r) for r in rows],
            "total":            int(total),
            "total_monitored":  int(total_monitored),
            "industries":       list(industries),
            "last_run":         last_run,
            "refreshing":       rate_limiter.is_locked(_SCREENER_REFRESH_LOCK_NAME),
        }

    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _query_sync)
    except Exception as exc:
        log_event(LOGGER, "screener_query_failed", level="error", error=str(exc))
        raise HTTPException(status_code=503, detail="Database error. See server logs.")


@app.post("/api/screener/refresh", status_code=202)
async def refresh_screener(request: Request):
    """Run the custom screener pipeline in the background. 409 if a run is
    already in progress — same lock-then-rate-limit pattern (and ordering)
    as /api/sme-signals/refresh."""
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured.")
    if not rate_limiter.try_acquire_lock(_SCREENER_REFRESH_LOCK_NAME, _SCREENER_REFRESH_LOCK_TTL_SECONDS):
        raise HTTPException(status_code=409, detail="A refresh is already running.")
    try:
        _rate_limit(request, "screener_refresh", max_calls=3, window_seconds=3600)
    except HTTPException:
        rate_limiter.release_lock(_SCREENER_REFRESH_LOCK_NAME)
        raise

    loop = asyncio.get_running_loop()

    def _run_pipeline():
        try:
            from screener_pipeline import run as run_screener_pipeline
            healthy = run_screener_pipeline()
            if not healthy:
                log_event(
                    LOGGER, "screener_refresh_unhealthy", level="warning",
                    detail="Pipeline ran but reported an unhealthy result (empty constituent "
                           "list or too high a metrics-fetch error rate) — see screener_pipeline logs above.",
                )
        except Exception as exc:
            log_event(LOGGER, "screener_refresh_failed", level="error", error=str(exc))
        finally:
            rate_limiter.release_lock(_SCREENER_REFRESH_LOCK_NAME)

    async def _launch():
        await loop.run_in_executor(None, _run_pipeline)

    asyncio.create_task(_launch())
    log_event(LOGGER, "screener_refresh_started")
    return {"started": True}


# ── Watchlist ─────────────────────────────────────────────────────────────────
# Every row is owned by exactly one identity: either the anonymous per-browser
# client_id (a UUID the frontend generates on first use and keeps in
# localStorage — see frontend/lib/watchlist.ts) or, once signed in, the
# account's user_id (see auth.py). A valid session always wins over client_id
# when both are present in a request — that's the whole point of an account:
# it doesn't depend on which browser sent the request. Signing in does NOT
# claim/merge an existing client_id's rows onto the account (see
# db/models.py's watchlist_items comment) — a freshly-signed-in user simply
# starts seeing whatever their account already owns.
_CLIENT_ID_RE = re.compile(r"^[a-zA-Z0-9-]{1,36}$")  # matches watchlist_items.client_id VARCHAR(36)
_MAX_WATCHLIST_ITEMS_PER_CLIENT = 200
_VALID_EXCHANGES = {"NSE", "BSE"}

# (owner_type, owner_value) — owner_type is "user" (owner_value: int user id)
# or "client" (owner_value: str client_id). Never constructed directly outside
# _resolve_watchlist_owner, so owner_type is always one of exactly these two
# literals wherever it's used to pick a column name below.
WatchlistOwner = tuple


def _resolve_watchlist_owner(token: str | None, client_id: str | None) -> WatchlistOwner:
    """A valid session always takes priority over client_id. Runs inside the
    same executor thread as the caller's other DB work, since checking the
    session also hits the DB. Raises ValueError (-> 422) if neither a valid
    session nor a well-formed client_id is available — this endpoint doesn't
    otherwise require being signed in, so an expired/invalid token just falls
    through to the client_id path rather than surfacing as a 401."""
    if token:
        import auth as _auth
        user = _auth.get_user_for_session(token)
        if user:
            return ("user", user["id"])
    if not client_id or not _CLIENT_ID_RE.match(client_id):
        raise ValueError("Invalid client_id.")
    return ("client", client_id)


def _owner_column(owner: WatchlistOwner) -> str:
    return "user_id" if owner[0] == "user" else "client_id"


class WatchlistAddRequest(BaseModel):
    client_id: str | None = None
    symbol: str
    company: str = Field(default="")
    exchange: str = Field(default="NSE")


def _watchlist_rows_sync(owner: WatchlistOwner) -> list[dict]:
    from sqlalchemy import text as _text

    column = _owner_column(owner)
    engine = _get_db_engine()
    with engine.connect() as conn:
        rows = conn.execute(_text(f"""
            SELECT symbol, company, exchange, added_at::text AS "addedAt"
            FROM watchlist_items
            WHERE {column} = :owner_value
            ORDER BY added_at DESC
        """), {"owner_value": owner[1]}).mappings().fetchall()
    return [dict(r) for r in rows]


@app.get("/api/watchlist")
async def get_watchlist(request: Request, client_id: str | None = Query(None)):
    _rate_limit(request, "watchlist_read", max_calls=120, window_seconds=60)
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured.")
    token = _bearer_token_from_request(request)

    def _sync() -> list[dict]:
        owner = _resolve_watchlist_owner(token, client_id)
        return _watchlist_rows_sync(owner)

    loop = asyncio.get_running_loop()
    try:
        items = await loop.run_in_executor(None, _sync)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        log_event(LOGGER, "watchlist_read_failed", level="error", error=str(exc))
        raise HTTPException(status_code=503, detail="Database error. See server logs.")
    return {"items": items}


@app.post("/api/watchlist")
async def add_to_watchlist(request: Request, body: WatchlistAddRequest):
    _rate_limit(request, "watchlist_write", max_calls=60, window_seconds=60)
    symbol = body.symbol.upper().strip()
    if not _TICKER_RE.match(symbol):
        raise HTTPException(status_code=422, detail="Invalid symbol.")
    exchange = body.exchange.upper().strip()
    if exchange not in _VALID_EXCHANGES:
        raise HTTPException(status_code=422, detail="Invalid exchange.")
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured.")
    token = _bearer_token_from_request(request)

    def _upsert_sync() -> list[dict]:
        from sqlalchemy import text as _text

        owner = _resolve_watchlist_owner(token, body.client_id)
        column = _owner_column(owner)
        # Distinct lock-key namespace per owner type so a client_id string and
        # a user_id integer can never collide on the same advisory lock.
        lock_key = f"watchlist:{owner[0]}:{owner[1]}"

        engine = _get_db_engine()
        with engine.begin() as conn:
            # Advisory lock scoped to this transaction (released automatically on
            # commit/rollback) serializes concurrent adds for the same owner, so
            # the count-then-insert below can't race past _MAX_WATCHLIST_ITEMS_PER_CLIENT.
            conn.execute(_text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"), {"lock_key": lock_key})
            count = conn.execute(_text(
                f"SELECT COUNT(*) FROM watchlist_items WHERE {column} = :owner_value"
            ), {"owner_value": owner[1]}).scalar() or 0
            if count >= _MAX_WATCHLIST_ITEMS_PER_CLIENT:
                raise ValueError(f"Watchlist is capped at {_MAX_WATCHLIST_ITEMS_PER_CLIENT} stocks.")
            conn.execute(_text(f"""
                INSERT INTO watchlist_items ({column}, symbol, company, exchange)
                VALUES (:owner_value, :symbol, :company, :exchange)
                ON CONFLICT ({column}, symbol) DO NOTHING
            """), {
                "owner_value": owner[1], "symbol": symbol,
                "company": body.company[:200], "exchange": exchange,
            })
        return _watchlist_rows_sync(owner)

    loop = asyncio.get_running_loop()
    try:
        items = await loop.run_in_executor(None, _upsert_sync)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        log_event(LOGGER, "watchlist_write_failed", level="error", error=str(exc))
        raise HTTPException(status_code=503, detail="Database error. See server logs.")
    return {"items": items}


@app.delete("/api/watchlist/{symbol}")
async def remove_from_watchlist(request: Request, symbol: str, client_id: str | None = Query(None)):
    _rate_limit(request, "watchlist_write", max_calls=60, window_seconds=60)
    sym = symbol.upper().strip()
    if not _TICKER_RE.match(sym):
        raise HTTPException(status_code=422, detail="Invalid symbol.")
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured.")
    token = _bearer_token_from_request(request)

    def _delete_sync() -> list[dict]:
        from sqlalchemy import text as _text

        owner = _resolve_watchlist_owner(token, client_id)
        column = _owner_column(owner)

        engine = _get_db_engine()
        with engine.begin() as conn:
            conn.execute(_text(
                f"DELETE FROM watchlist_items WHERE {column} = :owner_value AND symbol = :symbol"
            ), {"owner_value": owner[1], "symbol": sym})
        return _watchlist_rows_sync(owner)

    loop = asyncio.get_running_loop()
    try:
        items = await loop.run_in_executor(None, _delete_sync)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        log_event(LOGGER, "watchlist_write_failed", level="error", error=str(exc))
        raise HTTPException(status_code=503, detail="Database error. See server logs.")
    return {"items": items}


# ── Consolidated view ──────────────────────────────────────────────────────────
# "What does AlphaPulse think about X" spans three independently-run pipelines
# today, so answering it means visiting three pages. This endpoint answers it
# in one request by reading whatever each pipeline has ALREADY computed —
# stock analysis's own cache, the market-picks result cache, and the SME
# pipeline's latest stored regime. It fetches no new data: no LLM calls, no
# scraping, no SME pipeline run. Any section that hasn't been computed yet
# for this symbol (or has gone stale past that pipeline's own TTL) is simply
# null in the response — that's the expected common case, not an error.
async def _consolidated_payload(sym: str) -> dict:
    """Shared aggregation body behind both GET /api/consolidated/{symbol}
    (internal, unauthenticated, IP rate-limited) and GET /api/v1/consolidated/
    {symbol} (external, API-key-gated) — the data returned is identical
    either way, only who's allowed to ask and how often differs."""

    def _analysis_sync() -> dict | None:
        import cache

        data = cache.load(sym, "analysis")
        if not data:
            return None
        return {
            "recommendation": data.get("recommendation"),
            "confidence":     data.get("confidence"),
            "summary":        data.get("summary"),
            "as_of":          data.get("_meta", {}).get("fetched_at"),
        }

    def _market_pick_sync() -> dict | None:
        cached = _load_picks_cache()
        if not cached:
            return None
        for pick in cached.get("picks", []):
            if str(pick.get("symbol", "")).upper() == sym:
                return {
                    "rank":             pick.get("rank"),
                    "recommendation":   pick.get("recommendation"),
                    "confidence_score": pick.get("confidence_score"),
                    "summary":          pick.get("summary"),
                    "generated_at":     cached.get("generated_at"),
                }
        return None

    def _sme_sync() -> dict | None:
        if not os.environ.get("DATABASE_URL"):
            return None
        try:
            from sqlalchemy import text as _text

            engine = _get_db_engine()
            with engine.connect() as conn:
                row = conn.execute(_text("""
                    SELECT
                        e.trade_date::text    AS trade_date,
                        e.cross_type          AS "cross",
                        (e.ema20 > e.ema50)   AS in_golden_cross,
                        s.name,
                        s.exchange
                    FROM ema_signals e
                    JOIN sme_stocks  s USING (symbol)
                    WHERE e.symbol = :symbol
                    ORDER BY e.trade_date DESC
                    LIMIT 1
                """), {"symbol": sym}).mappings().first()
        except Exception as exc:
            # Best-effort: a stock most commonly just isn't an SME stock at all
            # (no row), and a genuine DB hiccup here shouldn't fail the whole
            # aggregated response when the other two sections are fine on their own.
            log_event(LOGGER, "consolidated_sme_query_failed", level="warning", symbol=sym, error=str(exc))
            return None
        return dict(row) if row else None

    loop = asyncio.get_running_loop()
    analysis, market_pick, sme = await asyncio.gather(
        loop.run_in_executor(None, _analysis_sync),
        loop.run_in_executor(None, _market_pick_sync),
        loop.run_in_executor(None, _sme_sync),
    )
    return {"symbol": sym, "analysis": analysis, "market_pick": market_pick, "sme": sme}


@app.get("/api/consolidated/{symbol}")
async def get_consolidated(request: Request, symbol: str):
    _rate_limit(request, "consolidated", max_calls=30, window_seconds=60)
    sym = symbol.upper().strip()
    if not _TICKER_RE.match(sym):
        raise HTTPException(status_code=422, detail="Invalid symbol.")
    return await _consolidated_payload(sym)


# ── Account & magic-link auth ────────────────────────────────────────────────
# Minimal, passwordless auth (see auth.py + email_sender.py). A session token
# is a bearer credential the frontend's Next.js proxy routes hold in an
# httpOnly cookie and send here as `Authorization: Bearer <token>` — this
# file never sees a cookie. Existing watchlist_items/positions stay keyed by
# the anonymous client_id; nothing here reads or migrates that data — an
# account is additive today, not a replacement for it.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Where the magic link should point — the Next.js app, not this API directly,
# since /auth/verify needs to run in the browser to receive the session cookie
# on the frontend's own origin. Distinct from ALLOWED_ORIGINS (a CORS
# allowlist that may hold several origins) — this is the one canonical URL to
# embed in an outbound email.
_FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000").rstrip("/")


class AuthRequestLinkRequest(BaseModel):
    email: str


def _bearer_token_from_request(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        return token or None
    return None


@app.post("/api/auth/request-link")
async def request_magic_link(request: Request, body: AuthRequestLinkRequest):
    _rate_limit(request, "auth_request_link", max_calls=5, window_seconds=900)
    email = body.email.lower().strip()
    if len(email) > 320 or not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Invalid email address.")
    # The IP-keyed limit above doesn't stop an attacker with rotating IPs from
    # email-bombing one victim's inbox — each IP gets its own fresh budget.
    # Also cap by the target address itself, independent of caller IP.
    _check_rate_limit(f"auth_request_link_email:{email}", max_calls=5, window_seconds=3600)
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured.")

    def _sync() -> bool:
        import auth as _auth
        from email_sender import send_magic_link_email

        token = _auth.create_magic_link(email)
        login_url = f"{_FRONTEND_URL}/auth/verify?token={token}"
        return send_magic_link_email(email, login_url)

    loop = asyncio.get_running_loop()
    try:
        delivered = await loop.run_in_executor(None, _sync)
    except Exception as exc:
        log_event(LOGGER, "auth_request_link_failed", level="error", error=str(exc))
        raise HTTPException(status_code=503, detail="Could not send sign-in link. See server logs.")
    if not delivered:
        # Logged server-side in send_magic_link_email; the link itself was still
        # created, so a fixed SMTP config lets the same link start working without
        # the user needing to re-request it. The response deliberately doesn't say
        # "failed" here to avoid leaking SMTP configuration state to the caller.
        log_event(LOGGER, "auth_link_email_not_delivered", level="warning", email=email)
    return {"sent": True}


@app.get("/api/auth/verify")
async def verify_magic_link(request: Request, token: str = Query(...)):
    _rate_limit(request, "auth_verify", max_calls=20, window_seconds=300)
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured.")

    def _sync() -> dict | None:
        import auth as _auth

        user = _auth.verify_magic_link(token)
        if user is None:
            return None
        session_token = _auth.create_session(user["id"])
        return {"user": user, "session_token": session_token}

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, _sync)
    except Exception as exc:
        log_event(LOGGER, "auth_verify_failed", level="error", error=str(exc))
        raise HTTPException(status_code=503, detail="Database error. See server logs.")
    if result is None:
        raise HTTPException(status_code=401, detail="This sign-in link is invalid, expired, or already used.")
    return result


@app.get("/api/auth/me")
async def get_current_user(request: Request):
    token = _bearer_token_from_request(request)
    if not token or not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=401, detail="Not signed in.")

    import auth as _auth

    loop = asyncio.get_running_loop()
    user = await loop.run_in_executor(None, _auth.get_user_for_session, token)
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return {"user": user}


@app.post("/api/auth/logout")
async def logout(request: Request):
    token = _bearer_token_from_request(request)
    if token and os.environ.get("DATABASE_URL"):
        import auth as _auth

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _auth.delete_session, token)
    return {"ok": True}


# ── API key management (programmatic access) ─────────────────────────────────
# Session-authenticated endpoints for a signed-in user to manage their own
# long-lived keys. The keys themselves gate the separate /api/v1/* surface
# below, aimed at external/scripted callers rather than the frontend.
class CreateApiKeyRequest(BaseModel):
    label: str | None = None


async def _require_session_user(request: Request) -> dict:
    token = _bearer_token_from_request(request)
    if not token or not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=401, detail="Not signed in.")

    import auth as _auth

    loop = asyncio.get_running_loop()
    user = await loop.run_in_executor(None, _auth.get_user_for_session, token)
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return user


@app.post("/api/api-keys", status_code=201)
async def create_api_key(request: Request, body: CreateApiKeyRequest):
    _rate_limit(request, "api_keys_create", max_calls=20, window_seconds=3600)
    user = await _require_session_user(request)
    label = (body.label or "").strip()[:120] or None

    import auth as _auth

    loop = asyncio.get_running_loop()
    try:
        key = await loop.run_in_executor(None, _auth.create_api_key, user["id"], label)
    except Exception as exc:
        log_event(LOGGER, "api_key_create_failed", level="error", error=str(exc))
        raise HTTPException(status_code=503, detail="Could not create API key. See server logs.")
    return key


@app.get("/api/api-keys")
async def list_api_keys(request: Request):
    """Also returns `tier` and `usage` (this account's current standing
    against the same sliding-window limit GET /api/v1/* enforces) — a usage
    dashboard alongside key management, not a separate endpoint, since a
    user managing their keys is exactly who wants to see this. `usage.calls`
    is a non-mutating peek (rate_limiter.get_usage_count) — checking it never
    itself counts as a call against the limit it's reporting on."""
    user = await _require_session_user(request)

    import auth as _auth

    loop = asyncio.get_running_loop()
    try:
        keys = await loop.run_in_executor(None, _auth.list_api_keys, user["id"])
    except Exception as exc:
        log_event(LOGGER, "api_key_list_failed", level="error", error=str(exc))
        raise HTTPException(status_code=503, detail="Could not list API keys. See server logs.")

    tier = user.get("tier") or _DEFAULT_TIER
    limit = _TIER_LIMITS.get(tier, _TIER_LIMITS[_DEFAULT_TIER])
    calls = await loop.run_in_executor(
        None, rate_limiter.get_usage_count, f"api_v1:{user['id']}", _API_V1_WINDOW_SECONDS,
    )
    return {
        "keys": keys,
        "tier": tier,
        "usage": {"calls": calls, "limit": limit, "window_seconds": _API_V1_WINDOW_SECONDS},
    }


@app.delete("/api/api-keys/{key_id}")
async def revoke_api_key(request: Request, key_id: int):
    user = await _require_session_user(request)

    import auth as _auth

    loop = asyncio.get_running_loop()
    try:
        revoked = await loop.run_in_executor(None, _auth.revoke_api_key, user["id"], key_id)
    except Exception as exc:
        log_event(LOGGER, "api_key_revoke_failed", level="error", error=str(exc))
        raise HTTPException(status_code=503, detail="Could not revoke API key. See server logs.")
    if not revoked:
        raise HTTPException(status_code=404, detail="No such API key.")
    return {"ok": True}


# ── Public v1 API (external, API-key-gated) ──────────────────────────────────
# A small, deliberately narrow surface for scripted/external consumers —
# distinct from the internal Next.js proxy routes under /api/*, which the
# frontend calls directly and which stay session-cookie/client_id-based.
# Auth here is a raw key in the `X-API-Key` header, never `Authorization:
# Bearer` — that header is reserved for the internal session-token
# convention (see the auth section above), and reusing it here would let a
# forwarded session token accidentally satisfy this check.
def _api_key_from_request(request: Request) -> str | None:
    key = request.headers.get("x-api-key", "").strip()
    return key or None


_API_V1_WINDOW_SECONDS = 3600
# Calls/hour by account tier — 'pro' exists as a column on `users` today with
# no real payment flow behind it (see db/models.py's own note), so every
# account is 'free' in practice until an operator flips it by hand. An
# unrecognized tier value (shouldn't happen given the column's DB default,
# but never trust a stored value blindly) falls back to 'free', the safer
# direction to fail in.
_TIER_LIMITS = {"free": 100, "pro": 1000}
_DEFAULT_TIER = "free"


async def _require_api_key_user(request: Request) -> int:
    """Returns the owning user_id for a valid X-API-Key header, else raises
    401. Also applies a per-user, tier-scaled rate limit distinct from the
    IP-keyed limits on internal endpoints, since a legitimate integration may
    call from a shared/rotating IP."""
    raw_key = _api_key_from_request(request)
    if not raw_key or not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header.")

    import auth as _auth

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _auth.get_user_for_api_key, raw_key)
    if result is None:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header.")
    user_id = result["user_id"]
    limit = _TIER_LIMITS.get(result.get("tier") or _DEFAULT_TIER, _TIER_LIMITS[_DEFAULT_TIER])
    _check_rate_limit(f"api_v1:{user_id}", max_calls=limit, window_seconds=_API_V1_WINDOW_SECONDS)
    return user_id


@app.get("/api/v1/consolidated/{symbol}")
async def get_consolidated_v1(request: Request, symbol: str):
    await _require_api_key_user(request)
    sym = symbol.upper().strip()
    if not _TICKER_RE.match(sym):
        raise HTTPException(status_code=422, detail="Invalid symbol.")
    return await _consolidated_payload(sym)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
