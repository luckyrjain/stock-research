import asyncio
import json
import os
import uuid

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


# ── Helpers ───────────────────────────────────────────────────────────────────

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
async def validate_symbol(symbol: str):
    sym = symbol.upper().strip()
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, _autocomplete_sync, sym)

    exact = next((r for r in results if r.get("symbol", "").upper() == sym), None)
    others = [r for r in results if r.get("symbol", "").upper() != sym and r.get("activeSeries")]

    if exact:
        active = bool(exact.get("activeSeries"))
        company = _company_name_from_result(exact)
        if not company:
            company = await loop.run_in_executor(None, _quote_company_name_sync, sym)
        return {
            "found": True,
            "valid": active,
            "symbol": exact["symbol"],
            "company": company,
            "suspended": not active,
            "suggestions": [{"symbol": r["symbol"], "company": _company_name_from_result(r)} for r in others[:5]],
        }

    return {
        "found": False,
        "valid": False,
        "symbol": sym,
        "company": "",
        "suggestions": [{"symbol": r["symbol"], "company": _company_name_from_result(r)} for r in others[:5]],
    }


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
