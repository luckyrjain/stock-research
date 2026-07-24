import os

from sqlalchemy import (
    Column, Date, DateTime, ForeignKey, Index, Integer,
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
    Column("cross_type",  String(10)),   # 'golden' | 'death' | NULL ('cross' is reserved in SQL)
    Column("run_at",      DateTime(timezone=True), server_default=text("NOW()")),
    UniqueConstraint("symbol", "trade_date", name="uq_ema_signals_symbol_date"),
    Index("idx_ema_signals_date",  "trade_date"),
    Index("idx_ema_signals_cross", "cross_type"),
)

# Cross-mode watchlist (stock analysis / market picks / SME signals share this
# one table). There is no account system yet — client_id is a UUID the
# frontend generates on first use and keeps in localStorage (see
# frontend/lib/watchlist.ts). This is not real multi-device sync (a cleared
# browser loses its client_id and, with it, access to its rows), but it does
# mean the data itself lives in Postgres rather than only in one browser's
# localStorage, and a future account system can adopt a client_id's rows
# wholesale once real auth exists.
watchlist_items = Table(
    "watchlist_items",
    metadata,
    Column("id",         Integer, primary_key=True, autoincrement=True),
    Column("client_id",  String(36),  nullable=False),
    Column("symbol",     String(20),  nullable=False),
    Column("company",    String(200)),
    Column("exchange",   String(5)),
    Column("added_at",   DateTime(timezone=True), server_default=text("NOW()")),
    UniqueConstraint("client_id", "symbol", name="uq_watchlist_client_symbol"),
    Index("idx_watchlist_client", "client_id"),
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


def get_engine(database_url: str | None = None):
    url = database_url or os.environ["DATABASE_URL"]
    return _create_engine(url)
