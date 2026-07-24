import os

from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer,
    MetaData, Numeric, String, Table, UniqueConstraint, text,
)
from sqlalchemy import create_engine as _create_engine

metadata = MetaData()

sme_stocks = Table(
    "sme_stocks",
    metadata,
    Column("symbol",     String(20), primary_key=True),
    Column("name",       String),
    Column("exchange",   String(5),  nullable=False),
    Column("isin",       String(12)),
    Column("series",     String(10)),
    Column("fetched_at", DateTime(timezone=True), server_default=text("NOW()")),
    # Liquidity snapshot from the most recent pipeline run — average daily
    # share volume / turnover (₹) over the last 20 trading days, from the same
    # OHLCV fetch already done for EMA signals (no extra network calls). NULL
    # until the first run that stores volume alongside close price. Flags
    # illiquid SME stocks, where a golden cross isn't necessarily tradeable at
    # the shown price.
    Column("avg_volume_20d",   Numeric(16, 2)),
    Column("avg_turnover_20d", Numeric(16, 2)),
    # Market cap in ₹ Cr, via yfinance fast_info (one extra lightweight
    # request per stock beyond the OHLCV history() fetch). NULL until the
    # first run after this column was added, or if fast_info didn't have it.
    Column("market_cap_cr", Numeric(16, 2)),
)

ema_signals = Table(
    "ema_signals",
    metadata,
    Column("id",          Integer, primary_key=True, autoincrement=True),
    Column("symbol",      String(20), ForeignKey("sme_stocks.symbol"), nullable=False),
    Column("trade_date",  Date,       nullable=False),
    Column("close_price", Numeric(12, 4)),
    Column("ema20",       Numeric(12, 4)),
    Column("ema50",       Numeric(12, 4)),
    # Standard momentum-screener confirmation signals alongside the EMA
    # cross — see sme_ema_pipeline._compute_rsi / _compute_volume_spike.
    Column("rsi14",        Numeric(6, 2)),
    Column("volume_spike", Boolean),
    Column("cross_type",  String(10)),   # 'golden' | 'death' | NULL ('cross' is reserved in SQL)
    Column("run_at",      DateTime(timezone=True), server_default=text("NOW()")),
    UniqueConstraint("symbol", "trade_date", name="uq_ema_signals_symbol_date"),
    Index("idx_ema_signals_date",  "trade_date"),
    Index("idx_ema_signals_cross", "cross_type"),
)

# Cross-mode watchlist (stock analysis / market picks / SME signals share this
# one table). Each row is owned by exactly one identity: either the anonymous
# per-browser client_id (a UUID the frontend generates on first use and keeps
# in localStorage — see frontend/lib/watchlist.ts) for a logged-out visitor,
# or user_id for a signed-in account (see auth.py) — never both, never
# neither (enforced by the CHECK below). Signing in does NOT claim/merge an
# existing client_id's rows onto the account; api.py simply prefers the
# account identity over client_id whenever a valid session is present, so a
# freshly-signed-in user starts with whatever their account has already
# accumulated, not their anonymous history (see CLAUDE.md's "Account &
# magic-link auth flow" for the reasoning). Two separate UNIQUE constraints
# (rather than one over both columns) are needed because Postgres treats NULL
# as distinct from any other NULL in a unique index — a single
# UNIQUE(client_id, user_id, symbol) would let one account row exist per
# symbol without limit, since every anonymous row's user_id is NULL and every
# account row's client_id is NULL.
watchlist_items = Table(
    "watchlist_items",
    metadata,
    Column("id",         Integer, primary_key=True, autoincrement=True),
    Column("client_id",  String(36)),
    Column("user_id",    Integer, ForeignKey("users.id")),
    Column("symbol",     String(20),  nullable=False),
    Column("company",    String(200)),
    Column("exchange",   String(5)),
    Column("added_at",   DateTime(timezone=True), server_default=text("NOW()")),
    CheckConstraint(
        "(client_id IS NULL) <> (user_id IS NULL)",
        name="ck_watchlist_exactly_one_owner",
    ),
    UniqueConstraint("client_id", "symbol", name="uq_watchlist_client_symbol"),
    UniqueConstraint("user_id", "symbol", name="uq_watchlist_user_symbol"),
    Index("idx_watchlist_client", "client_id"),
    Index("idx_watchlist_user", "user_id"),
)

# One row per (symbol, day) the analysis pipeline actually ran, populated by
# verdict_history.save_snapshot() from both main.py (CLI) and api.py's
# /api/analyse SSE stream. Powers the "verdict timeline" strip in the stock
# analysis hero — a same-day re-run (cache hit or force refresh) upserts the
# existing row rather than adding a duplicate.
verdict_history = Table(
    "verdict_history",
    metadata,
    Column("id",             Integer, primary_key=True, autoincrement=True),
    Column("symbol",         String(20), nullable=False),
    Column("verdict_date",   Date,       nullable=False),
    Column("recommendation", String(10)),
    Column("confidence",     String(10)),
    Column("current_price",  Numeric(14, 4)),
    Column("signal_score",   Numeric(6, 2)),
    Column("created_at",     DateTime(timezone=True), server_default=text("NOW()")),
    UniqueConstraint("symbol", "verdict_date", name="uq_verdict_history_symbol_date"),
    Index("idx_verdict_history_symbol", "symbol"),
)


# Minimal magic-link auth (see auth.py): no passwords. A user is created on
# first successful link click, never via a separate signup step. Existing
# anonymous client_id data is never migrated onto an account — watchlist_items
# rows are simply owned by whichever identity (client_id or user_id) was
# active when each row was added (see CLAUDE.md's "Account & magic-link auth
# flow" for the reasoning).
users = Table(
    "users",
    metadata,
    Column("id",         Integer, primary_key=True, autoincrement=True),
    Column("email",      String(320), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), server_default=text("NOW()")),
)

# Single-use, short-lived tokens emailed to a user to sign in. Only a SHA-256
# hash of the token is ever stored — the raw token exists only in the email
# itself and the process memory that generated it, so a DB leak alone can't
# be used to sign in as anyone.
magic_links = Table(
    "magic_links",
    metadata,
    Column("id",         Integer, primary_key=True, autoincrement=True),
    Column("email",      String(320), nullable=False),
    Column("token_hash", String(64),  nullable=False, unique=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("used_at",    DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), server_default=text("NOW()")),
    Index("idx_magic_links_email", "email"),
)

# Opaque bearer session tokens (same hash-only-storage convention as
# magic_links above). The frontend's Next.js proxy routes hold the raw token
# in an httpOnly cookie and forward it as `Authorization: Bearer <token>` —
# this table (and auth.py) never sees a cookie, only the token.
sessions = Table(
    "sessions",
    metadata,
    Column("id",         Integer, primary_key=True, autoincrement=True),
    Column("user_id",    Integer, ForeignKey("users.id"), nullable=False),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=text("NOW()")),
    Index("idx_sessions_user", "user_id"),
)


def get_engine(database_url: str | None = None):
    url = database_url or os.environ["DATABASE_URL"]
    return _create_engine(url)
