"""Daily batch job: re-analyses every symbol on a signed-in user's watchlist,
detects a recommendation change against the prior stored verdict, and emails
a digest to each affected user.

Mirrors sme_ema_pipeline.py's standalone-batch-job shape (PostgreSQL,
`--force` CLI flag, a run()/main() split, a _MAX_ACCEPTABLE_ERROR_RATE-style
health gate so a bad run fails a cron job loudly) applied to the existing
stock-analysis pipeline (main._fetch_task + signals.engine + crew's analyst
call) instead of the SME OHLCV fetch.

Anonymous (client_id-owned) watchlist rows have no email to notify and are
never considered here — only user_id-owned rows are.
"""
import argparse
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import cache
import verdict_history
from crew import ALL_DATA_TASKS, run_analysis_with_fallback
from db.models import get_engine
from email_sender import send_watchlist_alert_email
from main import _fetch_task
from error_tracking import init_error_tracking
from observability import get_logger, log_event
from schemas import normalize as schema_normalize
from signals.engine import run_signal_engine

LOGGER = get_logger("watchlist_alerts")

# This job runs the full (data-fetch + LLM analyst) pipeline per symbol, so
# an unbounded watchlist fan-in means an unbounded daily LLM bill — same
# cost-control instinct as market_picks_pipeline's _MAX_STOCKS. Symbols
# beyond this count are skipped for that day's run (logged, never silently
# dropped) rather than letting the cap grow without limit.
_MAX_ALERT_SYMBOLS = 50
_MAX_ACCEPTABLE_ERROR_RATE = 0.5


def _get_watched_symbols(engine) -> dict[str, list[dict]]:
    """symbol -> [{"user_id", "email"}, ...] for every signed-in user's
    watchlist row. Anonymous (client_id-owned) rows are excluded — there's
    no email to send them to."""
    from sqlalchemy import text

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT w.symbol, w.user_id, u.email
            FROM watchlist_items w
            JOIN users u ON u.id = w.user_id
            WHERE w.user_id IS NOT NULL
        """)).mappings().fetchall()

    by_symbol: dict[str, list[dict]] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append({"user_id": r["user_id"], "email": r["email"]})
    return by_symbol


def _analyze_symbol(symbol: str, run_id: str, force: bool = False) -> dict | None:
    """Fetch fresh data (respecting existing cache TTLs unless `force` —
    a symbol some other part of the app already refreshed today isn't
    double-fetched), run the signal engine + analyst, save the same caches
    and verdict snapshot main.py's CLI path writes, and return the analysis
    dict. Returns None on any failure — isolated per-symbol so one bad fetch
    can't sink the whole run."""
    try:
        if force:
            stale_tasks = list(ALL_DATA_TASKS)
            cached_data: dict[str, dict] = {}
        else:
            stale_tasks = [n for n in ALL_DATA_TASKS if not cache.is_fresh(symbol, n)]
            cached_data = {n: cache.load(symbol, n) for n in ALL_DATA_TASKS if n not in stale_tasks}

        freshly_fetched: dict[str, dict] = {}
        if stale_tasks:
            with ThreadPoolExecutor(max_workers=len(stale_tasks)) as pool:
                futures = {pool.submit(_fetch_task, n, symbol, run_id): n for n in stale_tasks}
                for future in as_completed(futures):
                    name = futures[future]
                    freshly_fetched[name] = schema_normalize(name, future.result())

            stock_info = freshly_fetched.get("stock_info") or cached_data.get("stock_info", {})
            if not stock_info or stock_info.get("error"):
                log_event(LOGGER, "watchlist_alert_symbol_skipped", level="warning",
                          symbol=symbol, reason="no valid stock_info")
                return None

            for name, data in freshly_fetched.items():
                cache.save(symbol, name, data)

        all_data = {**cached_data, **freshly_fetched}
        stock_info = all_data.get("stock_info") or {}

        run_analysis = force or bool(stale_tasks) or not cache.is_fresh(symbol, "analysis")
        if not run_analysis:
            analysis = cache.load(symbol, "analysis") or {}
            verdict_history.save_snapshot(symbol, analysis, None, stock_info)
            return analysis

        signal_result = run_signal_engine(symbol, all_data)
        signal_context = {
            "final_score": signal_result.final_score,
            "verdict": signal_result.verdict,
            "signals": {k: v.__dict__ for k, v in signal_result.signals.items()},
        }
        analysis = run_analysis_with_fallback(symbol, all_data, signal_context=signal_context, run_id=run_id)
        cache.save(symbol, "analysis", analysis)
        verdict_history.save_snapshot(symbol, analysis, signal_context, stock_info)
        return analysis
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "watchlist_alert_symbol_failed", level="warning", symbol=symbol, error=str(exc))
        return None


