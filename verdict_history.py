"""Persists a one-row-per-(symbol, day) verdict snapshot to Postgres, powering
the frontend's "verdict timeline" strip on the stock analysis hero. Called
from both main.py (CLI) and api.py's /api/analyse SSE stream right after a
report is built, so the two entry points stay in lockstep the same way they
already share main._build_report().

Best-effort throughout: a missing DATABASE_URL or a DB hiccup here must never
break the analysis pipeline itself — failures are logged and swallowed, the
same convention signals/store.py uses for its own write-only audit trail.
"""
import os
import threading
from datetime import datetime, timezone

from observability import get_logger, log_event

LOGGER = get_logger("verdict_history")

_ENGINE = None
_ENGINE_LOCK = threading.Lock()


def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:  # re-check: another thread may have won the race
                from db.models import get_engine
                _ENGINE = get_engine()
    return _ENGINE


def save_snapshot(symbol: str, analysis: dict, signal_context: dict | None, stock_info: dict) -> None:
    """Upsert today's verdict row for `symbol`. No-ops if DATABASE_URL isn't
    set or `analysis` has no recommendation (e.g. an empty/degraded payload).

    Disclosed limitation: this is a plain `ON CONFLICT (symbol, verdict_date)
    DO UPDATE` — last write wins, with no session/request correlation. Two
    concurrent analyses of the same symbol on the same day (an ordinary
    visitor and watchlist_alerts.py --force racing each other, or two
    browser tabs) can legitimately produce two different LLM outputs;
    whichever commits last silently overwrites the other. VerdictTimeline
    then shows a "history" that may not match what an earlier visitor saw
    in their own session that same day. Not fixed here — a real fix would
    need per-request/session-scoped storage (or optimistic concurrency with
    a client-visible conflict signal), which is more infrastructure than a
    once-daily-digest timeline currently needs; flagged as a known gap
    rather than silently accepted, same "disclosed, not silently accepted"
    instinct as this codebase's other documented residual risks.

    This same race also has a consequence beyond UI consistency: detect_
    recent_changes() (used by both watchlist_alerts.py's own daily digest
    and GET /api/watchlist/calendar) diffs whichever row happens to have
    won this upsert against the row before it. If an ordinary visitor's
    same-day re-analysis loses the race against watchlist_alerts.py's own
    save_snapshot() call for that symbol, the row watchlist_alerts.py reads
    back moments later to detect a change may already be the visitor's
    (different) result rather than its own just-saved one — potentially
    masking or fabricating an apparent recommendation/price change that
    neither run actually produced on its own. This is an existing
    consequence of the race documented above, not a new/separate bug — it
    isn't fixed here for the same infrastructure-cost reason."""
    if not os.environ.get("DATABASE_URL"):
        return
    recommendation = analysis.get("recommendation")
    if not recommendation:
        return

    # Computed here (not SQL's CURRENT_DATE) so "today" is always the app's own
    # UTC clock — the same explicit-UTC convention cache.py's freshness checks
    # use — rather than whatever timezone the connected Postgres server happens
    # to be configured with.
    verdict_date = datetime.now(timezone.utc).date().isoformat()

    try:
        from sqlalchemy import text

        engine = _get_engine()
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO verdict_history
                    (symbol, verdict_date, recommendation, confidence, current_price, signal_score)
                VALUES
                    (:symbol, :verdict_date, :recommendation, :confidence, :current_price, :signal_score)
                ON CONFLICT (symbol, verdict_date) DO UPDATE SET
                    recommendation = EXCLUDED.recommendation,
                    confidence     = EXCLUDED.confidence,
                    current_price  = EXCLUDED.current_price,
                    signal_score   = EXCLUDED.signal_score
            """), {
                "symbol":         symbol.upper().strip(),
                "verdict_date":   verdict_date,
                "recommendation": recommendation,
                "confidence":     analysis.get("confidence"),
                "current_price":  stock_info.get("current_price"),
                "signal_score":   (signal_context or {}).get("final_score"),
            })
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "verdict_snapshot_save_failed", level="warning", symbol=symbol, error=str(exc))


def load_history(symbol: str, limit: int = 60) -> list[dict]:
    """Most recent `limit` verdict rows for `symbol`, oldest first (natural
    left-to-right reading order for a timeline strip). Returns [] rather than
    raising if DATABASE_URL isn't set or the query fails."""
    if not os.environ.get("DATABASE_URL"):
        return []

    try:
        from sqlalchemy import text

        engine = _get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT * FROM (
                    SELECT
                        verdict_date::text AS date,
                        recommendation,
                        confidence,
                        current_price,
                        signal_score
                    FROM verdict_history
                    WHERE symbol = :symbol
                    ORDER BY verdict_date DESC
                    LIMIT :limit
                ) recent
                ORDER BY date ASC
            """), {"symbol": symbol.upper().strip(), "limit": limit}).mappings().fetchall()
        return [
            {
                "date":           r["date"],
                "recommendation": r["recommendation"],
                "confidence":     r["confidence"],
                "current_price":  float(r["current_price"]) if r["current_price"] is not None else None,
                "signal_score":   float(r["signal_score"]) if r["signal_score"] is not None else None,
            }
            for r in rows
        ]
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "verdict_history_load_failed", level="warning", symbol=symbol, error=str(exc))
        return []


def detect_recent_changes(symbol: str, price_move_threshold_pct: float = 10.0) -> dict:
    """One shared read of the two most recent stored snapshots for `symbol`,
    returning both a recommendation-change flag and a price-move flag — the
    same two conditions watchlist_alerts.py's daily digest already emails.
    Single source of truth for "what counts as a notable change" so the
    once-daily email and any same-day in-app surfacing (e.g. a watchlist
    badge) can never disagree on the threshold.

    Returns {"recommendation_change": {...} | None, "price_move": {...} | None}
    — both None when there are fewer than 2 stored snapshots (nothing to
    compare against yet), never guessed.
    """
    history = load_history(symbol, limit=2)
    if len(history) < 2:
        return {"recommendation_change": None, "price_move": None}
    previous, current = history[0], history[1]

    recommendation_change = None
    new_rec = current.get("recommendation")
    if new_rec and new_rec != previous.get("recommendation"):
        recommendation_change = {
            "old_recommendation": previous.get("recommendation"),
            "new_recommendation": new_rec,
            "confidence":         current.get("confidence"),
        }

    price_move = None
    old_price, new_price = previous.get("current_price"), current.get("current_price")
    if old_price is not None and new_price is not None and old_price:
        change_pct = round((new_price - old_price) / old_price * 100, 1)
        if abs(change_pct) >= price_move_threshold_pct:
            price_move = {"old_price": old_price, "new_price": new_price, "change_pct": change_pct}

    return {"recommendation_change": recommendation_change, "price_move": price_move}
