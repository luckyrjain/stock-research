"""
SME Stocks EMA Crossover Pipeline
==================================
Fetches all Indian SME stocks (NSE Emerge + BSE SME), downloads 3 months of
daily OHLCV, computes EMA 20 and EMA 50, detects crossovers (both bullish and
bearish) and stores results in PostgreSQL.

Usage:
    python sme_ema_pipeline.py --setup-db   # create tables (idempotent)
    python sme_ema_pipeline.py              # run pipeline (24 h cache on stock lists)
    python sme_ema_pipeline.py --force      # bypass stock-list cache
    python sme_ema_pipeline.py --lookback 5 # days back to check crossovers (default 5)
"""

import argparse
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from sqlalchemy import text

from db.models import get_engine, metadata
from tools.sme_tools import get_all_sme_stocks

load_dotenv()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

_LOOKBACK_DAYS = 5
_OHLCV_PERIOD  = "3mo"   # ~62 trading days — enough for stable EMA 50
_MAX_WORKERS   = 8


# ── Phase 2: OHLCV fetch ──────────────────────────────────────────────────────

def _fetch_ohlcv(stock: dict) -> dict:
    """Download daily OHLCV for one SME stock. Never raises."""
    symbol   = stock["symbol"]
    exchange = stock["exchange"]
    suffix   = ".NS" if exchange == "NSE" else ".BO"
    ticker   = f"{symbol}{suffix}"
    try:
        df = yf.Ticker(ticker).history(period=_OHLCV_PERIOD, interval="1d", auto_adjust=True)
        if df.empty:
            return {"error": f"no data returned by yfinance", "symbol": symbol}
        df = df[["Close"]].dropna()
        return {"symbol": symbol, "exchange": exchange, "df": df}
    except Exception as exc:
        return {"error": str(exc), "symbol": symbol}


# ── Phase 3: EMA computation ──────────────────────────────────────────────────

