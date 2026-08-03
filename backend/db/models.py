import os

from sqlalchemy import (
    JSON, BigInteger, Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index,
    Integer, MetaData, Numeric, String, Table, UniqueConstraint, text,
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


# Custom stock screener (see pipelines/screener_pipeline.py) — a stored-metrics table
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

# ── EOD price store (securities/prices_daily/mf_nav_daily) + corporate
# actions (corporate_actions, prices_daily.adj_close). See CLAUDE.md's "EOD
# price store + corporate actions flow" section.

securities = Table(
    "securities",
    metadata,
    Column("symbol",       String(20), primary_key=True),
    Column("isin",         String(12)),
    Column("company_name", String(200)),
    Column("series",       String(10)),
    Column("listing_date", Date),
    Column("face_value",   Numeric(10, 2)),
    # Last date this symbol appeared in a bhavcopy — a stale last_seen
    # detects a delisting/suspension without a separate status field.
    Column("last_seen",    Date),
)

prices_daily = Table(
    "prices_daily",
    metadata,
    Column("symbol",        String(20), primary_key=True),
    Column("trade_date",    Date,       primary_key=True),
    Column("open",          Numeric(12, 2)),
    Column("high",          Numeric(12, 2)),
    Column("low",           Numeric(12, 2)),
    Column("close",         Numeric(12, 2)),
    Column("prev_close",    Numeric(12, 2)),
    Column("avg_price",     Numeric(12, 2)),
    Column("volume",        BigInteger),
    Column("turnover_lacs", Numeric(18, 2)),  # bhavcopy TURNOVER_LACS is already in lakhs
    Column("trades",        Integer),
    Column("delivery_qty",  BigInteger),
    Column("delivery_pct",  Numeric(6, 2)),
    # Split/bonus-adjusted close. Seeded to `close` on insert; the corporate
    # actions recompute job (pipelines/corporate_actions_pipeline.py) is the only
    # writer after that — a re-ingested day's ON CONFLICT UPDATE must never
    # touch this column, or a replay would clobber an already-adjusted value
    # back to raw.
    Column("adj_close",     Numeric(12, 4)),
    Index("idx_prices_daily_date", "trade_date"),
)

mf_nav_daily = Table(
    "mf_nav_daily",
    metadata,
    Column("scheme_code", String(12), primary_key=True),
    Column("nav_date",    Date,       primary_key=True),
    Column("nav",         Numeric(14, 4), nullable=False),
    Column("scheme_name", String(250)),
)

corporate_actions = Table(
    "corporate_actions",
    metadata,
    Column("id",           Integer, primary_key=True, autoincrement=True),
    Column("symbol",       String(20), nullable=False),
    Column("ex_date",      Date, nullable=False),
    # split | bonus | dividend | rights | buyback | other — see
    # tools/corporate_actions.py::parse_purpose for the classifier.
    Column("type",         String(10), nullable=False),
    Column("purpose_raw",  String(300), nullable=False),
    # Multiplier applied to prices strictly before ex_date; NULL for
    # non-adjusting types (dividend/rights/buyback/other) — never guessed.
    Column("price_factor", Numeric(12, 6)),
    # Dividend Rs/share; NULL for every other type.
    Column("amount",       Numeric(12, 4)),
    Column("record_date",  Date),
    # One symbol can have a dividend and a split on the same ex-date, but
    # NSE occasionally revises the same action's purpose text, which would
    # otherwise insert a near-duplicate row under this same key.
    UniqueConstraint("symbol", "ex_date", "purpose_raw", name="uq_corp_actions_sym_ex_purpose"),
    Index("idx_corp_actions_symbol", "symbol"),
)

# ── Portfolio Aggregator (personal net-worth tracker, unrelated to the
# `positions` table above — see CLAUDE.md's "Portfolio aggregator" section
# for the distinction between this and the existing /portfolio page) ────────
# No auth/ownership column by design (localhost/Tailscale-only personal
# tool, tens of users at most) — `profiles` is a bare picker, not an
# account system; keeps the door open for real auth later without forcing
# it now.

profiles = Table(
    "profiles",
    metadata,
    Column("id",         Integer, primary_key=True, autoincrement=True),
    Column("name",       String(60), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")),
)

accounts = Table(
    "accounts",
    metadata,
    Column("id",          Integer, primary_key=True, autoincrement=True),
    Column("profile_id",  Integer, ForeignKey("profiles.id"), nullable=False),
    Column("name",        String(120), nullable=False),
    Column("institution", String(120)),
    # bank | broker | amc | epfo | other
    Column("type",        String(10), nullable=False),
    Column("created_at",  DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")),
    Index("idx_accounts_profile", "profile_id"),
)

assets = Table(
    "assets",
    metadata,
    Column("id",         Integer, primary_key=True, autoincrement=True),
    Column("account_id", Integer, ForeignKey("accounts.id"), nullable=False),
    # mf | stock | fd | epf | ppf | cash | manual | loan
    Column("type",       String(10), nullable=False),
    Column("name",       String(200), nullable=False),
    # Only meaningful for mf/stock — the tradeable symbol, e.g. for a future
    # link to this app's own analysis page or the EOD price store.
    Column("symbol",     String(20)),
    # Per-type free-form data (e.g. an FD's rate/maturity date) — JSON, not
    # JSONB: this codebase's tests run these tables against SQLite
    # (`create_engine("sqlite://")`, house rule — no live DB in tests), and
    # JSONB has no SQLite equivalent, whereas SQLAlchemy's generic JSON type
    # works identically on both backends. No JSONB-specific operators (containment,
    # path queries) are used anywhere against this column, so nothing is lost.
    Column("meta",       JSON, nullable=False, server_default="{}"),
    Column("archived",   Boolean, nullable=False, server_default=text("false")),
    Column("created_at", DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")),
    Index("idx_assets_account", "account_id"),
)

holdings = Table(
    "holdings",
    metadata,
    Column("id",       Integer, primary_key=True, autoincrement=True),
    # One-to-one with assets, but modeled as its own table (not columns on
    # assets) since it's only ever populated for mf/stock assets — every
    # other asset type has neither field, and this avoids a NULL-heavy
    # assets row for the common case (fd/cash/manual/loan).
    Column("asset_id",  Integer, ForeignKey("assets.id"), nullable=False, unique=True),
    Column("units",     Numeric(18, 4), nullable=False),
    Column("avg_cost",  Numeric(14, 4)),
)

valuations = Table(
    "valuations",
    metadata,
    Column("id",       Integer, primary_key=True, autoincrement=True),
    Column("asset_id", Integer, ForeignKey("assets.id"), nullable=False),
    Column("as_of",    Date, nullable=False),
    Column("value",    Numeric(16, 2), nullable=False),
    # History from day one — one row per (asset, date); same-day re-entry
    # upserts rather than accumulating duplicate rows for the same date.
    UniqueConstraint("asset_id", "as_of", name="uq_valuations_asset_date"),
    Index("idx_valuations_asset", "asset_id"),
)

# Written by portfolio/cas_import.py (CAS-sourced rows, meta.source="cas") and
# portfolio/csv_import.py (broker-CSV-sourced rows, meta.source="csv"); read by
# portfolio/portfolio_valuation.py's xirr_report() to compute per-asset/portfolio XIRR.
transactions = Table(
    "transactions",
    metadata,
    Column("id",       Integer, primary_key=True, autoincrement=True),
    Column("asset_id", Integer, ForeignKey("assets.id"), nullable=False),
    Column("date",     Date, nullable=False),
    Column("type",     String(10), nullable=False),
    Column("amount",   Numeric(16, 2), nullable=False),
    Column("units",    Numeric(18, 4)),
    Column("meta",     JSON, nullable=False, server_default="{}"),
    Index("idx_transactions_asset", "asset_id"),
)


# Durable JSON state that used to live as one file per record under output/ —
# market-picks daily snapshots, per-source health/quality telemetry, scraper
# error and LLM-cost counters, CAS import archives, CLI reports. Every one of
# those was "a JSON blob at a path", so they share one table keyed by
# (namespace, key) rather than six near-identical tables. output/ is now
# strictly a regenerable TTL cache; nothing durable is written there.
#
# `JSON`, not `JSONB`, and `CURRENT_TIMESTAMP`, not `NOW()` — same reason the
# Portfolio Aggregator tables above give: these are exercised against SQLite in
# tests, and nothing here needs a JSONB-specific operator.
#
# The composite primary key also serves every read: `WHERE namespace = ?
# ORDER BY key` is a prefix scan, so no separate index is needed.
app_state = Table(
    "app_state",
    metadata,
    Column("namespace",  String(40),  primary_key=True),
    Column("key",        String(200), primary_key=True),
    Column("payload",    JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False,
           server_default=text("CURRENT_TIMESTAMP")),
)


def get_engine(database_url: str | None = None):
    url = database_url or os.environ["DATABASE_URL"]
    return _create_engine(url)


def stamp_alembic_head() -> None:
    """Records "this database is already at the latest Alembic revision"
    without executing any DDL — the same one-time step CLAUDE.md's Alembic
    section documents for an *existing* deployment migrating onto Alembic
    for the first time (`alembic stamp head`).

    Both pipelines/sme_ema_pipeline.py's and pipelines/screener_pipeline.py's own --setup-db
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
