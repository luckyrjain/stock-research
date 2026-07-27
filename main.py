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
from crew import ALL_DATA_TASKS, _configured_providers, parse_json_object, run_analysis_with_fallback
from error_tracking import init_error_tracking
from mf_holdings_history import compute_stake_deltas as compute_mf_holdings_deltas
from mf_holdings_history import save_snapshot as save_mf_holdings_snapshot
from observability import get_logger, log_event
from schema_drift import log_drift_if_any
from schemas import normalize as schema_normalize
from schemas import validate as schema_validate
from signals.engine import run_signal_engine
from signals.filings_classifier import classify_filings
from signals.store import save_signal
from signals.interpreter import interpret
from tools.news_tools import get_latest_news
from tools.nse_tools import get_mf_holdings, get_stock_quote
from tools.screener_tools import get_fundamentals, get_holdings
from tools.nse_filings_tools import get_nse_filings
from verdict_history import save_snapshot as save_verdict_snapshot

load_dotenv()

LOGGER = get_logger("main")


# ── Tool dispatch ─────────────────────────────────────────────────────────────

def _save_raw_tool_output(symbol: str, task_name: str, raw_payload: object) -> None:
    """Persist raw tool output for selected tasks to aid debugging and
    auditability. Best-effort, never raises -- this runs inside
    _fetch_task()'s own try block right after a tool call has already
    succeeded, so a failure here (disk full, a read-only output/ mount, a
    permission error) must not be indistinguishable from the fetch itself
    failing. Without this guard, an exception here burned a retry attempt
    on an already-successful fetch, and could exhaust every attempt and
    discard real, successfully-scraped data as {"error": ...} — the same
    "auxiliary/debug persistence must not break the primary operation"
    convention schema_drift.py::log_drift_if_any() already follows."""
    if task_name not in {"research", "shareholding", "filings"}:
        return

    try:
        symbol_dir = Path("output") / symbol.upper()
        symbol_dir.mkdir(parents=True, exist_ok=True)
        raw_path = symbol_dir / f"{task_name}_raw.json"

        payload = {
            "_meta": {"fetched_at": datetime.now(timezone.utc).isoformat(), "task": task_name},
            "raw_output": raw_payload if isinstance(raw_payload, dict) else str(raw_payload),
        }
        raw_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "save_raw_tool_output_failed", level="warning", symbol=symbol, task=task_name, error=str(exc))

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
                log_drift_if_any(task_name, raw, run_id=run_id, symbol=symbol)
                if task_name == "mf_holdings":
                    save_mf_holdings_snapshot(symbol, raw)
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
                log_drift_if_any(task_name, parsed, run_id=run_id, symbol=symbol)
                if task_name == "mf_holdings":
                    save_mf_holdings_snapshot(symbol, parsed)
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


