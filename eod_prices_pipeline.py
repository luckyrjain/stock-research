"""
EOD Price Store Pipeline
========================
Ingests NSE full bhavcopy (equities: OHLC, volume, delivery) and AMFI NAVs
(held mutual fund schemes only) into PostgreSQL.

Usage:
    python eod_prices_pipeline.py                       # self-heal: last 5 weekdays
    python eod_prices_pipeline.py --date 2026-07-03     # one specific day
    python eod_prices_pipeline.py --backfill 2024-07-01 # every weekday from date to today

Table creation lives in db/prices_setup.py (--setup-db / --reset-db).

MF NAV ingestion only stores schemes actually held in a portfolio `assets`
table (type='mf') — this codebase doesn't have a portfolio system yet, so
`_held_scheme_codes()` degrades to "no held schemes" (logged, not raised)
until one exists. See CLAUDE.md's "EOD price store + corporate actions
flow" section.
"""

import argparse
import sys
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from sqlalchemy import text

from corporate_actions_pipeline import run_ca_step
from db.models import get_engine, metadata, mf_nav_daily, prices_daily, securities
from error_tracking import init_error_tracking
from observability import get_logger, log_event
from tools.eod_sources import (
    download_bhavcopy, download_equity_master, fetch_nav_all,
    fetch_scheme_history, make_nse_session, parse_bhavcopy,
    parse_equity_master, parse_nav_all,
)

_EOD_TABLES = [securities, prices_daily, mf_nav_daily]

load_dotenv()
LOGGER = get_logger("eod_prices")

_GAP_WINDOW = 5          # weekdays the default run self-heals over
_BACKFILL_HORIZON = 730  # days of MF NAV history to backfill
_BATCH_SIZE = 500


# ── Date logic ────────────────────────────────────────────────────────────────

def _missing_dates(existing: set[date], today: date, window: int = _GAP_WINDOW) -> list[date]:
    """Last `window` weekdays up to `today` that have no rows yet, ascending."""
    candidates: list[date] = []
    d = today
    while len(candidates) < window:
        if d.weekday() < 5:
            candidates.append(d)
        d -= timedelta(days=1)
    return sorted(c for c in candidates if c not in existing)


def _existing_dates(engine, since: date) -> set[date]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT trade_date FROM prices_daily WHERE trade_date >= :since"),
            {"since": since},
        ).fetchall()
    out = set()
    for (d,) in rows:
        out.add(d if isinstance(d, date) else datetime.strptime(str(d), "%Y-%m-%d").date())
    return out


# ── Upserts (column-list ON CONFLICT: works on PostgreSQL and SQLite) ─────────

def _upsert_prices(engine, rows: list[dict]) -> int:
    if not rows:
        return 0
    sql = text("""
        INSERT INTO prices_daily
            (symbol, trade_date, open, high, low, close, prev_close, avg_price,
             volume, turnover_lacs, trades, delivery_qty, delivery_pct, adj_close)
        VALUES
            (:symbol, :trade_date, :open, :high, :low, :close, :prev_close, :avg_price,
             :volume, :turnover_lacs, :trades, :delivery_qty, :delivery_pct, :close)
        ON CONFLICT (symbol, trade_date) DO UPDATE SET
            open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
            close = EXCLUDED.close, prev_close = EXCLUDED.prev_close,
            avg_price = EXCLUDED.avg_price, volume = EXCLUDED.volume,
            turnover_lacs = EXCLUDED.turnover_lacs, trades = EXCLUDED.trades,
            delivery_qty = EXCLUDED.delivery_qty, delivery_pct = EXCLUDED.delivery_pct
    """)
    with engine.begin() as conn:
        for i in range(0, len(rows), _BATCH_SIZE):
            conn.execute(sql, [
                {k: r.get(k) for k in (
                    "symbol", "trade_date", "open", "high", "low", "close",
                    "prev_close", "avg_price", "volume", "turnover_lacs",
                    "trades", "delivery_qty", "delivery_pct")}
                for r in rows[i:i + _BATCH_SIZE]
            ])
    return len(rows)


def _upsert_seen(engine, rows: list[dict]) -> None:
    """Mark symbols as seen in a bhavcopy. last_seen is kept monotonic
    regardless of ingest order, so re-ingesting a historical day (e.g. via
    `--date` for a timed-out old run) never regresses it (SQLite has no
    GREATEST, hence the CASE)."""
    if not rows:
        return
    sql = text("""
        INSERT INTO securities (symbol, series, last_seen)
        VALUES (:symbol, :series, :last_seen)
        ON CONFLICT (symbol) DO UPDATE SET
            series = EXCLUDED.series,
            last_seen = CASE WHEN securities.last_seen IS NULL
                               OR EXCLUDED.last_seen > securities.last_seen
                             THEN EXCLUDED.last_seen ELSE securities.last_seen END
    """)
    with engine.begin() as conn:
        for i in range(0, len(rows), _BATCH_SIZE):
            conn.execute(sql, rows[i:i + _BATCH_SIZE])


