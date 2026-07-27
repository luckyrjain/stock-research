"""
SME Stocks EMA Crossover Pipeline
==================================
Fetches all Indian SME stocks (NSE Emerge + BSE SME), downloads 1 year of
daily OHLCV, computes EMA 20 and EMA 50, detects golden/death crosses
(EMA20 crossing EMA50) and stores the last ~3 months in PostgreSQL. Also
computes RSI(14) and a volume-spike flag per day (momentum-screener
confirmation signals alongside the EMA cross), and stores avg daily
volume/turnover + market cap per stock, from the same OHLCV/fast_info fetch
— no extra network calls beyond one lightweight fast_info lookup per stock
— to flag illiquid names and show market cap inline.

Usage:
    python sme_ema_pipeline.py --setup-db   # create tables (idempotent)
    python sme_ema_pipeline.py              # run pipeline (24 h cache on stock lists)
    python sme_ema_pipeline.py --force      # bypass stock-list cache
    python sme_ema_pipeline.py --lookback 5 # days back to check crossovers (default 5)
    python sme_ema_pipeline.py --reset-db   # drop and recreate DB tables, then exit
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from sqlalchemy import text

from db.models import get_engine, metadata
from error_tracking import init_error_tracking
from observability import get_logger, log_event
from tools.sme_tools import get_all_sme_stocks

load_dotenv()
LOGGER = get_logger("sme_ema_pipeline")

_LOOKBACK_DAYS = 5
_OHLCV_PERIOD  = "1y"    # full year so EMA 50 is converged before the stored window
_STORE_DAYS    = 63      # ~3 months of trading days kept in the DB
_RETENTION_DAYS = 100    # calendar days ≈ _STORE_DAYS trading days + buffer; older rows pruned
_MAX_WORKERS   = 8
# Recently-listed SME stocks can have well under a year of history. With
# adjust=False, an "EMA50" computed on too few bars is really just a
# recency-weighted average of all of them, not a converged 50-day EMA, and
# produces spurious crosses right after listing. Require a healthy margin
# above the 50-span before trusting a cross flag.
_MIN_HISTORY_DAYS = 75
# If more than this fraction of monitored stocks fail their OHLCV fetch,
# treat the whole run as unhealthy (run() returns False) rather than a normal
# handful of delisted/renamed symbols — almost always means NSE/yfinance is
# rate-limiting or blocking this run rather than genuinely bad individual
# symbols. Callers use this to fail loudly (e.g. the scheduled CI workflow
# exits non-zero so GitHub's built-in run-failure notification fires)
# instead of silently "succeeding" with mostly-empty data.
_MAX_ACCEPTABLE_ERROR_RATE = 0.5


# ── Phase 2: OHLCV fetch ──────────────────────────────────────────────────────

def _fetch_ohlcv(stock: dict) -> dict:
    """Download daily OHLCV for one SME stock. Never raises."""
    symbol   = stock["symbol"]
    exchange = stock["exchange"]
    suffix   = ".NS" if exchange == "NSE" else ".BO"
    ticker   = f"{symbol}{suffix}"
    try:
        yf_ticker = yf.Ticker(ticker)
        df = yf_ticker.history(period=_OHLCV_PERIOD, interval="1d", auto_adjust=True)
        if df.empty:
            return {"error": f"no data returned by yfinance", "symbol": symbol}
        # Keep Volume alongside Close (used for liquidity, see _compute_liquidity)
        # — only require Close to be present; a legitimately illiquid day can
        # have zero volume, which dropna(subset=["Close"]) correctly keeps.
        # Volume is normally always present for equities, but if yfinance ever
        # omits it for some ticker, EMA/cross detection must still succeed —
        # only the liquidity figure (already optional downstream) is lost.
        cols = ["Close", "Volume"] if "Volume" in df.columns else ["Close"]
        df = df[cols].dropna(subset=["Close"])
        if "Volume" in df.columns:
            df["Volume"] = df["Volume"].fillna(0)
        return {
            "symbol": symbol, "exchange": exchange, "df": df,
            "market_cap_cr": _safe_market_cap_cr(yf_ticker),
        }
    except Exception as exc:
        return {"error": str(exc), "symbol": symbol}


def _safe_market_cap_cr(yf_ticker) -> float | None:
    """Market cap in ₹ Cr, via fast_info — a second request per stock beyond
    history(), but a light one. Trailing P/E deliberately isn't fetched here:
    it needs yfinance's full .info scrape (much heavier), which across
    potentially hundreds of SME stocks per run would meaningfully add to this
    pipeline's already rate-limit-sensitive runtime for one inline column.
    Best-effort and never raises — a market cap miss must not cost this stock
    its OHLCV/cross data, so this is never counted as an OHLCV fetch error.
    """
    try:
        mcap = getattr(yf_ticker.fast_info, "market_cap", None)
        return round(mcap / 1e7, 2) if mcap else None
    except Exception:
        return None


# ── Phase 3: EMA computation ──────────────────────────────────────────────────

def _compute_ema_signals(result: dict) -> list[dict]:
    """
    Compute EMA 20/50 and golden/death cross flags for one stock.
    A golden cross fires when EMA20 crosses from <= EMA50 to > EMA50; death is the reverse.
    EMAs are computed over the full fetched series so EMA50 is converged;
    only the last _STORE_DAYS rows are returned for storage.
    """
    if "error" in result:
        return []

    symbol = result["symbol"]
    df = result["df"].copy()
    has_enough_history = len(df) >= _MIN_HISTORY_DAYS

    df["ema20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["rsi14"] = _compute_rsi(df["Close"])
    df["volume_spike"] = _compute_volume_spike(df)

    above = df["ema20"] > df["ema50"]
    prev_above = above.shift(1)
    golden = above & (prev_above == False)   # noqa: E712 — elementwise; NaN first row never flags
    death  = (~above) & (prev_above == True)  # noqa: E712
    # Per-bar convergence guard: a cross is only trusted at a bar that
    # itself has _MIN_HISTORY_DAYS of preceding closes behind it — checking
    # only the TOTAL series length (has_enough_history, below) isn't
    # enough, since a stock whose total history just clears that threshold
    # (e.g. 80 days) can still have an early cross fire well before that
    # (e.g. day 28, with only 28 bars of EMA50 warm-up — exactly the
    # "recency-weighted average, not a converged EMA" case _MIN_HISTORY_DAYS
    # exists to exclude), and that early cross can still land inside the
    # stored/served window once only the last _STORE_DAYS rows are kept
    # below (a stock between _MIN_HISTORY_DAYS and _MIN_HISTORY_DAYS +
    # _STORE_DAYS days old has its whole history — including that early,
    # unconverged cross — inside the stored tail).
    converged = np.arange(len(df)) >= (_MIN_HISTORY_DAYS - 1)
    golden = golden & converged
    death  = death & converged
    df["cross"] = np.where(golden, "golden", np.where(death, "death", None))

    if not has_enough_history:
        # EMA50 isn't converged anywhere in this series yet — still store
        # price/EMA for the current-regime view (the converged mask above
        # already guarantees no cross is claimed), just log it for
        # visibility into how often this happens across the SME universe.
        log_event(
            LOGGER, "sme_insufficient_history", level="debug",
            symbol=symbol, trading_days=len(df), min_required=_MIN_HISTORY_DAYS,
        )

    df = df.iloc[-_STORE_DAYS:]

    rows = []
    for idx, row in df.iterrows():
        trade_date = idx.date() if hasattr(idx, "date") else idx
        cross = row["cross"]
        volume_spike = row["volume_spike"]
        rows.append({
            "symbol":       symbol,
            "trade_date":   trade_date,
            "close_price":  _safe_float(row["Close"]),
            "ema20":        _safe_float(row["ema20"]),
            "ema50":        _safe_float(row["ema50"]),
            "rsi14":        _safe_float(row["rsi14"]),
            "volume_spike": bool(volume_spike) if pd.notna(volume_spike) else None,
            "cross":        None if (cross is None or pd.isna(cross)) else str(cross),
        })
    return rows


def _safe_float(val) -> float | None:
    try:
        f = float(val)
        return round(f, 4) if not np.isnan(f) else None
    except (TypeError, ValueError):
        return None


_RSI_PERIOD = 14


def _compute_rsi(close: pd.Series) -> pd.Series:
    """RSI(14), Wilder-style exponential smoothing via pandas ewm — standard
    momentum-screener confirmation alongside the EMA cross. Note this isn't a
    bit-exact match to textbook Wilder's method (which seeds avg_gain/avg_loss
    with a plain mean of the first 14 deltas before switching to smoothing;
    ewm(adjust=False) instead seeds recursively from the very first delta) —
    the difference only affects the first handful of post-warmup values and
    has fully decayed away by the time anything here gets stored (only the
    last _STORE_DAYS rows of a full year's fetch are ever persisted).
    NaN for the first _RSI_PERIOD rows (not enough history to smooth over
    yet); a completely flat price (no gains or losses at all, vanishingly
    rare for a real stock) is treated as neutral (50), not undefined, since a
    straight-up (avg_loss == 0, avg_gain > 0) or straight-down (avg_gain ==
    0, avg_loss > 0) move already resolves correctly to 100/0 through plain
    float division.
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / _RSI_PERIOD, adjust=False, min_periods=_RSI_PERIOD).mean()
    avg_loss = loss.ewm(alpha=1 / _RSI_PERIOD, adjust=False, min_periods=_RSI_PERIOD).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
    return rsi