def _detect_change(symbol: str) -> dict | None:
    """Compare today's freshly-saved verdict against the one immediately
    before it. None if there's no prior day to compare against yet, or the
    recommendation didn't change."""
    history = verdict_history.load_history(symbol, limit=2)
    if len(history) < 2:
        return None
    previous, current = history[0], history[1]
    new_rec = current.get("recommendation")
    if not new_rec or new_rec == previous.get("recommendation"):
        return None
    return {
        "symbol": symbol,
        "old_recommendation": previous.get("recommendation"),
        "new_recommendation": new_rec,
        "confidence": current.get("confidence"),
    }


def run(force: bool = False) -> bool:
    """Returns True on a healthy run (zero changes/emails included — that's
    not a failure), False if the run couldn't meaningfully complete (no DB,
    no LLM key, or too high a per-symbol analysis failure rate to trust)."""
    if not os.environ.get("DATABASE_URL"):
        log_event(LOGGER, "watchlist_alerts_no_database_url", level="error")
        return False

    has_key = any(
        os.getenv(k) for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "GOOGLE_API_KEY")
    )
    if not has_key and os.getenv("LLM_PROVIDER", "").lower() != "ollama":
        log_event(LOGGER, "watchlist_alerts_no_llm_key", level="error")
        return False

    engine = get_engine()
    by_symbol = _get_watched_symbols(engine)
    if not by_symbol:
        log_event(LOGGER, "watchlist_alerts_no_watched_symbols")
        return True

    symbols = sorted(by_symbol)
    if len(symbols) > _MAX_ALERT_SYMBOLS:
        log_event(
            LOGGER, "watchlist_alerts_symbol_cap_exceeded", level="warning",
            total=len(symbols), cap=_MAX_ALERT_SYMBOLS, skipped=symbols[_MAX_ALERT_SYMBOLS:],
        )
        symbols = symbols[:_MAX_ALERT_SYMBOLS]

    run_id = uuid.uuid4().hex[:12]
    alerts_by_user: dict[int, dict] = {}
    analyzed, failed = 0, 0

    for symbol in symbols:
        analysis = _analyze_symbol(symbol, run_id, force=force)
        if analysis is None:
            failed += 1
            continue
        analyzed += 1

        change = _detect_change(symbol)
        if not change:
            continue
        for watcher in by_symbol[symbol]:
            entry = alerts_by_user.setdefault(watcher["user_id"], {"email": watcher["email"], "alerts": []})
            entry["alerts"].append(change)

    for user in alerts_by_user.values():
        sent = send_watchlist_alert_email(user["email"], user["alerts"])
        log_event(
            LOGGER, "watchlist_alert_email_sent" if sent else "watchlist_alert_email_failed",
            level="info" if sent else "warning",
            alert_count=len(user["alerts"]),
        )

    log_event(
        LOGGER, "watchlist_alerts_completed",
        symbols=len(symbols), analyzed=analyzed, failed=failed, users_notified=len(alerts_by_user),
    )

    if symbols and (failed / len(symbols)) > _MAX_ACCEPTABLE_ERROR_RATE:
        log_event(
            LOGGER, "watchlist_alerts_error_rate_exceeded", level="error",
            error_rate=round(failed / len(symbols), 3), threshold=_MAX_ACCEPTABLE_ERROR_RATE,
        )
        return False
    return True


def main() -> None:
    init_error_tracking()
    parser = argparse.ArgumentParser(description="Watchlist recommendation-change email alerts")
    parser.add_argument("--force", action="store_true", help="Bypass all data/analysis caches")
    args = parser.parse_args()

    ok = run(force=args.force)
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
