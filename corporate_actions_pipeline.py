"""
Corporate Actions Pipeline
==========================
Ingests NSE corporate actions and maintains prices_daily.adj_close
(split/bonus-adjusted; dividends recorded as data only).

Usage:
    python corporate_actions_pipeline.py                       # trailing 30-day window
    python corporate_actions_pipeline.py --backfill 2024-07-01 # chunked history
    python corporate_actions_pipeline.py --recompute TCS       # one symbol repair
    python corporate_actions_pipeline.py --recompute-all       # full repair

Also called from eod_prices_pipeline.run() as an isolated step (own
try/except, never affects the pipeline's exit code) — after bhavcopy
ingestion but before the actual final step, portfolio_valuation.py's
refresh_valuations(), so freshly-adjusted closes are what gets valued.

Parser changes require re-running --backfill and --recompute-all: recompute
triggers only off newly ingested rows.
"""

import argparse
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from sqlalchemy import text

from db.models import corporate_actions, get_engine
from error_tracking import init_error_tracking
from observability import get_logger, log_event
from tools.corporate_actions import fetch_corporate_actions, parse_corporate_actions
from tools.eod_sources import make_nse_session

load_dotenv()
LOGGER = get_logger("corporate_actions")

_WINDOW_DAYS = 30     # trailing window for the daily run
_CHUNK_DAYS = 90      # NSE limits date ranges on the corporate actions API
_BATCH_SIZE = 500
_UNPARSED_SAMPLE_CAP = 3
_SUSPECT_KEYWORDS = ("BONUS", "SPLIT", "SUB-DIVISION", "SUBDIVISION")


# ── Upserts ───────────────────────────────────────────────────────────────────

def _upsert_actions(engine, rows: list[dict]) -> set[str]:
    """Upsert action rows; return symbols with a price-affecting factor."""
    if not rows:
        return set()
    sql = text("""
        INSERT INTO corporate_actions
            (symbol, ex_date, type, purpose_raw, price_factor, amount, record_date)
        VALUES
            (:symbol, :ex_date, :type, :purpose_raw, :price_factor, :amount, :record_date)
        ON CONFLICT (symbol, ex_date, purpose_raw) DO UPDATE SET
            type = EXCLUDED.type, price_factor = EXCLUDED.price_factor,
            amount = EXCLUDED.amount, record_date = EXCLUDED.record_date
    """)
    with engine.begin() as conn:
        for i in range(0, len(rows), _BATCH_SIZE):
            conn.execute(sql, rows[i:i + _BATCH_SIZE])
    return {r["symbol"] for r in rows if r.get("price_factor") is not None}


# ── Adjustment ────────────────────────────────────────────────────────────────

def adjusting_actions(engine, symbol: str) -> list[tuple[date, float]]:
    """Deduped (ex_date, factor) list for one symbol, ascending by ex_date.

    Grouped by (ex_date, type): NSE purpose-text revisions create near-duplicate
    rows with the same factor that must apply once. If a group has more than one
    distinct factor (a ratio REVISION, e.g. Bonus 1:2 corrected to 1:1), only the
    highest-id (most recently ingested) row's factor is applied — a symbol
    cannot legitimately have two splits or two bonuses on one ex-date.
    """
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, ex_date, type, price_factor FROM corporate_actions "
            "WHERE symbol = :s AND price_factor IS NOT NULL"
        ), {"s": symbol}).fetchall()
    groups: dict[tuple[date, str], list[tuple[int, float]]] = {}
    for row_id, ex_date, typ, factor in rows:
        d = ex_date if isinstance(ex_date, date) else datetime.strptime(str(ex_date), "%Y-%m-%d").date()
        groups.setdefault((d, typ), []).append((row_id, float(factor)))
    out: list[tuple[date, float]] = []
    for (d, typ), entries in groups.items():
        distinct_factors = {f for _, f in entries}
        if len(distinct_factors) == 1:
            out.append((d, entries[0][1]))
            continue
        chosen_factor = max(entries, key=lambda e: e[0])[1]
        log_event(LOGGER, "ca_factor_conflict", level="warning", symbol=symbol,
                  ex_date=str(d), type=typ, factors=sorted(distinct_factors),
                  chosen=chosen_factor)
        out.append((d, chosen_factor))
    return sorted(out)


def recompute_symbol(engine, symbol: str) -> int:
    """Rewrite adj_close for one symbol:
    adj_close = close * product(factor of actions with ex_date > trade_date)."""
    actions = adjusting_actions(engine, symbol)
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT trade_date, close FROM prices_daily WHERE symbol = :s"
        ), {"s": symbol}).fetchall()
    updates: list[dict] = []
    for trade_date, close in rows:
        d = trade_date if isinstance(trade_date, date) else datetime.strptime(str(trade_date), "%Y-%m-%d").date()
        factor = 1.0
        for ex_date, f in actions:
            if ex_date > d:
                factor *= f
        updates.append({"s": symbol, "d": d, "adj": round(float(close) * factor, 4)})  # SQLite ignores Numeric scale; keep tests deterministic
    if updates:
        sql = text("UPDATE prices_daily SET adj_close = :adj WHERE symbol = :s AND trade_date = :d")
        with engine.begin() as conn:
            for i in range(0, len(updates), _BATCH_SIZE):
                conn.execute(sql, updates[i:i + _BATCH_SIZE])
    return len(updates)


