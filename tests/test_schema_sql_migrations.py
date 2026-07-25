"""Regression coverage for db/schema.sql's migration-guard convention.

`CREATE TABLE IF NOT EXISTS` is a no-op against a database whose table
already exists — so a column added only to the CREATE TABLE body (as
sme_stocks.avg_volume_20d/avg_turnover_20d/market_cap_cr and
ema_signals.rsi14/volume_spike/cross_type all originally were, per git
history) never actually reaches a pre-existing production database. Every
such column needs its own `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` guard,
same convention `users.tier` and `watchlist_items.user_id` already follow.
This test catches the next column that's added to db/models.py without a
matching guard in db/schema.sql, instead of that surfacing later as a
"column does not exist" error against a real deployed database.
"""
import re
import unittest
from pathlib import Path

from db.models import ema_signals, sme_stocks

_SCHEMA_SQL = (Path(__file__).resolve().parent.parent / "db" / "schema.sql").read_text()

# Columns present in the very first commit that introduced each table's
# CREATE TABLE statement — these are safe without a separate ALTER TABLE
# guard, since CREATE TABLE IF NOT EXISTS itself creates them for any
# database that predates the table entirely. Every column added afterward
# needs its own guard.
_SME_STOCKS_BASELINE_COLUMNS = {"symbol", "name", "exchange", "isin", "series", "fetched_at"}
_EMA_SIGNALS_BASELINE_COLUMNS = {"id", "symbol", "trade_date", "close_price", "ema20", "ema50", "run_at"}


def _has_add_column_guard(table_name: str, column_name: str) -> bool:
    pattern = rf"ALTER TABLE\s+{table_name}\s+ADD COLUMN IF NOT EXISTS\s+{column_name}\b"
    return re.search(pattern, _SCHEMA_SQL, re.IGNORECASE) is not None


class SchemaSqlMigrationGuardTest(unittest.TestCase):
    def test_every_non_baseline_sme_stocks_column_has_a_migration_guard(self) -> None:
        missing = [
            col for col in sme_stocks.columns.keys()
            if col not in _SME_STOCKS_BASELINE_COLUMNS and not _has_add_column_guard("sme_stocks", col)
        ]
        self.assertEqual(
            missing, [],
            f"sme_stocks columns missing an ALTER TABLE ADD COLUMN IF NOT EXISTS "
            f"guard in db/schema.sql: {missing}",
        )

    def test_every_non_baseline_ema_signals_column_has_a_migration_guard(self) -> None:
        missing = [
            col for col in ema_signals.columns.keys()
            if col not in _EMA_SIGNALS_BASELINE_COLUMNS and not _has_add_column_guard("ema_signals", col)
        ]
        self.assertEqual(
            missing, [],
            f"ema_signals columns missing an ALTER TABLE ADD COLUMN IF NOT EXISTS "
            f"guard in db/schema.sql: {missing}",
        )


if __name__ == "__main__":
    unittest.main()
