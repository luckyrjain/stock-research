-- SME Stocks EMA Crossover Schema
-- Usage: psql $DATABASE_URL -f db/schema.sql
-- Or: python sme_ema_pipeline.py --setup-db

CREATE TABLE IF NOT EXISTS sme_stocks (
    symbol            VARCHAR(20) PRIMARY KEY,
    name              TEXT,
    exchange          VARCHAR(5)  NOT NULL,   -- 'NSE' | 'BSE'
    isin              VARCHAR(12),
    series            VARCHAR(10),
    fetched_at        TIMESTAMPTZ DEFAULT NOW(),
    -- Avg daily share volume / turnover (₹) over the last 20 trading days,
    -- from the same OHLCV fetch already done for EMA signals. NULL until the
    -- first run that stores volume alongside close price.
    avg_volume_20d    NUMERIC(16, 2),
    avg_turnover_20d  NUMERIC(16, 2),
    -- Market cap in ₹ Cr, via yfinance fast_info (one extra lightweight
    -- request per stock beyond the OHLCV history() fetch). NULL until the
    -- first run after this column was added, or if fast_info didn't have it.
    market_cap_cr     NUMERIC(16, 2)
);

CREATE TABLE IF NOT EXISTS ema_signals (
    id              SERIAL PRIMARY KEY,
    symbol          VARCHAR(20) NOT NULL REFERENCES sme_stocks(symbol),
    trade_date      DATE        NOT NULL,
    close_price     NUMERIC(12, 4),
    ema20           NUMERIC(12, 4),
    ema50           NUMERIC(12, 4),
    -- Standard momentum-screener confirmation signals alongside the EMA cross.
    rsi14           NUMERIC(6, 2),
    volume_spike    BOOLEAN,
    cross_type      VARCHAR(10),          -- 'golden' | 'death' | NULL
    run_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_ema_signals_date  ON ema_signals(trade_date);
CREATE INDEX IF NOT EXISTS idx_ema_signals_cross ON ema_signals(cross_type);

-- Cross-mode watchlist. No account system yet — client_id is a UUID the
-- frontend generates on first use and keeps in localStorage.
CREATE TABLE IF NOT EXISTS watchlist_items (
    id          SERIAL PRIMARY KEY,
    client_id   VARCHAR(36)  NOT NULL,
    symbol      VARCHAR(20)  NOT NULL,
    company     VARCHAR(200),
    exchange    VARCHAR(5),
    added_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(client_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_client ON watchlist_items(client_id);

-- One row per (symbol, day) the analysis pipeline actually ran. Powers the
-- "verdict timeline" strip on the stock analysis hero. A same-day re-run
-- upserts the existing row rather than adding a duplicate.
CREATE TABLE IF NOT EXISTS verdict_history (
    id              SERIAL PRIMARY KEY,
    symbol          VARCHAR(20) NOT NULL,
    verdict_date    DATE        NOT NULL,
    recommendation  VARCHAR(10),
    confidence      VARCHAR(10),
    current_price   NUMERIC(14, 4),
    signal_score    NUMERIC(6, 2),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, verdict_date)
);

CREATE INDEX IF NOT EXISTS idx_verdict_history_symbol ON verdict_history(symbol);