def _upsert_master(engine, rows: list[dict]) -> None:
    """Enrich securities from EQUITY_L.csv. Never touches last_seen or series
    — the daily bhavcopy via `_upsert_seen` is authoritative for series."""
    if not rows:
        return
    sql = text("""
        INSERT INTO securities (symbol, isin, company_name, series, listing_date, face_value)
        VALUES (:symbol, :isin, :company_name, :series, :listing_date, :face_value)
        ON CONFLICT (symbol) DO UPDATE SET
            isin = EXCLUDED.isin, company_name = EXCLUDED.company_name,
            listing_date = EXCLUDED.listing_date, face_value = EXCLUDED.face_value
    """)
    with engine.begin() as conn:
        for i in range(0, len(rows), _BATCH_SIZE):
            conn.execute(sql, rows[i:i + _BATCH_SIZE])


def _upsert_navs(engine, rows: list[dict]) -> int:
    if not rows:
        return 0
    sql = text("""
        INSERT INTO mf_nav_daily (scheme_code, nav_date, nav, scheme_name)
        VALUES (:scheme_code, :nav_date, :nav, :scheme_name)
        ON CONFLICT (scheme_code, nav_date) DO UPDATE SET
            nav = EXCLUDED.nav, scheme_name = EXCLUDED.scheme_name
    """)
    with engine.begin() as conn:
        for i in range(0, len(rows), _BATCH_SIZE):
            conn.execute(sql, rows[i:i + _BATCH_SIZE])
    return len(rows)


# ── Equity ingestion ──────────────────────────────────────────────────────────

def ingest_day(engine, trade_date: date, session) -> dict:
    """Ingest one day's bhavcopy. Returns a summary dict, never raises."""
    result = download_bhavcopy(trade_date, session)
    if result["status"] == "missing":
        return {"date": str(trade_date), "status": "missing"}
    if result["status"] == "error":
        return {"date": str(trade_date), "status": "error", "error": result["error"]}

    parsed = parse_bhavcopy(result["csv"])
    rows = parsed["rows"]
    if not rows:
        return {"date": str(trade_date), "status": "error", "error": "empty bhavcopy"}
    _upsert_seen(engine, [
        {"symbol": r["symbol"], "series": r["series"], "last_seen": r["trade_date"]}
        for r in rows
    ])
    count = _upsert_prices(engine, rows)
    return {
        "date": str(trade_date), "status": "ok", "rows": count,
        "skipped_series": parsed["skipped_series"], "malformed": parsed["malformed"],
    }


def refresh_securities_master(engine, session) -> None:
    result = download_equity_master(session)
    if result["status"] != "ok":
        log_event(LOGGER, "eod_master_failed", level="warning", error=result["error"])
        return
    rows = parse_equity_master(result["csv"])
    _upsert_master(engine, rows)
    log_event(LOGGER, "eod_master_refreshed", rows=len(rows))


# ── MF NAV ingestion ──────────────────────────────────────────────────────────

