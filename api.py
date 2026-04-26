import asyncio
import json
import os
import time
import uuid
import re

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from observability import get_logger, log_event

load_dotenv()

app = FastAPI(title="StockResearch AI")
LOGGER = get_logger("api")

_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.nseindia.com",
    "Accept": "application/json",
}

# ── ISIN cache (NSE equity master list, refreshed hourly) ─────────────────────
_ISIN_CACHE: tuple[float, dict[str, dict]] | None = None
_ISIN_CACHE_TTL = 3600


def _is_isin(s: str) -> bool:
    return bool(re.match(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$", s))


def _load_isin_map() -> dict[str, dict]:
    """Download and parse NSE's equity master CSV into an ISIN → {symbol, company} map."""
    global _ISIN_CACHE
    now = time.monotonic()
    if _ISIN_CACHE and now - _ISIN_CACHE[0] < _ISIN_CACHE_TTL:
        return _ISIN_CACHE[1]
    import requests
    try:
        r = requests.get(
            "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        mapping: dict[str, dict] = {}
        for line in r.text.splitlines()[1:]:
            parts = line.split(",")
            if len(parts) >= 7:
                symbol = parts[0].strip()
                company = parts[1].strip()
                isin = parts[6].strip()
                if isin and symbol:
                    mapping[isin] = {"symbol": symbol, "company": company}
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
            # Derive symbol from Screener URL slug (e.g. /company/505685/ → 505685)
            slug = (item.get("url") or "").strip("/").split("/")[-1]
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


def _quote_company_name_sync(symbol: str) -> str:
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
        return (info.get("companyName") or "").strip()
    except Exception:
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
        slug = best.get("url", "").strip("/").split("/")[-1]
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

            # ── Run analyst (LLM, slow) ───────────────────────────────────
            run_analysis = bool(stale) or not cache.is_fresh(sym, "analysis")
            analysis: dict = {}

            if run_analysis:
                yield _sse({"event": "analysing"})

                def _run_analyst():
                    from crew import run_analysis_with_fallback
                    return run_analysis_with_fallback(sym, all_data, run_id=run_id)

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

            report = _build_report(sym, all_data, analysis)
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
