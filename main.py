"""Run stock research pipeline and generate analysis/report outputs."""
# pylint: disable=line-too-long

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import uuid

import requests

from dotenv import load_dotenv

import cache
from crew import ALL_DATA_TASKS, parse_json_object, run_analysis_with_fallback
from observability import get_logger, log_event
from schemas import normalize as schema_normalize
from schemas import validate as schema_validate
from signals.engine import run_signal_engine
from signals.store import save_signal
from signals.interpreter import interpret
from tools.news_tools import get_latest_news
from tools.nse_tools import get_mf_holdings, get_stock_quote
from tools.screener_tools import get_fundamentals, get_holdings
from tools.nse_filings_tools import get_nse_filings

load_dotenv()

LOGGER = get_logger("main")


# ── Tool dispatch ─────────────────────────────────────────────────────────────

def _save_raw_tool_output(symbol: str, task_name: str, raw_payload: object) -> None:
    """Persist raw tool output for selected tasks to aid debugging and auditability."""
    if task_name not in {"research", "shareholding", "filings"}:
        return

    symbol_dir = Path("output") / symbol.upper()
    symbol_dir.mkdir(parents=True, exist_ok=True)
    raw_path = symbol_dir / f"{task_name}_raw.json"

    payload = {
        "_meta": {"fetched_at": datetime.now(timezone.utc).isoformat(), "task": task_name},
        "raw_output": raw_payload if isinstance(raw_payload, dict) else str(raw_payload),
    }
    raw_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

def _fetch_task(task_name: str, symbol: str, run_id: str, max_attempts: int = 3) -> dict:
    """Call the appropriate data tool directly (no LLM involved)."""

    dispatch = {
        "stock_info":   lambda: get_stock_quote.run(symbol),
        "research":     lambda: get_fundamentals.run(symbol),
        "news":         lambda: get_latest_news.run(f"{symbol} India stock latest news"),
        "shareholding": lambda: get_holdings.run(symbol),
        "mf_holdings":  lambda: get_mf_holdings.run(symbol),
        "filings": lambda: get_nse_filings(symbol),
    }
    last_error = "unknown error"

    for attempt in range(1, max_attempts + 1):
        started_at = time.perf_counter()
        log_event(
            LOGGER,
            "tool_attempt_started",
            run_id=run_id,
            symbol=symbol,
            task=task_name,
            attempt=attempt,
            max_attempts=max_attempts,
        )
        try:
            raw = dispatch[task_name]()
            _save_raw_tool_output(symbol, task_name, raw)
            if isinstance(raw, dict):
                elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
                log_event(
                    LOGGER,
                    "tool_attempt_succeeded",
                    run_id=run_id,
                    symbol=symbol,
                    task=task_name,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    latency_ms=elapsed_ms,
                    payload_type="dict",
                )
                return raw

            parsed = parse_json_object(str(raw))
            if parsed is not None:
                elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
                log_event(
                    LOGGER,
                    "tool_attempt_succeeded",
                    run_id=run_id,
                    symbol=symbol,
                    task=task_name,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    latency_ms=elapsed_ms,
                    payload_type="json_text",
                )
                return parsed

            last_error = "tool returned an unparseable payload"
        except Exception as exc:  # pylint: disable=broad-exception-caught
            last_error = str(exc)

        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
        log_event(
            LOGGER,
            "tool_attempt_failed",
            level="warning",
            run_id=run_id,
            symbol=symbol,
            task=task_name,
            attempt=attempt,
            max_attempts=max_attempts,
            latency_ms=elapsed_ms,
            error=last_error,
            will_retry=attempt < max_attempts,
        )

    return {"error": last_error, "symbol": symbol, "task": task_name}


