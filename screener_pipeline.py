"""
Custom Stock Screener Pipeline
================================
Fetches the NIFTY 500 constituent list (tools/nifty500_tools.py — NSE's own
published index membership, a bounded and real universe, not "all NSE
stocks") and, for each stock, reuses the SAME already-cached fetch functions
the rest of this codebase already has rather than duplicating any
scraping/OHLCV logic:

  - tools.nse_tools.get_stock_quote — price, P/E, market cap, sector/industry
    (one yfinance .info call per stock, same as the main analysis flow).
  - signals.technical.technical_signal — RSI14 + EMA20/EMA50 trend posture,
    off the already-6h-cached price_history series (see CLAUDE.md's
    "Technical signal" section).

Stores the result in a stored-metrics table (screener_stocks) so
GET /api/screener can filter/sort without a live fetch per request — the
same "fetch once, filter/sort many" shape sme_ema_pipeline.py established for
SME stocks.

Usage:
    python screener_pipeline.py --setup-db   # create tables (idempotent)
    python screener_pipeline.py              # run pipeline (24 h cache on constituent list)
    python screener_pipeline.py --force      # bypass constituent-list cache
    python screener_pipeline.py --reset-db   # drop and recreate DB tables, then exit
"""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
from sqlalchemy import text

from db.models import get_engine, metadata, screener_stocks
from error_tracking import init_error_tracking
from observability import get_logger, log_event
from tools.nifty500_tools import get_nifty500_constituents

load_dotenv()
LOGGER = get_logger("screener_pipeline")

_MAX_WORKERS = 8
# If more than this fraction of the universe fails its metrics fetch, treat
# the whole run as unhealthy (run() returns False) — same threshold and
# reasoning as sme_ema_pipeline._MAX_ACCEPTABLE_ERROR_RATE: almost always
# NSE/yfinance rate-limiting rather than genuinely bad individual symbols.
_MAX_ACCEPTABLE_ERROR_RATE = 0.5


def _fetch_one(stock: dict) -> dict:
    """Fetch quote + technical metrics for one stock. Never raises.

    The two calls are isolated in their own try/except — a technical_signal
    failure (e.g. a transient pandas/price-history hiccup) must not discard
    an otherwise-good quote (price/P/E/market cap/sector), and vice versa.
    rsi14/ema_trend simply stay null (never guessed) if technical_signal
    couldn't be computed, same as when it legitimately returns UNKNOWN for
    too little history.
    """
    symbol = stock["symbol"]
    try:
        from tools.nse_tools import get_stock_quote

        quote = json.loads(get_stock_quote.run(symbol=symbol))
        if quote.get("error"):
            return {"error": quote["error"], "symbol": symbol}

        rsi14 = None
        ema_trend = None
        try:
            from signals.technical import technical_signal

            tech = technical_signal(symbol)
            rsi14 = tech.meta.get("rsi14")
            if "ema20_above_ema50" in tech.meta:
                ema_trend = "bullish" if tech.meta["ema20_above_ema50"] else "bearish"
        except Exception as exc:
            log_event(
                LOGGER, "screener_technical_signal_failed", level="debug",
                symbol=symbol, error=str(exc),
            )

        return {
            "symbol":         symbol,
            "company_name":   quote.get("company_name") or stock.get("company_name"),
            "exchange":       quote.get("primary_exchange") or "NSE",
            "nse_industry":   stock.get("industry"),
            "sector":         quote.get("sector"),
            "current_price":  quote.get("current_price"),
            "pe_ratio":       quote.get("pe_ratio"),
            "market_cap_cr":  quote.get("market_cap_cr"),
            "avg_volume_10d": quote.get("avg_volume_10d"),
            "rsi14":          rsi14,
            "ema_trend":      ema_trend,
        }
    except Exception as exc:
        return {"error": str(exc), "symbol": symbol}