def _fetched_at(data: dict) -> str | None:
    """Pulls a task's own _meta.fetched_at before _strip_meta discards it —
    used to build `data_freshness` below. None (never guessed) when a task
    has no _meta at all (an error payload, or a task that was never run)."""
    return data.get("_meta", {}).get("fetched_at") if isinstance(data, dict) else None


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
    init_error_tracking()

    parser = argparse.ArgumentParser(description="NSE stock research pipeline")
    parser.add_argument("symbol", help="NSE stock symbol (e.g. RELIANCE, TCS)")
    parser.add_argument("--force", action="store_true", help="Ignore cache and re-fetch all data")
    args = parser.parse_args()

    # Delegates to crew.py's own provider-key registry rather than a
    # hand-duplicated list of env var names -- a hardcoded tuple here had
    # drifted out of sync with _API_KEY_ENV (missing OPENROUTER_API_KEY, a
    # fully-supported 5th provider api.py's own equivalent startup check
    # already accounts for via this same function), so a deployment
    # configured with only an OpenRouter key worked fine through the API
    # server but the CLI refused to even attempt the pipeline.
    has_key = bool(_configured_providers())
    is_ollama = os.getenv("LLM_PROVIDER", "").lower() == "ollama"
    if not has_key and not is_ollama:
        print("Error: No API key or local provider found.")
        print("Set one of: ANTHROPIC_API_KEY, OPENAI_API_KEY, GROQ_API_KEY, GOOGLE_API_KEY, OPENROUTER_API_KEY")
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
        save_verdict_snapshot(symbol, analysis, None, all_data.get("stock_info") or {})
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
            meta = cache.save(symbol, name, data)
            # cache.save() deliberately doesn't mutate `data` itself (see its
            # own docstring) -- stamped here instead so _fetched_at() below
            # finds a real timestamp for a task fetched fresh THIS run, not
            # just on a later run once cache.load() reads _meta back off disk.
            if meta:
                data["_meta"] = meta

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
    mf_holdings_trend = compute_mf_holdings_deltas(symbol)
    report = _build_report(symbol, all_data, analysis, signal_context, mf_holdings_trend)
    save_verdict_snapshot(symbol, analysis, signal_context, all_data.get("stock_info") or {})
    report_dir = Path("output") / symbol
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"report_{date.today()}.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nReport saved to: {report_path}")
    log_event(LOGGER, "pipeline_completed", run_id=run_id, symbol=symbol, report_path=str(report_path))


def _build_report(
    symbol: str,
    all_data: dict,
    analysis: dict,
    signals: dict | None = None,
    mf_holdings_trend: list[dict] | None = None,
) -> dict:
    # Captured before _strip_meta discards each task's own _meta — the main
    # report page previously only showed report.generated_at ("Updated
    # today"), which is stamped fresh on every _build_report() call
    # regardless of whether any underlying data was actually refetched, so
    # e.g. a 6-day-stale shareholding table (168h TTL) still read as
    # "Updated today". data_freshness surfaces each task's own real fetch
    # timestamp so the UI can show the true oldest-data age instead.
    data_freshness = {
        "stock_info":   _fetched_at(all_data.get("stock_info", {})),
        "research":     _fetched_at(all_data.get("research", {})),
        "news":         _fetched_at(all_data.get("news", {})),
        "shareholding": _fetched_at(all_data.get("shareholding", {})),
        "mf_holdings":  _fetched_at(all_data.get("mf_holdings", {})),
        "filings":      _fetched_at(all_data.get("filings", {})),
    }

    stock       = _strip_meta(all_data.get("stock_info", {}))
    research    = _strip_meta(all_data.get("research", {}))
    news_raw    = _strip_meta(all_data.get("news", {}))
    shareholding = _strip_meta(all_data.get("shareholding", {}))
    mf          = _strip_meta(all_data.get("mf_holdings", {}))
    filings_raw = _strip_meta(all_data.get("filings", {}))

    holdings = {**shareholding, "mutual_funds": mf.get("mutual_funds", [])}
    filings_list = filings_raw.get("filings", []) if isinstance(filings_raw, dict) else []

    return {
        "symbol": symbol,
        "generated_at": date.today().isoformat(),
        "data_freshness": data_freshness,
        "analysis": _strip_meta(analysis),
        # Promoted out of `analysis` (where it's `_degraded`, underscore-
        # prefixed and stripped above) into its own sibling field — a
        # previous LLM outage converged straight to a safe-fallback HOLD
        # that was indistinguishable from a real one anywhere in the
        # report, since _strip_meta() dropped the one flag that said
        # otherwise. See crew.py::_safe_analysis_fallback and
        # ResultsDashboard's degraded-analysis banner.
        "degraded": bool(analysis.get("_degraded", False)),
        "signals": signals or {},
        "stock_info": stock,
        "research": research,
        "news": news_raw.get("articles", []) if isinstance(news_raw, dict) else [],
        "holdings": holdings,
        "filings": filings_list,
        "filings_summary": classify_filings(filings_list),
        "mf_holdings_trend": mf_holdings_trend or [],
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
