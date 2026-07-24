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

from observability import get_logger, log_event

LOGGER = get_logger("verdict_history")

_ENGINE = None


def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        from db.models import get_engine
        _ENGINE = get_engine()
    return _ENGINE


def save_snapshot(symbol: str, analysis: dict, signal_context: dict | None, stock_info: dict) -> None:
    """Upsert today's verdict row for `symbol`. No-ops if DATABASE_URL isn't
    set or `analysis` has no recommendation (e.g. an empty/degraded payload)."""
    if not os.environ.get("DATABASE_URL"):
        return
    recommendation = analysis.get("recommendation")
    if not recommendation:
        return

    try:
        from sqlalchemy import text

        engine = _get_engine()
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO verdict_history
                    (symbol, verdict_date, recommendation, confidence, current_price, signal_score)
                VALUES
                    (:symbol, CURRENT_DATE, :recommendation, :confidence, :current_price, :signal_score)
                ON CONFLICT (symbol, verdict_date) DO UPDATE SET
                    recommendation = EXCLUDED.recommendation,
                    confidence     = EXCLUDED.confidence,
                    current_price  = EXCLUDED.current_price,
                    signal_score   = EXCLUDED.signal_score
            """), {
                "symbol":         symbol.upper().strip(),
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