def _compute_ema_signals(result: dict) -> list[dict]:
    """
    Compute EMA 20/50 and crossover flags for one stock.
    Returns a list of row dicts (one per trading day).
    """
    if "error" in result:
        return []

    symbol = result["symbol"]
    df = result["df"].copy()

    df["ema20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["Close"].ewm(span=50, adjust=False).mean()

    prev_close = df["Close"].shift(1)

    for ema_col, flag_col in [("ema20", "crossed_ema20"), ("ema50", "crossed_ema50")]:
        prev_ema = df[ema_col].shift(1)
        bullish  = (prev_close < prev_ema) & (df["Close"] >= df[ema_col])
        bearish  = (prev_close >= prev_ema) & (df["Close"] < df[ema_col])
        df[flag_col] = bullish | bearish

    # cross_direction: derived from EMA20 if it crossed, else EMA50
    prev_ema20  = df["ema20"].shift(1)
    prev_ema50  = df["ema50"].shift(1)
    bullish20   = (prev_close < prev_ema20) & (df["Close"] >= df["ema20"])
    bearish20   = (prev_close >= prev_ema20) & (df["Close"] < df["ema20"])
    bullish50   = (prev_close < prev_ema50) & (df["Close"] >= df["ema50"])
    bearish50   = (prev_close >= prev_ema50) & (df["Close"] < df["ema50"])

    df["cross_direction"] = np.where(
        bullish20, "bullish",
        np.where(bearish20, "bearish",
        np.where(bullish50, "bullish",
        np.where(bearish50, "bearish", None)))
    )
    # Blank out direction on rows with no crossover
    no_cross = ~(df["crossed_ema20"] | df["crossed_ema50"])
    df.loc[no_cross, "cross_direction"] = None

    rows = []
    for idx, row in df.iterrows():
        trade_date = idx.date() if hasattr(idx, "date") else idx
        direction  = row["cross_direction"]
        rows.append({
            "symbol":          symbol,
            "trade_date":      trade_date,
            "close_price":     _safe_float(row["Close"]),
            "ema20":           _safe_float(row["ema20"]),
            "ema50":           _safe_float(row["ema50"]),
            "crossed_ema20":   bool(row["crossed_ema20"]),
            "crossed_ema50":   bool(row["crossed_ema50"]),
            "cross_direction": None if (direction is None or pd.isna(direction)) else str(direction),
        })
    return rows


def _safe_float(val) -> float | None:
    try:
        f = float(val)
        return round(f, 4) if not np.isnan(f) else None
    except (TypeError, ValueError):
        return None


# ── Phase 4: PostgreSQL writes ────────────────────────────────────────────────

def _upsert_stocks(engine, stocks: list[dict]) -> None:
    if not stocks:
        return
    with engine.begin() as conn:
        for s in stocks:
            conn.execute(
                text("""
                    INSERT INTO sme_stocks (symbol, name, exchange, isin, series, fetched_at)
                    VALUES (:symbol, :name, :exchange, :isin, :series, NOW())
                    ON CONFLICT (symbol) DO UPDATE SET
                        name       = EXCLUDED.name,
                        isin       = EXCLUDED.isin,
                        fetched_at = NOW()
                """),
                {
                    "symbol":   s["symbol"],
                    "name":     s.get("name"),
                    "exchange": s["exchange"],
                    "isin":     s.get("isin") or None,
                    "series":   s.get("series"),
                },
            )
    logger.info("Upserted %d stocks into sme_stocks", len(stocks))


def _upsert_signals(engine, rows: list[dict]) -> None:
    if not rows:
        return
    batch_size = 500
    total = 0
    with engine.begin() as conn:
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            conn.execute(
                text("""
                    INSERT INTO ema_signals
                        (symbol, trade_date, close_price, ema20, ema50,
                         crossed_ema20, crossed_ema50, cross_direction, run_at)
                    VALUES
                        (:symbol, :trade_date, :close_price, :ema20, :ema50,
                         :crossed_ema20, :crossed_ema50, :cross_direction, NOW())
                    ON CONFLICT ON CONSTRAINT uq_ema_signals_symbol_date DO UPDATE SET
                        close_price     = EXCLUDED.close_price,
                        ema20           = EXCLUDED.ema20,
                        ema50           = EXCLUDED.ema50,
                        crossed_ema20   = EXCLUDED.crossed_ema20,
                        crossed_ema50   = EXCLUDED.crossed_ema50,
                        cross_direction = EXCLUDED.cross_direction,
                        run_at          = NOW()
                """),
                batch,
            )
            total += len(batch)
    logger.info("Upserted %d signal rows into ema_signals", total)


# ── Phase 5: Summary output ───────────────────────────────────────────────────

def _print_summary(engine, lookback_days: int) -> None:
    query = text("""
        SELECT
            s.symbol,
            s.name,
            s.exchange,
            e.trade_date,
            e.close_price,
            e.cross_direction,
            CASE
                WHEN e.crossed_ema20 AND e.crossed_ema50 THEN 'EMA20+EMA50'
                WHEN e.crossed_ema20                     THEN 'EMA20'
                ELSE                                          'EMA50'
            END AS crossed,
            e.ema20,
            e.ema50
        FROM ema_signals e
        JOIN sme_stocks  s USING (symbol)
        WHERE (e.crossed_ema20 OR e.crossed_ema50)
          AND e.trade_date >= CURRENT_DATE - (:lookback * INTERVAL '1 day')
        ORDER BY e.trade_date DESC, s.symbol
    """)
    with engine.connect() as conn:
        result = conn.execute(query, {"lookback": lookback_days}).fetchall()

    if not result:
        print(f"\nNo EMA crossovers found in the last {lookback_days} trading days.")
        return

    w = 80
    print(f"\n{'=' * w}")
    print(f"  SME Stocks — EMA Crossovers (last {lookback_days} days)")
    print(f"{'=' * w}")
    hdr = f"{'Date':<12} {'Symbol':<16} {'Exch':<6} {'Crossed':<14} {'Direction':<10} {'Close':>9} {'EMA20':>9} {'EMA50':>9}"
    print(hdr)
    print("-" * w)
    for row in result:
        print(
            f"{str(row.trade_date):<12} "
            f"{row.symbol:<16} "
            f"{row.exchange:<6} "
            f"{row.crossed:<14} "
            f"{(row.cross_direction or ''):<10} "
            f"{float(row.close_price or 0):>9.2f} "
            f"{float(row.ema20 or 0):>9.2f} "
            f"{float(row.ema50 or 0):>9.2f}"
        )
    print(f"\n  {len(result)} crossover event(s)\n")


# ── Setup ─────────────────────────────────────────────────────────────────────

def setup_db(engine) -> None:
    """Create tables and indexes (idempotent)."""
    metadata.create_all(engine)
    logger.info("Database tables created/verified")


# ── Pipeline entry point ──────────────────────────────────────────────────────

def run(force: bool = False, lookback_days: int = _LOOKBACK_DAYS) -> None:
    engine = get_engine()

    # Phase 1: fetch SME stock lists
    logger.info("Phase 1 — Fetching SME stock lists...")
    stocks = get_all_sme_stocks(force=force)
    if not stocks:
        logger.error("No SME stocks fetched — aborting")
        return
    logger.info("Total SME stocks: %d", len(stocks))

    # Must upsert stocks before signals (FK constraint)
    _upsert_stocks(engine, stocks)

    # Phase 2: download OHLCV in parallel
    logger.info("Phase 2 — Downloading OHLCV (%d stocks, %d workers)...", len(stocks), _MAX_WORKERS)
    ohlcv_ok, errors = [], 0
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_ohlcv, s): s["symbol"] for s in stocks}
        for future in as_completed(futures):
            res = future.result()
            if "error" in res:
                errors += 1
                logger.debug("OHLCV skip %s: %s", res["symbol"], res["error"])
            else:
                ohlcv_ok.append(res)
    logger.info("OHLCV: %d fetched, %d errors", len(ohlcv_ok), errors)

    # Phase 3: compute EMAs + crossover flags
    logger.info("Phase 3 — Computing EMA 20/50 crossovers...")
    all_rows = []
    for res in ohlcv_ok:
        all_rows.extend(_compute_ema_signals(res))
    logger.info("Signal rows: %d across %d stocks", len(all_rows), len(ohlcv_ok))

    # Phase 4: write to PostgreSQL
    logger.info("Phase 4 — Writing to PostgreSQL...")
    _upsert_signals(engine, all_rows)

    # Phase 5: print crossover summary
    _print_summary(engine, lookback_days)


def main() -> None:
    parser = argparse.ArgumentParser(description="SME EMA Crossover Pipeline")
    parser.add_argument("--setup-db",  action="store_true",
                        help="Create DB tables and exit")
    parser.add_argument("--force",     action="store_true",
                        help="Bypass 24 h cache on SME stock lists")
    parser.add_argument("--lookback",  type=int, default=_LOOKBACK_DAYS,
                        help=f"Days back to report crossovers (default: {_LOOKBACK_DAYS})")
    args = parser.parse_args()

    if args.setup_db:
        setup_db(get_engine())
        return

    run(force=args.force, lookback_days=args.lookback)


if __name__ == "__main__":
    main()