def _fetch_all_parallel(task_names: list[str], symbol: str, run_id: str) -> dict[str, dict]:
    """Fetch multiple data tasks in parallel threads."""
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=len(task_names)) as pool:
        futures = {pool.submit(_fetch_task, name, symbol, run_id): name for name in task_names}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
                print(f"  [ok] {name}")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                print(f"  [err] {name}: {exc}")
                results[name] = {"error": str(exc), "symbol": symbol}
    return results


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_meta(data: dict) -> dict:
    """Strip internal-only (underscore-prefixed, e.g. _meta, _degraded) fields."""
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _nse_autocomplete(query: str) -> list[dict]:
    """Return raw NSE autocomplete results for a query."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.nseindia.com",
            "Accept": "application/json",
        }
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=6)
        resp = session.get(
            f"https://www.nseindia.com/api/search/autocomplete?q={query}",
            headers=headers,
            timeout=6,
        )
        return resp.json().get("symbols", [])
    except Exception:  # pylint: disable=broad-exception-caught
        return []


def _validate_stock_data(symbol: str, stock_info: dict) -> None:
    """Validate the stock data for a given symbol."""
    def _abort(reason: str) -> None:
        """Abort with a given reason."""
        results = _nse_autocomplete(symbol)
        msg = f"Error: {reason}"

        # Check if the exact symbol exists but has no active series (suspended/delisted)
        exact = next((r for r in results if r.get("symbol", "").upper() == symbol), None)
        if exact and not exact.get("activeSeries"):
            msg += (
                f"\n\nNote: '{symbol}' ({exact.get('symbol_info', '')}) exists on NSE "
                "but has no active trading series — it is likely suspended or delisted."
            )

        # Show other active symbols as suggestions
        suggestions = [
            r for r in results
            if r.get("symbol", "").upper() != symbol and r.get("activeSeries")
        ]
        if suggestions:
            msg += "\n\nDid you mean:\n"
            msg += "\n".join(
                f"  {r['symbol']:<15} {r.get('symbol_info', '')}"
                for r in suggestions[:5]
            )

        raise SystemExit(msg)

    ok, err = schema_validate("stock_info", stock_info)
    if not ok:
        _abort(f"no valid market data found for '{symbol}' on NSE or BSE — {err}.")


def _print_status(symbol: str) -> None:
    """Print the cache status for a given symbol."""
    statuses = cache.status(symbol)
    print(f"Cache status for {symbol}:")
    for name, label in statuses.items():
        print(f"  {name:<14} {label}")
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():  # pylint: disable=too-many-locals,too-many-statements
    """Run the CLI stock research pipeline for a given NSE symbol."""

    parser = argparse.ArgumentParser(description="NSE stock research pipeline")
    parser.add_argument("symbol", help="NSE stock symbol (e.g. RELIANCE, TCS)")
    parser.add_argument("--force", action="store_true", help="Ignore cache and re-fetch all data")
    args = parser.parse_args()

    has_key = any(
        os.getenv(k)
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "GOOGLE_API_KEY")
    )
    is_ollama = os.getenv("LLM_PROVIDER", "").lower() == "ollama"
    if not has_key and not is_ollama:
        print("Error: No API key or local provider found.")
        print("Set one of: ANTHROPIC_API_KEY, OPENAI_API_KEY, GROQ_API_KEY, GOOGLE_API_KEY")
        print("or set LLM_PROVIDER=ollama in your .env file.")
        raise SystemExit(1)

    symbol = args.symbol.upper().strip()
    run_id = uuid.uuid4().hex[:12]
    log_event(LOGGER, "pipeline_started", run_id=run_id, symbol=symbol, force_refresh=args.force)
    print(f"\nStock research pipeline: {symbol}\n{'='*50}")

    _print_status(symbol)

    if args.force:
        stale_tasks = list(ALL_DATA_TASKS)
        cached_data: dict[str, dict] = {}
    else:
        stale_tasks = [n for n in ALL_DATA_TASKS if not cache.is_fresh(symbol, n)]
        cached_data = {
            n: cache.load(symbol, n)
            for n in ALL_DATA_TASKS
            if n not in stale_tasks
        }

    run_analysis = bool(stale_tasks) or not cache.is_fresh(symbol, "analysis")

    if not stale_tasks and not run_analysis:
        log_event(LOGGER, "pipeline_cache_hit", run_id=run_id, symbol=symbol)
        print("All data is fresh — loading from cache.\n")
        all_data = {n: cache.load(symbol, n) for n in ALL_DATA_TASKS}
        analysis = cache.load(symbol, "analysis") or {}
        _print_report(all_data, analysis)
        return

    # ── Step 1: fetch stale data tasks directly (no LLM) ─────────────────────
    freshly_fetched: dict[str, dict] = {}
    if stale_tasks:
        fresh_count = len(ALL_DATA_TASKS) - len(stale_tasks)
        print(f"Fetching: {', '.join(stale_tasks)}")
        if fresh_count:
            print(f"Using cache for {fresh_count} task(s)")
        print()
        freshly_fetched = _fetch_all_parallel(stale_tasks, symbol, run_id)

        # Normalize to canonical schema before any downstream use
        freshly_fetched = {name: schema_normalize(name, data) for name, data in freshly_fetched.items()}

        # Validate before saving anything or running the analyst
        stock_info = freshly_fetched.get("stock_info") or cached_data.get("stock_info", {})
        _validate_stock_data(symbol, stock_info)

        for name, data in freshly_fetched.items():
            cache.save(symbol, name, data)

    all_data = {**cached_data, **freshly_fetched}

    signal_result = run_signal_engine(symbol, all_data)
    signal_insight = interpret(signal_result)
    save_signal(signal_result)

    signal_context = {
    "final_score": signal_result.final_score,
    "verdict": signal_result.verdict,
    "insight": signal_insight,
    "signals": {
        k: v.__dict__ for k, v in signal_result.signals.items()
        }
    }

    # ── Step 2: run analyst via LLM ───────────────────────────────────────────
    analysis: dict = {}
    if run_analysis:
        print("\nRunning analyst...")
        analysis = run_analysis_with_fallback(symbol, all_data, signal_context=signal_context, run_id=run_id)
        cache.save(symbol, "analysis", analysis)

    _print_report(all_data, analysis)

    # ── Step 3: save merged report ────────────────────────────────────────────
    report = _build_report(symbol, all_data, analysis, signal_context)
    report_dir = Path("output") / symbol
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"report_{date.today()}.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nReport saved to: {report_path}")
    log_event(LOGGER, "pipeline_completed", run_id=run_id, symbol=symbol, report_path=str(report_path))


def _build_report(symbol: str, all_data: dict, analysis: dict, signals: dict | None = None) -> dict:
    stock       = _strip_meta(all_data.get("stock_info", {}))
    research    = _strip_meta(all_data.get("research", {}))
    news_raw    = _strip_meta(all_data.get("news", {}))
    shareholding = _strip_meta(all_data.get("shareholding", {}))
    mf          = _strip_meta(all_data.get("mf_holdings", {}))
    filings_raw = _strip_meta(all_data.get("filings", {}))

    holdings = {**shareholding, "mutual_funds": mf.get("mutual_funds", [])}

    return {
        "symbol": symbol,
        "generated_at": date.today().isoformat(),
        "analysis": _strip_meta(analysis),
        "signals": signals or {},
        "stock_info": stock,
        "research": research,
        "news": news_raw.get("articles", []) if isinstance(news_raw, dict) else [],
        "holdings": holdings,
        "filings": filings_raw.get("filings", []) if isinstance(filings_raw, dict) else [],
    }


def _print_report(all_data: dict, analysis: dict) -> None:  # pylint: disable=too-many-branches
    stock = all_data.get("stock_info", {})
    print(f"\n{'='*50}")

    rec  = analysis.get("recommendation", "")
    conf = analysis.get("confidence", "")
    if rec:
        print(f"\n  RECOMMENDATION : {rec}  [{conf} confidence]")
    if analysis.get("summary"):
        print(f"  {analysis['summary']}")

    if stock.get("company_name"):
        print("\nMarket data:")
        print(f"  Company  : {stock['company_name']}")
    if stock.get("prices_by_exchange"):
        print("  Quotes   :")
        for exchange, quote in stock["prices_by_exchange"].items():
            price = quote.get("current_price")
            if price is None:
                continue
            print(f"    {exchange:<4} Rs{price}  ({quote.get('change_pct', 0):+.2f}%)")
    if stock.get("current_price"):
        print(f"  Price    : Rs{stock['current_price']}  ({stock.get('change_pct', 0):+.2f}%)")
    if stock.get("market_cap_cr"):
        print(f"  Mkt Cap  : Rs{stock['market_cap_cr']:,.0f} Cr")
    if stock.get("pe_ratio"):
        print(f"  P/E      : {stock['pe_ratio']:.1f}")

    if analysis.get("valuation"):
        v = analysis["valuation"]
        print(f"\nValuation: {v.get('verdict', '')} — {v.get('comment', '')}")
    if analysis.get("business_quality"):
        print(f"\nBusiness quality: {analysis['business_quality']}")
    if analysis.get("bull_factors"):
        print("\nBull factors:")
        for f in analysis["bull_factors"]:
            print(f"  + {f}")
    if analysis.get("bear_factors"):
        print("\nBear factors:")
        for f in analysis["bear_factors"]:
            print(f"  - {f}")
    if analysis.get("key_risks"):
        print("\nKey risks:")
        for r in analysis["key_risks"]:
            print(f"  ! {r}")
    if analysis.get("news_highlights"):
        print(f"\nNews: {analysis['news_highlights']}")
    if analysis.get("institutional_trend"):
        print(f"\nInstitutional trend: {analysis['institutional_trend']}")


if __name__ == "__main__":
    main()