_VOLUME_SPIKE_WINDOW_DAYS = 20
_VOLUME_SPIKE_MULTIPLIER  = 2.0


def _compute_volume_spike(df: pd.DataFrame) -> pd.Series:
    """True when a day's volume is more than _VOLUME_SPIKE_MULTIPLIER times
    its trailing _VOLUME_SPIKE_WINDOW_DAYS average — a cross with no volume
    confirmation behind it is a weak signal on its own. NaN (never guessed)
    for the first _VOLUME_SPIKE_WINDOW_DAYS rows of a stock's history, or
    for the whole series if this fetch has no Volume column at all (see
    _fetch_ohlcv) — comparing against NaN would otherwise silently resolve
    to False ("no spike") rather than "unknown," so those rows are masked
    back to NaN explicitly rather than trusting the comparison operator.
    """
    if "Volume" not in df.columns:
        return pd.Series(np.nan, index=df.index)
    avg_volume = df["Volume"].rolling(window=_VOLUME_SPIKE_WINDOW_DAYS, min_periods=_VOLUME_SPIKE_WINDOW_DAYS).mean()
    spike = df["Volume"] > (_VOLUME_SPIKE_MULTIPLIER * avg_volume)
    return spike.where(avg_volume.notna())


_LIQUIDITY_WINDOW_DAYS = 20