def recompute_all(engine) -> None:
    """Repair path: reset every adj_close to close, then re-apply factors."""
    with engine.begin() as conn:
        conn.execute(text("UPDATE prices_daily SET adj_close = close"))
    with engine.connect() as conn:
        symbols = [r[0] for r in conn.execute(text(
            "SELECT DISTINCT symbol FROM corporate_actions WHERE price_factor IS NOT NULL"
        )).fetchall()]
    for sym in sorted(symbols):
        n = recompute_symbol(engine, sym)
        log_event(LOGGER, "ca_recomputed", symbol=sym, rows=n)
    log_event(LOGGER, "ca_recompute_all_done", symbols=len(symbols))


# ── Ingestion ─────────────────────────────────────────────────────────────────

def _missed_factor_suspects(rows: list[dict]) -> list[str]:
    """purpose_raw of type=='other' rows that look like a missed ratio parse.

    Distinguishes the dangerous case (BONUS/SPLIT purpose whose ratio failed to
    parse) from the bulk of type=='other' noise (AGM/EGM and similar).
    """
    return [
        r["purpose_raw"] for r in rows
        if r["type"] == "other"
        and any(kw in r["purpose_raw"].upper() for kw in _SUSPECT_KEYWORDS)
    ]


def _ingest_window(engine, from_d: date, to_d: date, session) -> set[str]:
    result = fetch_corporate_actions(from_d, to_d, session)
    if result["status"] != "ok":
        log_event(LOGGER, "ca_fetch_failed", level="warning",
                  start=str(from_d), end=str(to_d), error=result["error"])
        return set()
    rows = parse_corporate_actions(result["raw"])
    suspects = _missed_factor_suspects(rows)
    if suspects:
        log_event(LOGGER, "ca_missed_factor_suspect", level="warning",
                  count=len(suspects), samples=suspects[:_UNPARSED_SAMPLE_CAP])
    other = len([r for r in rows if r["type"] == "other"])
    affected = _upsert_actions(engine, rows)
    log_event(LOGGER, "ca_ingested", start=str(from_d), end=str(to_d),
              actions=len(rows), affected_symbols=len(affected), other=other)
    return affected


def run_ca_step(engine, session=None) -> None:
    """Daily step: trailing window ingest + recompute affected symbols."""
    session = session or make_nse_session()
    today = date.today()
    affected = _ingest_window(engine, today - timedelta(days=_WINDOW_DAYS), today, session)
    for sym in sorted(affected):
        n = recompute_symbol(engine, sym)
        log_event(LOGGER, "ca_recomputed", symbol=sym, rows=n)


def backfill(engine, start: date) -> None:
    session = make_nse_session()
    today = date.today()
    affected: set[str] = set()
    d = start
    while d <= today:
        end = min(d + timedelta(days=_CHUNK_DAYS - 1), today)
        affected |= _ingest_window(engine, d, end, session)
        d = end + timedelta(days=1)
    for sym in sorted(affected):
        n = recompute_symbol(engine, sym)
        log_event(LOGGER, "ca_recomputed", symbol=sym, rows=n)


def setup_db(engine) -> None:
    """Create this pipeline's own table (corporate_actions) and exit —
    scoped, same convention as screener_pipeline.py's setup_db(). The
    prices_daily.adj_close column it writes into is owned by
    eod_prices_pipeline.py's own table definition."""
    corporate_actions.create(engine, checkfirst=True)
    log_event(LOGGER, "ca_db_table_created")


def main() -> None:
    init_error_tracking()
    parser = argparse.ArgumentParser(description="Corporate actions pipeline")
    parser.add_argument("--setup-db", action="store_true",
                        help="Create the corporate_actions table and exit")
    parser.add_argument("--reset-db", action="store_true",
                        help="Drop and recreate the corporate_actions table, then exit")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--backfill", type=str, metavar="YYYY-MM-DD",
                       help="ingest history from this date in chunks")
    group.add_argument("--recompute", type=str, metavar="SYMBOL",
                       help="recompute adj_close for one symbol")
    group.add_argument("--recompute-all", action="store_true",
                       help="reset and recompute adj_close for all symbols")
    args = parser.parse_args()

    if args.setup_db:
        setup_db(get_engine())
        return

    if args.reset_db:
        engine = get_engine()
        corporate_actions.drop(engine, checkfirst=True)
        corporate_actions.create(engine, checkfirst=True)
        log_event(LOGGER, "ca_db_table_reset")
        return

    engine = get_engine()
    if args.recompute:
        n = recompute_symbol(engine, args.recompute.strip().upper())
        print(f"{args.recompute.strip().upper()}: {n} rows recomputed")
    elif args.recompute_all:
        recompute_all(engine)
    elif args.backfill:
        backfill(engine, datetime.strptime(args.backfill, "%Y-%m-%d").date())
    else:
        run_ca_step(engine)


if __name__ == "__main__":
    main()
