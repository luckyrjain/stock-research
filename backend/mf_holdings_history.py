"""Persists a one-row-per-(symbol, as_of_date, fund) mutual-fund stake
snapshot to Postgres, powering the "stake change vs. prior quarter" trend
surfaced on the stock analysis report. Called from main._fetch_task() — the
one choke point both main.py (CLI) and api.py's /api/analyse SSE stream
already funnel every data-slice fetch through — right after a successful
mf_holdings fetch, so it only ever runs on a genuine fresh fetch, never a
cache hit (a cache hit never reaches _fetch_task at all).

Best-effort throughout: a missing DATABASE_URL or a DB hiccup here must
never break the analysis pipeline itself — failures are logged and
swallowed, the same convention verdict_history.py and state_store.py use
for their own persistence.
"""
import os
import threading

from observability import get_logger, log_event

LOGGER = get_logger("mf_holdings_history")

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


def save_snapshot(symbol: str, mf_holdings: dict) -> None:
    """Upsert one row per fund in `mf_holdings["mutual_funds"]` for this
    symbol/as_of_date. No-ops (never invented) if DATABASE_URL isn't set,
    `mf_holdings` is an error payload, `as_of_date` is missing, or there
    are no funds to record — a symbol with no mutual fund holders is a
    legitimate scrape result, not a reason to write an empty snapshot.

    Same last-write-wins shape as verdict_history.save_snapshot's own
    disclosed limitation (no session correlation on a concurrent write),
    but materially lower-risk here: `as_of_date` is Screener/NSE's own
    quarterly disclosure date, not derived per-request, so two concurrent
    fetches racing for the same symbol/quarter are writing the same
    underlying scraped figures, not two independently-generated answers
    that could disagree — a race here means a redundant write, not a
    silently-discarded different verdict."""
    if not os.environ.get("DATABASE_URL"):
        return
    if not isinstance(mf_holdings, dict) or mf_holdings.get("error"):
        return

    as_of_date = mf_holdings.get("as_of_date")
    funds = mf_holdings.get("mutual_funds") or []
    if not as_of_date or not funds:
        return

    sym = symbol.upper().strip()

    try:
        from sqlalchemy import text

        engine = _get_engine()
        with engine.begin() as conn:
            for f in funds:
                fund_name = f.get("fund")
                holding_pct = f.get("holding_pct")
                if not fund_name or holding_pct is None:
                    continue
                conn.execute(text("""
                    INSERT INTO mf_holdings_history
                        (symbol, as_of_date, fund, holding_pct)
                    VALUES
                        (:symbol, :as_of_date, :fund, :holding_pct)
                    ON CONFLICT (symbol, as_of_date, fund) DO UPDATE SET
                        holding_pct = EXCLUDED.holding_pct
                """), {
                    "symbol":      sym,
                    "as_of_date":  as_of_date,
                    "fund":        fund_name,
                    "holding_pct": holding_pct,
                })
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "mf_holdings_snapshot_save_failed", level="warning", symbol=symbol, error=str(exc))


def compute_stake_deltas(symbol: str, top_n: int = 10) -> list[dict]:
    """Every fund in the most recent stored as_of_date snapshot, ranked by
    holding_pct descending, each with `delta_pct` — the change vs. that same
    fund's holding_pct in the second-most-recent stored snapshot. `delta_pct`
    is None (never guessed) when there's no prior snapshot at all, or this
    fund wasn't present in it (a new entrant has nothing to compare against).
    Returns [] if DATABASE_URL isn't set, the query fails, or no snapshot
    has ever been stored for this symbol."""
    if not os.environ.get("DATABASE_URL"):
        return []

    try:
        from sqlalchemy import text

        engine = _get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text("""
                WITH dates AS (
                    SELECT DISTINCT as_of_date
                    FROM mf_holdings_history
                    WHERE symbol = :symbol
                    ORDER BY as_of_date DESC
                    LIMIT 2
                ),
                latest AS (SELECT as_of_date FROM dates ORDER BY as_of_date DESC LIMIT 1),
                prior  AS (SELECT as_of_date FROM dates ORDER BY as_of_date DESC OFFSET 1 LIMIT 1)
                SELECT
                    cur.fund,
                    cur.holding_pct        AS holding_pct,
                    prev.holding_pct       AS prior_holding_pct,
                    cur.as_of_date::text   AS as_of_date,
                    prior.as_of_date::text AS prior_as_of_date
                FROM mf_holdings_history cur
                CROSS JOIN latest
                LEFT JOIN prior ON true
                LEFT JOIN mf_holdings_history prev
                    ON prev.symbol = cur.symbol
                   AND prev.fund = cur.fund
                   AND prev.as_of_date = prior.as_of_date
                WHERE cur.symbol = :symbol AND cur.as_of_date = latest.as_of_date
                ORDER BY cur.holding_pct DESC
                LIMIT :top_n
            """), {"symbol": symbol.upper().strip(), "top_n": top_n}).mappings().fetchall()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log_event(LOGGER, "mf_holdings_deltas_load_failed", level="warning", symbol=symbol, error=str(exc))
        return []

    deltas = []
    for r in rows:
        holding_pct = float(r["holding_pct"])
        prior_pct = float(r["prior_holding_pct"]) if r["prior_holding_pct"] is not None else None
        deltas.append({
            "fund":              r["fund"],
            "holding_pct":       holding_pct,
            "delta_pct":         round(holding_pct - prior_pct, 3) if prior_pct is not None else None,
            "as_of_date":        r["as_of_date"],
            "prior_as_of_date":  r["prior_as_of_date"],
        })
    return deltas