def _compute_liquidity(result: dict) -> dict | None:
    """Avg daily share volume / turnover (₹) over the last _LIQUIDITY_WINDOW_DAYS
    trading days, from the same OHLCV fetch already done for EMA signals — no
    extra network calls. None if the stock errored out or has no trading days
    at all (never invents a liquidity figure from partial/missing data).
    """
    if "error" in result:
        return None
    symbol = result["symbol"]
    df = result["df"]
    if df.empty or "Volume" not in df.columns:
        return None

    window = df.tail(_LIQUIDITY_WINDOW_DAYS)
    avg_volume = window["Volume"].mean()
    avg_turnover = (window["Close"] * window["Volume"]).mean()
    return {
        "symbol":           symbol,
        "avg_volume_20d":   _safe_float(avg_volume),
        "avg_turnover_20d": _safe_float(avg_turnover),
    }


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
                        exchange   = EXCLUDED.exchange,
                        isin       = EXCLUDED.isin,
                        series     = EXCLUDED.series,
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
    log_event(LOGGER, "sme_stocks_upserted", count=len(stocks))


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
                        (symbol, trade_date, close_price, ema20, ema50, rsi14, volume_spike, cross_type, run_at)
                    VALUES
                        (:symbol, :trade_date, :close_price, :ema20, :ema50, :rsi14, :volume_spike, :cross, NOW())
                    ON CONFLICT ON CONSTRAINT uq_ema_signals_symbol_date DO UPDATE SET
                        close_price  = EXCLUDED.close_price,
                        ema20        = EXCLUDED.ema20,
                        ema50        = EXCLUDED.ema50,
                        rsi14        = EXCLUDED.rsi14,
                        volume_spike = EXCLUDED.volume_spike,
                        cross_type   = EXCLUDED.cross_type,
                        run_at       = NOW()
                """),
                batch,
            )
            total += len(batch)
    log_event(LOGGER, "sme_signals_upserted", count=total)


def _upsert_liquidity(engine, rows: list[dict]) -> None:
    """Update sme_stocks' avg_volume_20d/avg_turnover_20d. A separate pass
    from _upsert_stocks (Phase 1, before OHLCV is fetched) since liquidity
    isn't known until Phase 3 — an UPDATE, not an upsert, since the row
    already exists by the time this runs."""
    if not rows:
        return
    with engine.begin() as conn:
        for r in rows:
            conn.execute(
                text("""
                    UPDATE sme_stocks
                    SET avg_volume_20d   = :avg_volume_20d,
                        avg_turnover_20d = :avg_turnover_20d
                    WHERE symbol = :symbol
                """),
                r,
            )
    log_event(LOGGER, "sme_liquidity_upserted", count=len(rows))


def _extract_market_cap(result: dict) -> dict | None:
    """None if the stock errored out or fast_info's market cap wasn't
    available (see _safe_market_cap_cr) — never invented."""
    if "error" in result:
        return None
    mcap = result.get("market_cap_cr")
    if mcap is None:
        return None
    return {"symbol": result["symbol"], "market_cap_cr": mcap}


def _upsert_market_cap(engine, rows: list[dict]) -> None:
    """Update sme_stocks.market_cap_cr. Same UPDATE-not-upsert pattern and
    reasoning as _upsert_liquidity — known only after Phase 2's fetch."""
    if not rows:
        return
    with engine.begin() as conn:
        for r in rows:
            conn.execute(
                text("UPDATE sme_stocks SET market_cap_cr = :market_cap_cr WHERE symbol = :symbol"),
                r,
            )
    log_event(LOGGER, "sme_market_cap_upserted", count=len(rows))


