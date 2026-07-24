-- SME Stocks EMA Crossover Schema
-- Usage: psql $DATABASE_URL -f db/schema.sql
-- Or (fresh installs only): python sme_ema_pipeline.py --setup-db
--
-- `--setup-db` calls SQLAlchemy's metadata.create_all(), which only creates
-- tables that don't exist yet — it never ALTERs an existing table to pick up
-- a schema change (e.g. a new column). This file is written to be safe to
-- re-run against a database that already has some/all tables (every
-- CREATE TABLE is IF NOT EXISTS, and later ALTER TABLE statements against
-- pre-existing tables are themselves idempotent/guarded — see the
-- watchlist_items migration below for the pattern), so on an existing
-- deployment this script — not `--setup-db` — is the safe way to pick up a
-- schema change without losing data.

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
--
-- Written to be safely re-runnable both against a fresh database (this
-- file's own CREATE TABLE above already declares client_id nullable, so
-- these statements are no-ops there) AND against a live database from
-- before this migration existed, where client_id was NOT NULL and the
-- CREATE TABLE IF NOT EXISTS above is itself a no-op against the existing
-- table — this ALTER TABLE is the only thing that actually relaxes it there.
-- Every existing row already satisfies the new CHECK/UNIQUE once that NOT
-- NULL is dropped (client_id set, user_id NULL). This file has no separate
-- migration-runner (psql -f db/schema.sql is meant to be idempotent
-- end-to-end — see its own header), so the constraint additions are guarded
-- by an existence check rather than plain ADD CONSTRAINT, which errors if
-- already present (Postgres has no ADD CONSTRAINT IF NOT EXISTS).
ALTER TABLE watchlist_items ALTER COLUMN client_id DROP NOT NULL;
ALTER TABLE watchlist_items ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_watchlist_exactly_one_owner') THEN
        ALTER TABLE watchlist_items
            ADD CONSTRAINT ck_watchlist_exactly_one_owner CHECK ((client_id IS NULL) <> (user_id IS NULL));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_watchlist_user_symbol') THEN
        ALTER TABLE watchlist_items
            ADD CONSTRAINT uq_watchlist_user_symbol UNIQUE (user_id, symbol);
    END IF;
END $$;

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

-- Long-lived programmatic-access credentials (same hash-only-storage
-- convention as magic_links/sessions above). No fixed TTL — valid until
-- revoked_at is set. key_prefix is not secret; it's stored only so the
-- key-management UI can list keys without ever re-displaying the raw secret.
CREATE TABLE IF NOT EXISTS api_keys (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    key_hash      VARCHAR(64)  NOT NULL UNIQUE,
    key_prefix    VARCHAR(16)  NOT NULL,
    label         VARCHAR(120),
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    last_used_at  TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
