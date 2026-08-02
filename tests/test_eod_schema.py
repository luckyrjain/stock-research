import unittest

from db import models


class EodSchemaTest(unittest.TestCase):
    def test_all_eod_tables_registered(self) -> None:
        for name in ("securities", "prices_daily", "mf_nav_daily"):
            self.assertIn(name, models.metadata.tables, f"table '{name}' missing from metadata")

    def test_prices_daily_composite_pk(self) -> None:
        pk_cols = {c.name for c in models.prices_daily.primary_key.columns}
        self.assertEqual(pk_cols, {"symbol", "trade_date"})

    def test_mf_nav_daily_composite_pk(self) -> None:
        pk_cols = {c.name for c in models.mf_nav_daily.primary_key.columns}
        self.assertEqual(pk_cols, {"scheme_code", "nav_date"})

    def test_prices_daily_columns(self) -> None:
        cols = models.prices_daily.c
        for col in ("open", "high", "low", "close", "prev_close", "avg_price",
                    "volume", "turnover_lacs", "trades", "delivery_qty", "delivery_pct"):
            self.assertIn(col, cols)

    def test_securities_columns(self) -> None:
        cols = models.securities.c
        for col in ("symbol", "isin", "company_name", "series",
                    "listing_date", "face_value", "last_seen"):
            self.assertIn(col, cols)


class CorporateActionsSchemaTest(unittest.TestCase):
    def test_table_registered(self) -> None:
        self.assertIn("corporate_actions", models.metadata.tables)

    def test_columns(self) -> None:
        cols = models.corporate_actions.c
        for col in ("id", "symbol", "ex_date", "type", "purpose_raw",
                    "price_factor", "amount", "record_date"):
            self.assertIn(col, cols)

    def test_unique_constraint(self) -> None:
        names = {c.name for c in models.corporate_actions.constraints if c.name}
        self.assertIn("uq_corp_actions_sym_ex_purpose", names)

    def test_prices_daily_has_adj_close(self) -> None:
        self.assertIn("adj_close", models.prices_daily.c)


if __name__ == "__main__":
    unittest.main()