def _prune_signals(engine) -> None:
    with engine.begin() as conn:
        deleted = conn.execute(
            text("""
                DELETE FROM ema_signals
                WHERE trade_date < CURRENT_DATE - (:days * INTERVAL '1 day')
            """),
            {"days": _RETENTION_DAYS},
        ).rowcount
    log_event(LOGGER, "sme_signals_pruned", count=deleted, retention_days=_RETENTION_DAYS)


# ── Phase 5: Summary output ───────────────────────────────────────────────────

def _print_summary(engine, lookback_days: int) -> None:
    query = text("""
        SELECT
            s.symbol,
            s.name,
            s.exchange,
            e.trade_date,
            e.close_price,
            e.cross_type,
            e.ema20,
            e.ema50
        FROM ema_signals e
        JOIN sme_stocks  s USING (symbol)
        WHERE e.cross_type IS NOT NULL
          AND e.trade_date >= CURRENT_DATE - (:lookback * INTERVAL '1 day')
        ORDER BY e.trade_date DESC, s.symbol
    """)
    with engine.connect() as conn:
        result = conn.execute(query, {"lookback": lookback_days}).fetchall()

    if not result:
        print(f"\nNo golden/death crosses found in the last {lookback_days} days.")
        return

    w = 80
    print(f"\n{'=' * w}")
    print(f"  SME Stocks — EMA20/EMA50 Golden & Death Crosses (last {lookback_days} days)")
    print(f"{'=' * w}")
    hdr = f"{'Date':<12} {'Symbol':<16} {'Exch':<6} {'Cross':<10} {'Close':>9} {'EMA20':>9} {'EMA50':>9}"
    print(hdr)
    print("-" * w)
    for row in result:
        print(
            f"{str(row.trade_date):<12} "
            f"{row.symbol:<16} "
            f"{row.exchange:<6} "
            f"{(row.cross_type or ''):<10} "
            f"{float(row.close_price or 0):>9.2f} "
            f"{float(row.ema20 or 0):>9.2f} "
            f"{float(row.ema50 or 0):>9.2f}"
        )
    print(f"\n  {len(result)} cross event(s)\n")


# ── Setup ─────────────────────────────────────────────────────────────────────

def setup_db(engine) -> None:
    """Create tables and indexes (idempotent). Also stamps the database as
    already being at Alembic's latest revision — without this, a database
    set up via --setup-db (bypassing `alembic upgrade head` entirely) has
    every table but no `alembic_version` row, so a subsequent `alembic
    upgrade head` fails because the tables it wants to CREATE already
    exist. See db.models.stamp_alembic_head's own docstring."""
    metadata.create_all(engine)
    from db.models import stamp_alembic_head
    stamp_alembic_head()
    log_event(LOGGER, "sme_db_tables_created")