def _held_scheme_codes(engine) -> set[str]:
    """AMFI scheme codes of active MF assets (assets.symbol holds the code).

    Degrades to an empty set (never raises) when the `assets` table doesn't
    exist yet — this codebase doesn't have a portfolio system as of this
    pipeline shipping, so NAV ingestion is a documented no-op until one is
    built."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT DISTINCT symbol FROM assets "
                "WHERE type = 'mf' AND archived = FALSE AND symbol IS NOT NULL"
            )).fetchall()
        return {str(r[0]).strip() for r in rows if str(r[0]).strip()}
    except Exception as exc:
        log_event(LOGGER, "eod_held_schemes_failed", level="warning", error=str(exc))
        return set()


def _schemes_missing_history(engine, codes: set[str]) -> set[str]:
    if not codes:
        return set()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT DISTINCT scheme_code FROM mf_nav_daily")).fetchall()
    have = {str(r[0]) for r in rows}
    return codes - have


def ingest_navs(engine) -> None:
    """Daily NAV upsert for held schemes + history backfill for new schemes.
    Failures here must never fail the equity ingestion — log and return."""
    codes = _held_scheme_codes(engine)
    if not codes:
        log_event(LOGGER, "eod_nav_skipped", reason="no held mf schemes")
        return

    since = date.today() - timedelta(days=_BACKFILL_HORIZON)
    for code in sorted(_schemes_missing_history(engine, codes)):
        hist = fetch_scheme_history(code, since=since)
        if hist["status"] == "ok":
            n = _upsert_navs(engine, hist["rows"])
            log_event(LOGGER, "eod_nav_history_backfilled", scheme=code, rows=n)
        else:
            log_event(LOGGER, "eod_nav_history_failed", level="warning",
                      scheme=code, error=hist["error"])

    result = fetch_nav_all()
    if result["status"] != "ok":
        log_event(LOGGER, "eod_nav_all_failed", level="warning", error=result["error"])
        return
    n = _upsert_navs(engine, parse_nav_all(result["text"], codes))
    log_event(LOGGER, "eod_nav_ingested", schemes=len(codes), rows=n)


def setup_db(engine) -> None:
    """Create this pipeline's own tables (securities, prices_daily,
    mf_nav_daily) and exit — scoped, not metadata.create_all(engine), same
    convention as screener_pipeline.py's setup_db(). corporate_actions is
    owned by corporate_actions_pipeline.py's own --setup-db instead."""
    metadata.create_all(engine, tables=_EOD_TABLES)
    from db.models import stamp_alembic_head
    stamp_alembic_head()
    log_event(LOGGER, "eod_db_tables_created")


# ── Entry point ───────────────────────────────────────────────────────────────

def run(dates: list[date]) -> int:
    """Ingest the given dates (ascending) + refresh master + NAVs.
    Returns process exit code: 1 if any day errored (e.g. persistent bot-block)."""
    engine = get_engine()
    session = make_nse_session()

    refresh_securities_master(engine, session)

    had_error = False
    for d in sorted(dates):
        summary = ingest_day(engine, d, session)
        level = "error" if summary["status"] == "error" else "info"
        log_event(LOGGER, "eod_day_ingested", level=level, **summary)
        if summary["status"] == "error":
            had_error = True

    try:
        ingest_navs(engine)
    except Exception as exc:  # NAV step must never affect the equity exit code
        log_event(LOGGER, "eod_nav_step_failed", level="warning", error=str(exc))

    try:
        run_ca_step(engine, session)
    except Exception as exc:  # CA step must never affect the equity exit code
        log_event(LOGGER, "eod_ca_step_failed", level="warning", error=str(exc))

    try:
        from portfolio_valuation import refresh_valuations
        refresh_valuations(engine)
    except Exception as exc:  # valuation step must never affect the equity exit code
        log_event(LOGGER, "eod_valuation_step_failed", level="warning", error=str(exc))

    return 1 if had_error else 0


def main() -> None:
    init_error_tracking()
    parser = argparse.ArgumentParser(description="EOD price store pipeline")
    parser.add_argument("--setup-db", action="store_true",
                        help="Create DB tables (securities, prices_daily, mf_nav_daily) and exit")
    parser.add_argument("--reset-db", action="store_true",
                        help="Drop and recreate this pipeline's own tables, then exit")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--date", type=str, metavar="YYYY-MM-DD",
                       help="ingest one specific day")
    group.add_argument("--backfill", type=str, metavar="YYYY-MM-DD",
                       help="ingest every weekday from this date to today")
    args = parser.parse_args()

    if args.setup_db:
        setup_db(get_engine())
        return

    if args.reset_db:
        # Scoped to this pipeline's own tables, not metadata.drop_all() —
        # same reasoning as screener_pipeline.py's --reset-db (see CLAUDE.md's
        # disclosed limitation on sme_ema_pipeline.py --reset-db for why a
        # blanket drop_all() is unsafe on a shared MetaData()).
        engine = get_engine()
        for table in reversed(_EOD_TABLES):
            table.drop(engine, checkfirst=True)
        for table in _EOD_TABLES:
            table.create(engine, checkfirst=True)
        log_event(LOGGER, "eod_db_tables_reset")
        return

    today = date.today()
    if args.date:
        dates = [datetime.strptime(args.date, "%Y-%m-%d").date()]
    elif args.backfill:
        start = datetime.strptime(args.backfill, "%Y-%m-%d").date()
        dates = [start + timedelta(days=i) for i in range((today - start).days + 1)]
        dates = [d for d in dates if d.weekday() < 5]
    else:
        engine = get_engine()
        window_start = today - timedelta(days=_GAP_WINDOW * 2)
        dates = _missing_dates(_existing_dates(engine, window_start), today)
        if not dates:
            log_event(LOGGER, "eod_up_to_date")
            return

    sys.exit(run(dates))


if __name__ == "__main__":
    main()
