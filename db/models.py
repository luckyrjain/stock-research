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
    # API access tier (see "Programmatic API access flow" in CLAUDE.md) — 'free'
    # or 'pro', gating the per-user rate limit on GET /api/v1/*. Deliberately no
    # real payment processing: there is no signup/checkout flow that sets this
    # to 'pro' today, so every account is 'free' in practice until an operator
    # updates the column by hand. server_default keeps every pre-existing row
    # (created before this column existed) a well-defined 'free' rather than
    # NULL, which api._TIER_LIMITS would otherwise have to guess a fallback for.
    Column("tier",        String(10), nullable=False, server_default=text("'free'")),
    CheckConstraint("tier IN ('free', 'pro')", name="ck_users_tier"),
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

# Long-lived programmatic-access credentials (see "Programmatic API access
# flow" in CLAUDE.md) — same hash-only-storage convention as magic_links/
# sessions above: the raw key is shown to its owner exactly once, at creation
# time, and never persisted. Unlike a session, a key has no fixed TTL — it's
# valid until explicitly revoked (revoked_at set), since a script/integration
# holding it can't "re-sign-in" the way a browser can. `key_prefix` is not
# secret (first few chars of the raw key) — stored only so the key-management
# UI can list keys in a way a user can tell apart without ever re-displaying
# the secret.
api_keys = Table(
    "api_keys",
    metadata,
    Column("id",           Integer, primary_key=True, autoincrement=True),
    Column("user_id",      Integer, ForeignKey("users.id"), nullable=False),
    Column("key_hash",     String(64), nullable=False, unique=True),
    Column("key_prefix",   String(16), nullable=False),
    Column("label",        String(120)),
    Column("created_at",   DateTime(timezone=True), server_default=text("NOW()")),
    Column("last_used_at", DateTime(timezone=True)),
    Column("revoked_at",   DateTime(timezone=True)),
    Index("idx_api_keys_user", "user_id"),
)


# Custom stock screener (see screener_pipeline.py) — a stored-metrics table
# for the NIFTY 500 universe, mirroring sme_stocks' "fetch once, filter/sort
# many" shape so GET /api/screener never needs a live yfinance call per
# request. nse_industry comes from NSE's own published NIFTY 500 constituent
# list (tools/nifty500_tools.py) — used for filter chips in preference to
# `sector` (yfinance's own field), whose GICS-vs-Indian-market taxonomy for
# NSE/BSE symbols is an explicitly disclosed unverified assumption elsewhere
# in this codebase (see signals/engine.py's sector-weight-override comment).
# `sector` is kept alongside it for reference/future use, not as the primary
# filter dimension.
screener_stocks = Table(
    "screener_stocks",
    metadata,
    Column("symbol",         String(20), primary_key=True),
    Column("company_name",   String(200)),
    Column("exchange",       String(5)),
    Column("nse_industry",   String(100)),
    Column("sector",         String(100)),
    Column("current_price",  Numeric(14, 4)),
    Column("pe_ratio",       Numeric(10, 2)),
    Column("market_cap_cr",  Numeric(16, 2)),
    Column("avg_volume_10d", Numeric(16, 2)),
    # RSI(14) + EMA20/EMA50 trend posture, from the same shared computation
    # signals/technical.py already does for the main stock-analysis flow
    # (same Wilder-style RSI formula as sme_ema_pipeline._compute_rsi) — not
    # recomputed here, just persisted for filtering/sorting without a live
    # fetch per screener request.
    Column("rsi14",          Numeric(6, 2)),
    Column("ema_trend",      String(10)),   # 'bullish' | 'bearish' | NULL (unknown/insufficient history)
    Column("fetched_at",     DateTime(timezone=True), server_default=text("NOW()")),
    Index("idx_screener_stocks_industry", "nse_industry"),
    Index("idx_screener_stocks_sector", "sector"),
)


