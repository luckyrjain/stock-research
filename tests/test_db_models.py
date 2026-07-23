import os
import unittest
from unittest.mock import patch

from db.models import ema_signals, get_engine, sme_stocks, watchlist_items


class GetEngineTest(unittest.TestCase):
    def test_uses_explicit_database_url_over_env(self) -> None:
        with patch("db.models._create_engine") as mocked_create:
            get_engine("postgresql://explicit/db")
            mocked_create.assert_called_once_with("postgresql://explicit/db")

    def test_falls_back_to_env_var_when_not_passed(self) -> None:
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://from-env/db"}, clear=False), \
             patch("db.models._create_engine") as mocked_create:
            get_engine()
            mocked_create.assert_called_once_with("postgresql://from-env/db")

    def test_raises_when_neither_arg_nor_env_var_present(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(KeyError):
                get_engine()


class TableSchemaTest(unittest.TestCase):
    def test_sme_stocks_columns(self) -> None:
        cols = set(sme_stocks.columns.keys())
        self.assertEqual(cols, {"symbol", "name", "exchange", "isin", "series", "fetched_at"})
        self.assertTrue(sme_stocks.columns["symbol"].primary_key)
        self.assertFalse(sme_stocks.columns["exchange"].nullable)

    def test_ema_signals_columns_and_constraints(self) -> None:
        cols = set(ema_signals.columns.keys())
        self.assertEqual(
            cols,
            {"id", "symbol", "trade_date", "close_price", "ema20", "ema50", "cross_type", "run_at"},
        )
        self.assertFalse(ema_signals.columns["symbol"].nullable)
        self.assertFalse(ema_signals.columns["trade_date"].nullable)

        fk_targets = {
            f"{fk.column.table.name}.{fk.column.name}"
            for col in ema_signals.columns
            for fk in col.foreign_keys
        }
        self.assertIn("sme_stocks.symbol", fk_targets)

        unique_constraint_cols = [
            tuple(c.name for c in constraint.columns)
            for constraint in ema_signals.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        ]
        self.assertIn(("symbol", "trade_date"), unique_constraint_cols)

    def test_indexes_exist_on_trade_date_and_cross_type(self) -> None:
        index_columns = {tuple(c.name for c in idx.columns) for idx in ema_signals.indexes}
        self.assertIn(("trade_date",), index_columns)
        self.assertIn(("cross_type",), index_columns)

    def test_watchlist_items_columns_and_constraints(self) -> None:
        cols = set(watchlist_items.columns.keys())
        self.assertEqual(cols, {"id", "client_id", "symbol", "company", "exchange", "added_at"})
        self.assertFalse(watchlist_items.columns["client_id"].nullable)
        self.assertFalse(watchlist_items.columns["symbol"].nullable)

        unique_constraint_cols = [
            tuple(c.name for c in constraint.columns)
            for constraint in watchlist_items.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        ]
        self.assertIn(("client_id", "symbol"), unique_constraint_cols)

        index_columns = {tuple(c.name for c in idx.columns) for idx in watchlist_items.indexes}
        self.assertIn(("client_id",), index_columns)


if __name__ == "__main__":
    unittest.main()