# ── Pipeline entry point ──────────────────────────────────────────────────────

def run(force: bool = False, lookback_days: int = _LOOKBACK_DAYS) -> bool:
    """Run the pipeline. Returns True on a healthy run, False if it was
    substantially unsuccessful (empty stock list, or too high an OHLCV fetch
    error rate to trust the result) — see _MAX_ACCEPTABLE_ERROR_RATE.
    """
    engine = get_engine()

    # Phase 1: fetch SME stock lists
    log_event(LOGGER, "sme_phase_started", phase="fetch_stock_lists")
    stocks = get_all_sme_stocks(force=force)
    if not stocks:
        log_event(LOGGER, "sme_no_stocks_fetched", level="error")
        return False
    log_event(LOGGER, "sme_stock_list_fetched", count=len(stocks))

    # Must upsert stocks before signals (FK constraint)
    _upsert_stocks(engine, stocks)

    # Phase 2: download OHLCV in parallel
    log_event(LOGGER, "sme_phase_started", phase="download_ohlcv", stocks=len(stocks), workers=_MAX_WORKERS)
    ohlcv_ok, errors = [], 0
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_ohlcv, s): s["symbol"] for s in stocks}
        for future in as_completed(futures):
            res = future.result()
            if "error" in res:
                errors += 1
                log_event(
                    LOGGER, "sme_ohlcv_fetch_skipped", level="debug",
                    symbol=res["symbol"], error=res["error"],
                )
            else:
                ohlcv_ok.append(res)
    log_event(LOGGER, "sme_ohlcv_fetch_completed", fetched=len(ohlcv_ok), errors=errors)

    # Phase 3: compute EMAs + golden/death cross flags, RSI/volume-spike, and liquidity
    log_event(LOGGER, "sme_phase_started", phase="compute_ema_crosses")
    all_rows = []
    liquidity_rows = []
    market_cap_rows = []
    for res in ohlcv_ok:
        all_rows.extend(_compute_ema_signals(res))
        liq = _compute_liquidity(res)
        if liq:
            liquidity_rows.append(liq)
        mcap = _extract_market_cap(res)
        if mcap:
            market_cap_rows.append(mcap)
    log_event(LOGGER, "sme_signal_rows_computed", rows=len(all_rows), stocks=len(ohlcv_ok))

    # Phase 4: write to PostgreSQL
    log_event(LOGGER, "sme_phase_started", phase="write_postgres")
    _upsert_signals(engine, all_rows)
    _upsert_liquidity(engine, liquidity_rows)
    _upsert_market_cap(engine, market_cap_rows)
    _prune_signals(engine)

    # Phase 5: print crossover summary
    _print_summary(engine, lookback_days)

    error_rate = errors / len(stocks)
    if error_rate > _MAX_ACCEPTABLE_ERROR_RATE:
        log_event(
            LOGGER, "sme_ohlcv_error_rate_exceeded", level="error",
            error_rate=round(error_rate, 3), threshold=_MAX_ACCEPTABLE_ERROR_RATE,
            note="NSE/yfinance may be rate-limiting or blocking this run rather than "
                 "these being genuinely bad individual symbols",
        )
        return False
    return True


def main() -> None:
    init_error_tracking()
    parser = argparse.ArgumentParser(description="SME EMA Crossover Pipeline")
    parser.add_argument("--setup-db",  action="store_true",
                        help="Create DB tables and exit")
    parser.add_argument("--reset-db",  action="store_true",
                        help="Drop and recreate DB tables, then exit")
    parser.add_argument("--force",     action="store_true",
                        help="Bypass 24 h cache on SME stock lists")
    parser.add_argument("--lookback",  type=int, default=_LOOKBACK_DAYS,
                        help=f"Days back to report crossovers (default: {_LOOKBACK_DAYS})")
    args = parser.parse_args()

    if args.setup_db:
        setup_db(get_engine())
        return

    if args.reset_db:
        engine = get_engine()
        metadata.drop_all(engine)
        metadata.create_all(engine)
        from db.models import stamp_alembic_head
        stamp_alembic_head()
        log_event(LOGGER, "sme_db_tables_reset")
        return

    healthy = run(force=args.force, lookback_days=args.lookback)
    if not healthy:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