# One row per (symbol, as_of_date, fund) — a snapshot of every mutual fund's
# stake in a stock as of NSE's quarterly shareholding disclosure, populated
# by mf_holdings_history.save_snapshot() from main._fetch_task() (the one
# choke point both the CLI and api.py's SSE endpoint already funnel every
# data-slice fetch through — see CLAUDE.md's "Schema-drift detection"
# section for the same pattern applied to schema_drift). Only written on a
# genuine fresh fetch (a cache hit never reaches _fetch_task at all), so
# this table's cadence naturally follows however often mf_holdings actually
# gets re-fetched (its own 168h/7-day cache TTL) — not a separate poller.
# Powers the "stake change vs. prior quarter" trend on the stock analysis
# report — a same-quarter re-fetch upserts the existing row rather than
# adding a duplicate, same convention as verdict_history.
mf_holdings_history = Table(
    "mf_holdings_history",
    metadata,
    Column("id",           Integer, primary_key=True, autoincrement=True),
    Column("symbol",       String(20), nullable=False),
    Column("as_of_date",   Date,       nullable=False),
    Column("fund",         String(200), nullable=False),
    Column("holding_pct",  Numeric(6, 3), nullable=False),
    Column("created_at",   DateTime(timezone=True), server_default=text("NOW()")),
    UniqueConstraint("symbol", "as_of_date", "fund", name="uq_mf_holdings_history_symbol_date_fund"),
    Index("idx_mf_holdings_history_symbol", "symbol"),
)

# "I bought this" positions (see CLAUDE.md's "Positions" section) — a wholly
# new table, so (unlike watchlist_items, which evolved from client_id-only to
# dual-owner over two migrations) the exact-one-owner CHECK and both UNIQUE
# constraints are declared inline from the start, no ALTER TABLE needed. Same
# ownership shape as watchlist_items for the same reason: an anonymous
# per-browser client_id until the user signs in, then the account's user_id —
# never both, never neither — and signing in does NOT migrate an existing
# client_id's positions onto the account (same deliberate scope call as
# watchlist_items; see db/models.py's comment on that table for the full
# reasoning, which applies here unchanged).
# shares is user-entered, never scraped/computed/guessed — there's no source
# of truth for it, so it's NULL (not 0) until the user fills it in on the
# Portfolio page. Numeric rather than Integer since a mutual-fund-style
# fractional holding isn't out of the question.
positions = Table(
    "positions",
    metadata,
    Column("id",           Integer, primary_key=True, autoincrement=True),
    Column("client_id",    String(36)),
    Column("user_id",      Integer, ForeignKey("users.id")),
    Column("symbol",       String(20), nullable=False),
    Column("company",      String(200)),
    Column("exchange",     String(5)),
    Column("entry_price",  Numeric(14, 4)),
    Column("target_price", Numeric(14, 4)),
    Column("stop_loss",    Numeric(14, 4)),
    Column("shares",       Numeric(16, 4)),
    Column("bought_at",    DateTime(timezone=True), server_default=text("NOW()")),
    CheckConstraint(
        "(client_id IS NULL) <> (user_id IS NULL)",
        name="ck_positions_exactly_one_owner",
    ),
    UniqueConstraint("client_id", "symbol", name="uq_positions_client_symbol"),
    UniqueConstraint("user_id", "symbol", name="uq_positions_user_symbol"),
    Index("idx_positions_client", "client_id"),
    Index("idx_positions_user", "user_id"),
)


def get_engine(database_url: str | None = None):
    url = database_url or os.environ["DATABASE_URL"]
    return _create_engine(url)


def stamp_alembic_head() -> None:
    """Records "this database is already at the latest Alembic revision"
    without executing any DDL — the same one-time step CLAUDE.md's Alembic
    section documents for an *existing* deployment migrating onto Alembic
    for the first time (`alembic stamp head`).

    Both sme_ema_pipeline.py's and screener_pipeline.py's own --setup-db
    call metadata.create_all(engine) directly, bypassing Alembic entirely —
    a fresh database set up this way ends up with all 11 tables but no
    `alembic_version` row, so a subsequent `alembic upgrade head` fails
    (the tables Alembic wants to CREATE already exist). Calling this right
    after create_all() closes that drift vector: the CLI path and the
    Alembic-migration path always agree on "what revision is this database
    at" from the moment the database is created, not just for deployments
    that happened to run `alembic upgrade head` first.

    Reads DATABASE_URL from the environment, same as migrations/env.py's own
    `_database_url()` (which always wins over any Config-object URL in
    online mode) — so the caller's DATABASE_URL must already be set, same
    requirement get_engine() above already has.

    Never raises — a stamp failure (e.g. alembic.ini not found when running
    from an unusual working directory) is logged and swallowed rather than
    failing the whole --setup-db command, since the tables themselves were
    already created successfully by the caller before this runs.
    """
    import logging

    from alembic import command
    from alembic.config import Config

    logger = logging.getLogger("stock_research.db")
    try:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cfg = Config(os.path.join(repo_root, "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(repo_root, "migrations"))
        command.stamp(cfg, "head")
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("alembic_stamp_head_failed error=%s", exc)
