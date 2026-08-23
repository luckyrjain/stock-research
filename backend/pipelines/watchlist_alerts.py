"""Daily batch job: re-analyses every symbol on a signed-in user's watchlist,
detects a recommendation change against the prior stored verdict, and emails
a digest to each affected user.

Mirrors pipelines/sme_ema_pipeline.py's standalone-batch-job shape (PostgreSQL,
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
from datetime import date

from core import cache, state_store
from analytics import verdict_history
from analyst.crew import ALL_DATA_TASKS, run_analysis_with_fallback
from db.models import get_engine
from core.email_sender import send_watchlist_alert_email
from main import _fetch_task
from core.error_tracking import init_error_tracking
from core.observability import get_logger, log_event
from core.schemas import normalize as schema_normalize
from signals.engine import run_signal_engine

LOGGER = get_logger("watchlist_alerts")

# Guards against sending the same day's digest twice -- save_snapshot()
# upserts today's verdict_history row, so a second run on the same day (a
# GitHub Actions workflow_dispatch retry, a manual --force rerun while
# cache.is_fresh() is still true) recomputes an identical "yesterday ->
# today" diff and, without this, would re-email every affected user. Keyed
# by calendar date so only "was this (user, symbol, kind) already alerted
# today" needs checking -- older days are pruned each run since nothing
# ever reads past today's key (see run()'s own delete_older_than call).
_ALERTED_NAMESPACE = "watchlist_alerts_sent"
_ALERTED_RETENTION_DAYS = 3


def _alert_key(user_id: int, symbol: str, kind: str) -> str:
    return f"{user_id}:{symbol}:{kind}"


def _claim_alert_keys(today: str, keys: set[str]) -> set[str]:
    """Atomically claims `keys` as sent-for-today, returning only the subset
    NOT already claimed by another run — the ones this run is now
    responsible for actually sending. Must be called BEFORE sending, not
    after: a read-then-send-then-record ordering (checking
    already-claimed, sending, then recording) leaves a window where two
    genuinely concurrent runs (an operator's manual rerun racing an
    in-flight cron run, the GitHub Actions concurrency guard notwithstanding)
    can both read "not yet sent," both dispatch the same email, and only
    then race to record it — by which point the duplicate has already gone
    out. Claiming first closes that window: only one caller's `mutate()`
    can ever see a given key as unclaimed, so at most one of two racing
    callers is ever told to send it."""
    if not keys:
        return set()
    claimed: set[str] = set()

    def _claim(current: dict) -> dict:
        existing = set((current or {}).get("keys", []))
        claimed.update(keys - existing)
        return {"keys": sorted(existing | keys)}

    state_store.mutate(_ALERTED_NAMESPACE, today, _claim, default={"keys": []})
    return claimed


def _release_alert_keys(today: str, keys: set[str]) -> None:
    """Un-claims `keys` — called when a claimed batch's send actually
    failed (SMTP error, etc.), so a transient failure doesn't permanently
    look like "already sent" and silently swallow a real alert forever."""
    if not keys:
        return

    def _release(current: dict) -> dict:
        existing = set((current or {}).get("keys", []))
        return {"keys": sorted(existing - keys)}

    state_store.mutate(_ALERTED_NAMESPACE, today, _release, default={"keys": []})

# This job runs the full (data-fetch + LLM analyst) pipeline per symbol, so
# an unbounded watchlist fan-in means an unbounded daily LLM bill — same
# cost-control instinct as market_picks_pipeline's _MAX_STOCKS. Symbols
# beyond this count are skipped for that day's run (logged, never silently
# dropped) rather than letting the cap grow without limit.
_MAX_ALERT_SYMBOLS = 50
_MAX_ACCEPTABLE_ERROR_RATE = 0.5

# A scoped-down, email-digest version of "real-time price alerts" — the
# review this shipped from called for push notifications on price/verdict
# thresholds, but a genuine push channel (device tokens, APNs/FCM/web-push
# infra, a subscription UI) is new product infrastructure this repo doesn't
# have anywhere yet. This instead widens the *existing* once-daily digest
# job's trigger set: alongside a recommendation change, also flag when a
# watched stock's live price has moved at least this much since the prior
# stored verdict snapshot. Same email, same cadence, one more reason to
# include a symbol in it.
_PRICE_MOVE_THRESHOLD_PCT = 10.0


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


def _detect_change(symbol: str, changes: dict | None = None) -> dict | None:
    """Compare today's freshly-saved verdict against the one immediately
    before it. None if there's no prior day to compare against yet, or the
    recommendation didn't change. Delegates the actual comparison to
    verdict_history.detect_recent_changes() — the same function
    GET /api/watchlist/calendar now calls for same-day in-app surfacing, so
    the email digest and the watchlist page can never disagree on what
    counts as a change.

    `changes` lets a caller that's already fetched
    detect_recent_changes()'s combined result (see _detect_price_move
    below and run()'s own call site) pass it straight through instead of
    triggering a second, redundant DB round-trip for the same symbol —
    detect_recent_changes() already computes both the recommendation-change
    and price-move verdicts from one shared read of the last two stored
    snapshots. Left optional (default None -> fetch it here) so this
    function's own standalone tests, and any other caller that only cares
    about this one check, don't need to fetch it themselves first."""
    if changes is None:
        changes = verdict_history.detect_recent_changes(symbol, _PRICE_MOVE_THRESHOLD_PCT)
    change = changes["recommendation_change"]
    return {"kind": "recommendation_change", "symbol": symbol, **change} if change else None


