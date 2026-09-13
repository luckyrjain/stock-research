"""Daily pick-snapshot history for the Market Picks pipeline: one record per
UTC day, used to compute each stock's rising/falling/stable confidence trend.
Split out of market_picks_pipeline.py — see that module for the pipeline
itself, which calls _save_history/_load_trend from its scoring phase.
"""

from datetime import datetime, timezone

from core import state_store
from pipelines.market_picks_scoring import _effective_signal

# ── Historical tracking ───────────────────────────────────────────────────────
# One record per UTC day under this namespace, keyed by YYYY-MM-DD — so lexical
# key order is chronological order, and "the last N days" is an ORDER BY ...
# DESC LIMIT N rather than the sorted-glob over output/_history/*.json this
# used to be. Read by two independent consumers: _load_trend() below, and
# api.py's GET /api/market-picks/history.
HISTORY_NAMESPACE = "market_picks_history"


def _save_history(picks: list[dict]) -> None:
    """Store today's pick snapshot under HISTORY_NAMESPACE."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snapshot = [
        {
            "symbol":           p["symbol"],
            "confidence":       p["confidence_score"],
            "effective_signal": round(_effective_signal(p.get("_sources_raw", [])), 3),
            "mention_count":    p["mention_count"],
            "current_price":    p.get("current_price"),
            "recommendation":   p.get("recommendation"),
        }
        for p in picks
    ]
    state_store.save(HISTORY_NAMESPACE, date_str, {"date": date_str, "picks": snapshot})


def _load_trend(symbol: str, today_confidence: float) -> dict:
    """
    Return {"trend": "rising"|"falling"|"stable"|"new", "delta": float|None}
    based on the previous 3 days of stored snapshots.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    past_scores: list[float] = []
    # 4 newest, since today's own snapshot may already be among them and is
    # skipped — leaving the 3 prior days this trend is defined over.
    for date_str, data in state_store.items(HISTORY_NAMESPACE, limit=4, newest_first=True):
        if date_str == today:
            continue
        try:
            for row in data.get("picks", []):
                if row["symbol"] == symbol:
                    past_scores.append(row["confidence"])
                    break
        except Exception:
            pass
    if not past_scores:
        return {"trend": "new", "delta": None}
    avg = sum(past_scores) / len(past_scores)
    delta = round(today_confidence - avg, 1)
    trend = "rising" if delta > 4 else "falling" if delta < -4 else "stable"
    return {"trend": trend, "delta": delta}