def _upsert_stocks(engine, rows: list[dict]) -> None:
    if not rows:
        return
    batch_size = 200
    total = 0
    with engine.begin() as conn:
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            conn.execute(
                text("""
                    INSERT INTO screener_stocks
                        (symbol, company_name, exchange, nse_industry, sector,
                         current_price, pe_ratio, market_cap_cr, avg_volume_10d,
                         rsi14, ema_trend, fetched_at)
                    VALUES
                        (:symbol, :company_name, :exchange, :nse_industry, :sector,
                         :current_price, :pe_ratio, :market_cap_cr, :avg_volume_10d,
                         :rsi14, :ema_trend, NOW())
                    ON CONFLICT (symbol) DO UPDATE SET
                        company_name   = EXCLUDED.company_name,
                        exchange       = EXCLUDED.exchange,
                        nse_industry   = EXCLUDED.nse_industry,
                        sector         = EXCLUDED.sector,
                        current_price  = EXCLUDED.current_price,
                        pe_ratio       = EXCLUDED.pe_ratio,
                        market_cap_cr  = EXCLUDED.market_cap_cr,
                        avg_volume_10d = EXCLUDED.avg_volume_10d,
                        rsi14          = EXCLUDED.rsi14,
                        ema_trend      = EXCLUDED.ema_trend,
                        fetched_at     = NOW()
                """),
                batch,
            )
            total += len(batch)
    log_event(LOGGER, "screener_stocks_upserted", count=total)


def setup_db(engine) -> None:
    """Create tables and indexes (idempotent). Also stamps the database as
    already being at Alembic's latest revision — see
    db.models.stamp_alembic_head's own docstring for why: without this, a
    database set up via --setup-db has every table but no `alembic_version`
    row, so a subsequent `alembic upgrade head` fails."""
    metadata.create_all(engine)
    from db.models import stamp_alembic_head
    stamp_alembic_head()
    log_event(LOGGER, "screener_db_tables_created")


def run(force: bool = False) -> bool:
    """Run the pipeline. Returns True on a healthy run, False if it was
    substantially unsuccessful (empty constituent list, or too high a
    metrics-fetch error rate to trust the result) — see
    _MAX_ACCEPTABLE_ERROR_RATE.
    """
    engine = get_engine()

    log_event(LOGGER, "screener_phase_started", phase="fetch_constituents")
    stocks = get_nifty500_constituents(force=force)
    if not stocks:
        log_event(LOGGER, "screener_no_stocks_fetched", level="error")
        return False
    log_event(LOGGER, "screener_constituent_list_fetched", count=len(stocks))

    log_event(
        LOGGER, "screener_phase_started", phase="fetch_metrics",
        stocks=len(stocks), workers=_MAX_WORKERS,
    )
    ok_rows, errors = [], 0
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_one, s): s["symbol"] for s in stocks}
        for future in as_completed(futures):
            res = future.result()
            if "error" in res:
                errors += 1
                log_event(
                    LOGGER, "screener_metric_fetch_skipped", level="debug",
                    symbol=res["symbol"], error=res["error"],
                )
            else:
                ok_rows.append(res)
    log_event(LOGGER, "screener_metrics_fetched", fetched=len(ok_rows), errors=errors)

    log_event(LOGGER, "screener_phase_started", phase="write_postgres")
    _upsert_stocks(engine, ok_rows)

    error_rate = errors / len(stocks)
    if error_rate > _MAX_ACCEPTABLE_ERROR_RATE:
        log_event(
            LOGGER, "screener_fetch_error_rate_exceeded", level="error",
            error_rate=round(error_rate, 3), threshold=_MAX_ACCEPTABLE_ERROR_RATE,
            note="NSE/yfinance may be rate-limiting or blocking this run rather than "
                 "these being genuinely bad individual symbols",
        )
        return False
    return True


def main() -> None:
    init_error_tracking()
    parser = argparse.ArgumentParser(description="Custom Stock Screener Pipeline")
    parser.add_argument("--setup-db", action="store_true",
                        help="Create DB tables and exit")
    parser.add_argument("--reset-db", action="store_true",
                        help="Drop and recreate the screener_stocks table, then exit")
    parser.add_argument("--force",    action="store_true",
                        help="Bypass 24 h cache on the NIFTY 500 constituent list")
    args = parser.parse_args()

    if args.setup_db:
        setup_db(get_engine())
        return

    if args.reset_db:
        # Scoped to this pipeline's own table, not metadata.drop_all() — the
        # shared MetaData() in db/models.py holds every table in the app
        # (users, sessions, watchlist_items, ...), and dropping all of them
        # just to reset screener_stocks would take down unrelated,
        # NOT-regenerable data. sme_ema_pipeline.py --reset-db predates this
        # and still has that broader-than-intended blast radius — see
        # CLAUDE.md's disclosed limitation on it under "SME golden cross flow".
        engine = get_engine()
        screener_stocks.drop(engine, checkfirst=True)
        screener_stocks.create(engine, checkfirst=True)
        log_event(LOGGER, "screener_db_table_reset")
        return

    healthy = run(force=args.force)
    if not healthy:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