def _detect_price_move(symbol: str, changes: dict | None = None) -> dict | None:
    """Compare today's freshly-saved verdict snapshot's price against the
    one immediately before it — None if there's no prior snapshot yet,
    either price is missing, the prior price is zero (can't compute a
    percentage off it), or the move is under _PRICE_MOVE_THRESHOLD_PCT.
    Independent of _detect_change: a stock can move 12% in a day and still
    close as a HOLD, which the recommendation-change check alone would
    never surface. Delegates to verdict_history.detect_recent_changes(),
    same reasoning (and same optional `changes` short-circuit) as
    _detect_change above."""
    if changes is None:
        changes = verdict_history.detect_recent_changes(symbol, _PRICE_MOVE_THRESHOLD_PCT)
    move = changes["price_move"]
    return {"kind": "price_move", "symbol": symbol, **move} if move else None


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
    today = date.today().isoformat()
    alerts_by_user: dict[int, dict] = {}
    analyzed, failed = 0, 0

    for symbol in symbols:
        analysis = _analyze_symbol(symbol, run_id, force=force)
        if analysis is None:
            failed += 1
            continue
        analyzed += 1

        # Fetched once and passed to both checks below -- detect_recent_changes()
        # already does one shared DB read of the last two stored snapshots
        # for both the recommendation-change and price-move verdicts, so
        # calling it separately from each of _detect_change/_detect_price_move
        # (their own default behavior when called standalone) would double
        # the DB round-trips per symbol for no benefit here.
        changes = verdict_history.detect_recent_changes(symbol, _PRICE_MOVE_THRESHOLD_PCT)
        symbol_alerts = [a for a in (_detect_change(symbol, changes), _detect_price_move(symbol, changes)) if a]
        if not symbol_alerts:
            continue
        for watcher in by_symbol[symbol]:
            entry = alerts_by_user.setdefault(
                watcher["user_id"], {"email": watcher["email"], "alerts": []},
            )
            entry["alerts"].extend(symbol_alerts)

    # Dedup happens here, at send time, as an atomic claim-then-send per
    # user -- not as an upfront filter against a plain read of "already
    # sent" (see _claim_alert_keys' own docstring for why that ordering
    # can't prevent two genuinely concurrent runs from both sending).
    deduped, notified_users = 0, 0
    for user_id, user in alerts_by_user.items():
        if not user["alerts"]:
            continue
        alert_keys = [
            (_alert_key(user_id, a["symbol"], a["kind"]), a) for a in user["alerts"]
        ]
        wanted_keys = {k for k, _ in alert_keys}
        claimed = _claim_alert_keys(today, wanted_keys)
        deduped += len(wanted_keys) - len(claimed)
        alerts_to_send = [a for k, a in alert_keys if k in claimed]
        if not alerts_to_send:
            continue  # every alert for this user was already claimed by another run
        notified_users += 1
        sent = send_watchlist_alert_email(user["email"], alerts_to_send)
        log_event(
            LOGGER, "watchlist_alert_email_sent" if sent else "watchlist_alert_email_failed",
            level="info" if sent else "warning",
            alert_count=len(alerts_to_send),
        )
        if not sent:
            # The claim already recorded these as "sent" -- release them so
            # a transient SMTP failure doesn't permanently look like a
            # delivered alert and get silently dropped on every future run.
            _release_alert_keys(today, claimed)
    state_store.delete_older_than(_ALERTED_NAMESPACE, days=_ALERTED_RETENTION_DAYS)

    log_event(
        LOGGER, "watchlist_alerts_completed",
        symbols=len(symbols), analyzed=analyzed, failed=failed,
        users_notified=notified_users, deduped=deduped,
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
