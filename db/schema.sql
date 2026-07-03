-- SME Stocks EMA Crossover Schema
-- Usage: psql $DATABASE_URL -f db/schema.sql
-- Or: python sme_ema_pipeline.py --setup-db

CREATE TABLE IF NOT EXISTS sme_stocks (
    symbol      VARCHAR(20) PRIMARY KEY,
    name        TEXT,
    exchange    VARCHAR(5)  NOT NULL,   -- 'NSE' | 'BSE'
    isin        VARCHAR(12),
    series      VARCHAR(10),
    fetched_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ema_signals (
    id              SERIAL PRIMARY KEY,
    symbol          VARCHAR(20) NOT NULL REFERENCES sme_stocks(symbol),
    trade_date      DATE        NOT NULL,
    close_price     NUMERIC(12, 4),
    ema20           NUMERIC(12, 4),
    ema50           NUMERIC(12, 4),
    crossed_ema20   BOOLEAN     DEFAULT FALSE,
    crossed_ema50   BOOLEAN     DEFAULT FALSE,
    cross_direction VARCHAR(10),          -- 'bullish' | 'bearish' | NULL
    run_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_ema_signals_date  ON ema_signals(trade_date);
CREATE INDEX IF NOT EXISTS idx_ema_signals_cross ON ema_signals(crossed_ema20, crossed_ema50);
