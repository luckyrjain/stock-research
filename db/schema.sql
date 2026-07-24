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

-- Cross-mode watchlist. Each row is owned by exactly one identity: the
-- anonymous per-browser client_id (a UUID the frontend keeps in
-- localStorage) or, once accounts exist below, user_id — never both, never
-- neither. user_id/its FK/CHECK/UNIQUE are added via ALTER TABLE further
-- down, after the users table exists (this file runs top-to-bottom).
CREATE TABLE IF NOT EXISTS watchlist_items (
    id          SERIAL PRIMARY KEY,
    client_id   VARCHAR(36),
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

-- Minimal magic-link auth (see auth.py). No passwords. A user row is created
-- on first successful link click, not via a separate signup step. Existing
-- anonymous client_id data is never migrated onto an account — accounts are
-- additive, not a migration.
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    email       VARCHAR(320) NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Links watchlist_items to an account, now that users exists. Exactly one of
-- client_id/user_id must be set per row (see the comment on watchlist_items'
-- own CREATE TABLE above) — enforced by ck_watchlist_exactly_one_owner.
ALTER TABLE watchlist_items
    ADD COLUMN user_id INTEGER REFERENCES users(id),
    ADD CONSTRAINT ck_watchlist_exactly_one_owner CHECK ((client_id IS NULL) <> (user_id IS NULL)),
    ADD CONSTRAINT uq_watchlist_user_symbol UNIQUE (user_id, symbol);

CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist_items(user_id);

-- Single-use, short-lived sign-in tokens. Only a SHA-256 hash of the token is
-- stored — the raw token exists only in the email itself and the process
-- memory that generated it.
CREATE TABLE IF NOT EXISTS magic_links (
    id          SERIAL PRIMARY KEY,
    email       VARCHAR(320) NOT NULL,
    token_hash  VARCHAR(64)  NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ  NOT NULL,
    used_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_magic_links_email ON magic_links(email);

-- Opaque bearer session tokens (same hash-only-storage convention as
-- magic_links). The frontend's Next.js proxy routes hold the raw token in an
-- httpOnly cookie and forward it as `Authorization: Bearer <token>`.
CREATE TABLE IF NOT EXISTS sessions (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    token_hash  VARCHAR(64) NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
