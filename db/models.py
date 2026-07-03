import os

from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Index, Integer,
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
)

ema_signals = Table(
    "ema_signals",
    metadata,
    Column("id",             Integer, primary_key=True, autoincrement=True),
    Column("symbol",         String(20), ForeignKey("sme_stocks.symbol"), nullable=False),
    Column("trade_date",     Date,       nullable=False),
    Column("close_price",    Numeric(12, 4)),
    Column("ema20",          Numeric(12, 4)),
    Column("ema50",          Numeric(12, 4)),
    Column("crossed_ema20",  Boolean,    server_default=text("FALSE")),
    Column("crossed_ema50",  Boolean,    server_default=text("FALSE")),
    Column("cross_direction", String(10)),
    Column("run_at",         DateTime(timezone=True), server_default=text("NOW()")),
    UniqueConstraint("symbol", "trade_date", name="uq_ema_signals_symbol_date"),
    Index("idx_ema_signals_date",  "trade_date"),
    Index("idx_ema_signals_cross", "crossed_ema20", "crossed_ema50"),
)


def get_engine(database_url: str | None = None):
    url = database_url or os.environ["DATABASE_URL"]
    return _create_engine(url)
