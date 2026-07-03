import asyncio
import json
import time
import uuid
import re
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse
from observability import get_logger, log_event
from signals.interpreter import interpret

load_dotenv()

# ── Market picks cache ────────────────────────────────────────────────────────
_PICKS_CACHE_PATH = Path("output/_market_picks/picks.json")
_PICKS_CACHE_TTL_HOURS = 6


def _load_picks_cache() -> dict | None:
    if not _PICKS_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(_PICKS_CACHE_PATH.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(data["_meta"]["fetched_at"])
        age_h = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
        return data if age_h <= _PICKS_CACHE_TTL_HOURS else None
    except Exception:
        return None


def _save_picks_cache(picks: list, generated_at: str) -> None:
    try:
        _PICKS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PICKS_CACHE_PATH.write_text(
            json.dumps({
                "picks":        picks,
                "generated_at": generated_at,
                "_meta":        {"fetched_at": datetime.now(timezone.utc).isoformat()},
            }, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass

app = FastAPI(title="StockResearch AI")
LOGGER = get_logger("api")

_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.nseindia.com",
    "Accept": "application/json",
}

# ── ISIN resolution (NSE equity master, cached 1 h) ──────────────────────────

_ISIN_CACHE: tuple[float, dict] | None = None


def _is_isin(s: str) -> bool:
    return bool(re.match(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$", s))


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
        "service": "StockResearch AI API",
        "status": "ok",
        "message": "Use /health for a simple health check, /api/validate/{symbol} to validate a symbol, and /api/analyse/{symbol} to stream analysis events.",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/api/validate/{symbol}")
async def validate_symbol(symbol: str, exchange: str = ""):
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
async def analyse(symbol: str, force: bool = False):
    sym = symbol.upper().strip()
    run_id = uuid.uuid4().hex[:12]

    async def stream():
        loop = asyncio.get_running_loop()
        try:
            import cache
            from crew import ALL_DATA_TASKS
            from main import _fetch_task, _build_report
            from schemas import normalize as schema_normalize, validate as schema_validate
            from signals.engine import run_signal_engine
            from signals.store import save_signal

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

            signal_result = run_signal_engine(sym, all_data)
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
                        result = {
                            "symbol": sym,
                            "recommendation": "HOLD",
                            "confidence": "LOW",
                            "summary": (
                                f"Automated analysis for {sym} failed while running in the background. "
                                "A safe fallback response was returned instead of terminating the stream."
                            ),
                            "valuation": {
                                "verdict": "Fairly Valued",
                                "comment": f"Analyst execution failed before structured valuation output was produced: {exc}",
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

                while True:
                    try:
                        analysis = await asyncio.wait_for(done_q.get(), timeout=15)
                        break
                    except asyncio.TimeoutError:
                        yield _heartbeat()

                cache.save(sym, "analysis", analysis)
            else:
                analysis = cache.load(sym, "analysis") or {}

            report = _build_report(sym, all_data, analysis, signal_context)
            log_event(LOGGER, "api_analysis_completed", run_id=run_id, symbol=sym)
            yield _sse({"event": "done", "report": report})

        except Exception as exc:
            log_event(LOGGER, "api_analysis_failed", level="error", run_id=run_id, symbol=sym, error=str(exc))
            yield _sse({"event": "error", "message": str(exc)})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/market-picks")
async def market_picks(force: bool = Query(default=False)):
    """Stream market-picks pipeline events as SSE.

    ?force=true  — skip cache and run a fresh pipeline.
    """
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
                _save_picks_cache(picks, generated_at)
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


@app.get("/api/prices")
async def get_prices(symbols: str = Query(...)):
    """Return LTP + day change% for a comma-separated list of NSE/BSE symbols."""
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()][:50]
    loop = asyncio.get_running_loop()

    def _fetch_one(sym: str) -> tuple[str, dict]:
        try:
            import yfinance as yf
            for suffix in (".NS", ".BO"):
                fi = yf.Ticker(sym + suffix).fast_info
                price = getattr(fi, "last_price", None)
                prev  = getattr(fi, "previous_close", None)
                if price and price > 0:
                    chg = round((price - prev) / prev * 100, 2) if prev else 0.0
                    return sym, {"price": round(price, 2), "change_pct": chg}
        except Exception:
            pass
        return sym, {}

    results = await asyncio.gather(
        *[loop.run_in_executor(None, _fetch_one, s) for s in sym_list]
    )
    return {"prices": dict(results)}


@app.get("/api/sme-signals")
async def get_sme_signals(
    lookback:  int = Query(5, ge=1, le=30, description="Trading days back to check for crossovers"),
    direction: str = Query("all",  description="all | bullish | bearish"),
    ema:       str = Query("all",  description="all | ema20 | ema50"),
):
    """Return SME stocks that crossed EMA 20 or EMA 50 in the last N days."""
    import os

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured. Run the SME pipeline first.")

    def _query_sync() -> dict:
        from sqlalchemy import create_engine, text as _text

        engine = create_engine(database_url)

        # Build WHERE clauses for EMA filter
        if ema == "ema20":
            ema_filter = "e.crossed_ema20 = TRUE"
        elif ema == "ema50":
            ema_filter = "e.crossed_ema50 = TRUE"
        else:
            ema_filter = "(e.crossed_ema20 OR e.crossed_ema50)"

        dir_filter = ""
        if direction in ("bullish", "bearish"):
            dir_filter = f"AND e.cross_direction = '{direction}'"

        with engine.connect() as conn:
            rows = conn.execute(_text(f"""
                SELECT
                    s.symbol,
                    s.name,
                    s.exchange,
                    e.trade_date::text   AS trade_date,
                    e.close_price::float AS close_price,
                    e.ema20::float       AS ema20,
                    e.ema50::float       AS ema50,
                    e.crossed_ema20,
                    e.crossed_ema50,
                    e.cross_direction,
                    CASE
                        WHEN e.crossed_ema20 AND e.crossed_ema50 THEN 'EMA20+EMA50'
                        WHEN e.crossed_ema20                     THEN 'EMA20'
                        ELSE                                          'EMA50'
                    END AS crossed
                FROM ema_signals e
                JOIN sme_stocks  s USING (symbol)
                WHERE {ema_filter}
                  {dir_filter}
                  AND e.trade_date >= CURRENT_DATE - (:lookback * INTERVAL '1 day')
                ORDER BY e.trade_date DESC, s.symbol
            """), {"lookback": lookback}).mappings().fetchall()

            total_monitored = conn.execute(
                _text("SELECT COUNT(*) FROM sme_stocks")
            ).scalar() or 0

            last_run = conn.execute(
                _text("SELECT MAX(run_at)::text FROM ema_signals")
            ).scalar()

        return {
            "signals":         [dict(r) for r in rows],
            "total_monitored": int(total_monitored),
            "last_run":        last_run,
        }

    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _query_sync)
    except Exception as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
