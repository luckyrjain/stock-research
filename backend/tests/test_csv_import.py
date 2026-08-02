import io
import unittest
from datetime import date
from unittest.mock import patch

from sqlalchemy import create_engine, insert
from sqlalchemy.pool import StaticPool

from portfolio.csv_import import (
    _normalize_rows,
    _parse_date,
    _parse_num,
    import_rows,
    parse_broker_file,
    suggest_mapping,
)
from db.models import accounts, assets, holdings, metadata, profiles, transactions


def _sqlite_engine():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    metadata.create_all(engine, tables=[profiles, accounts, assets, holdings, transactions])
    return engine


def _mk_account(engine) -> int:
    with engine.begin() as conn:
        pid = conn.execute(insert(profiles).values(name="p")).inserted_primary_key[0]
        return conn.execute(insert(accounts).values(profile_id=pid, name="a", type="broker")).inserted_primary_key[0]


_ZERODHA_HEADERS = ["symbol", "isin", "trade_date", "trade_type", "quantity", "price"]
_ZERODHA_ROW = ["TCS", "INE467B01029", "2024-01-15", "buy", "10", "3500"]


class ParseDateTest(unittest.TestCase):
    def test_parses_all_accepted_formats(self) -> None:
        self.assertEqual(_parse_date("2024-01-15"), date(2024, 1, 15))
        self.assertEqual(_parse_date("15-01-2024"), date(2024, 1, 15))
        self.assertEqual(_parse_date("15/01/2024"), date(2024, 1, 15))
        self.assertEqual(_parse_date("15-Jan-2024"), date(2024, 1, 15))
        self.assertEqual(_parse_date("15 Jan 2024"), date(2024, 1, 15))

    def test_unparseable_returns_none(self) -> None:
        self.assertIsNone(_parse_date("not a date"))


class ParseNumTest(unittest.TestCase):
    def test_strips_commas_and_currency_symbols(self) -> None:
        self.assertEqual(_parse_num("1,234.50"), 1234.50)
        self.assertEqual(_parse_num("₹500"), 500.0)
        self.assertEqual(_parse_num("Rs.100"), 100.0)

    def test_empty_and_bad_values_return_none(self) -> None:
        self.assertIsNone(_parse_num(""))
        self.assertIsNone(_parse_num("abc"))


class SuggestMappingTest(unittest.TestCase):
    def test_detects_zerodha_tradebook(self) -> None:
        result = suggest_mapping(_ZERODHA_HEADERS)
        self.assertEqual(result["detected"], "zerodha")
        self.assertEqual(result["mapping"]["date"], "trade_date")
        self.assertEqual(result["mapping"]["side"], "trade_type")

    def test_fuzzy_fallback_on_generic_headers(self) -> None:
        result = suggest_mapping(["Date", "Stock", "Buy/Sell", "Qty", "Rate", "Net Amount"])
        self.assertIsNone(result["detected"])
        self.assertEqual(result["mapping"]["date"], "Date")
        self.assertEqual(result["mapping"]["symbol"], "Stock")
        self.assertEqual(result["mapping"]["side"], "Buy/Sell")
        self.assertEqual(result["mapping"]["quantity"], "Qty")
        self.assertEqual(result["mapping"]["price"], "Rate")
        self.assertEqual(result["mapping"]["amount"], "Net Amount")

    def test_unmappable_headers_yield_nulls(self) -> None:
        result = suggest_mapping(["foo", "bar", "baz"])
        self.assertIsNone(result["mapping"]["date"])
        self.assertIsNone(result["mapping"]["symbol"])


class NormalizeRowsTest(unittest.TestCase):
    _MAPPING = {"date": "date", "symbol": "symbol", "side": "side",
                "quantity": "qty", "price": "price", "amount": None, "isin": None}
    _HEADERS = ["date", "symbol", "side", "qty", "price"]

    def test_accepts_buy_sell_aliases(self) -> None:
        rows = [["2024-01-01", "TCS", "bought", "10", "100"],
                ["2024-01-02", "TCS", "sold", "5", "110"]]
        txns, skipped, warnings = _normalize_rows(rows, self._HEADERS, self._MAPPING)
        self.assertEqual(skipped, 0)
        self.assertEqual([t["type"] for t in txns], ["buy", "sell"])

    def test_derives_amount_from_qty_times_price_when_unmapped(self) -> None:
        rows = [["2024-01-01", "TCS", "buy", "10", "100"]]
        txns, _, _ = _normalize_rows(rows, self._HEADERS, self._MAPPING)
        self.assertEqual(txns[0]["amount"], 1000.0)

    def test_skips_bad_date_side_and_quantity_with_warnings(self) -> None:
        rows = [
            ["not-a-date", "TCS", "buy", "10", "100"],
            ["2024-01-01", "TCS", "unknown", "10", "100"],
            ["2024-01-01", "TCS", "buy", "", "100"],
        ]
        txns, skipped, warnings = _normalize_rows(rows, self._HEADERS, self._MAPPING)
        self.assertEqual(skipped, 3)
        self.assertEqual(len(warnings), 3)
        self.assertIn("unparseable date", warnings[0])
        self.assertIn("unrecognized side", warnings[1])
        self.assertIn("bad quantity", warnings[2])

    def test_skips_row_with_neither_price_nor_amount_instead_of_fabricating_zero(self) -> None:
        # Regression test: a row with no parseable price AND no amount
        # column mapped previously fell through to
        # `amount = units * (price or 0.0)`, silently importing a real-
        # quantity, fabricated-₹0-cost-basis transaction instead of being
        # skipped like every other unparseable field here.
        rows = [["2024-01-01", "TCS", "buy", "10", ""]]
        txns, skipped, warnings = _normalize_rows(rows, self._HEADERS, self._MAPPING)
        self.assertEqual(txns, [])
        self.assertEqual(skipped, 1)
        self.assertIn("missing price/amount", warnings[0])

    def test_zero_price_is_not_treated_as_missing(self) -> None:
        # A genuine price of 0 (e.g. a bonus-share credit some brokers log
        # as a buy at price 0) must not be skipped by the missing-price
        # check above — 0.0 is a real, parseable value, not None.
        rows = [["2024-01-01", "TCS", "buy", "10", "0"]]
        txns, skipped, _ = _normalize_rows(rows, self._HEADERS, self._MAPPING)
        self.assertEqual(skipped, 0)
        self.assertEqual(txns[0]["amount"], 0.0)


class ParseBrokerFileTest(unittest.TestCase):
    def test_parses_csv_with_comma_delimiter(self) -> None:
        content = "symbol,side,qty\nTCS,buy,10\n".encode()
        result = parse_broker_file(content, "trades.csv")
        self.assertEqual(result["headers"], ["symbol", "side", "qty"])
        self.assertEqual(result["rows"], [["TCS", "buy", "10"]])

    def test_parses_csv_with_semicolon_delimiter(self) -> None:
        content = "symbol;side;qty\nTCS;buy;10\n".encode()
        result = parse_broker_file(content, "trades.csv")
        self.assertEqual(result["headers"], ["symbol", "side", "qty"])

    def test_parses_xlsx(self) -> None:
        import pandas as pd
        buf = io.BytesIO()
        pd.DataFrame([{"symbol": "TCS", "side": "buy", "qty": "10"}]).to_excel(buf, index=False)
        result = parse_broker_file(buf.getvalue(), "trades.xlsx")
        self.assertEqual(result["headers"], ["symbol", "side", "qty"])
        self.assertEqual(result["rows"], [["TCS", "buy", "10"]])

    def test_empty_file_returns_error(self) -> None:
        result = parse_broker_file(b"", "trades.csv")
        self.assertIn("error", result)


class ImportRowsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _sqlite_engine()
        self.account_id = _mk_account(self.engine)
        self._resolve_patch = patch("portfolio.csv_import.resolve_symbol", return_value={
            "symbol": None, "exchange": None, "confidence": "unresolved", "candidate_name": None,
        })
        self.mock_resolve = self._resolve_patch.start()
        self.addCleanup(self._resolve_patch.stop)
        self._master_patch = patch("portfolio.csv_import.get_full_securities_master", return_value=[])
        self._master_patch.start()
        self.addCleanup(self._master_patch.stop)

    def test_unknown_account_returns_error(self) -> None:
        result = import_rows(self.engine, [_ZERODHA_ROW], _ZERODHA_HEADERS,
                             suggest_mapping(_ZERODHA_HEADERS)["mapping"], 9999, "zerodha")
        self.assertEqual(result, {"error": "account not found"})

    def test_creates_new_asset_and_transaction(self) -> None:
        result = import_rows(self.engine, [_ZERODHA_ROW], _ZERODHA_HEADERS,
                             suggest_mapping(_ZERODHA_HEADERS)["mapping"], self.account_id, "zerodha")
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["assets_created"], 1)
        self.assertTrue(any("unverified" in w for w in result["warnings"]))

    def test_resolved_isin_confidence_substitutes_symbol(self) -> None:
        self.mock_resolve.return_value = {
            "symbol": "TCS", "exchange": "NSE", "confidence": "isin", "candidate_name": "Tata Consultancy",
        }
        result = import_rows(self.engine, [["TATACONS", "INE467B01029", "2024-01-15", "buy", "10", "3500"]],
                             _ZERODHA_HEADERS, suggest_mapping(_ZERODHA_HEADERS)["mapping"],
                             self.account_id, "zerodha")
        self.assertEqual(result["assets_created"], 1)
        with self.engine.connect() as conn:
            from sqlalchemy import select
            row = conn.execute(select(assets.c.symbol, assets.c.meta)).mappings().first()
        self.assertEqual(row["symbol"], "TCS")
        self.assertEqual(row["meta"]["resolved_exchange"], "NSE")

    def test_reimport_dedupes_identical_rows(self) -> None:
        mapping = suggest_mapping(_ZERODHA_HEADERS)["mapping"]
        import_rows(self.engine, [_ZERODHA_ROW], _ZERODHA_HEADERS, mapping, self.account_id, "zerodha")
        result = import_rows(self.engine, [_ZERODHA_ROW], _ZERODHA_HEADERS, mapping, self.account_id, "zerodha")
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["duplicates"], 1)

    def test_units_derived_as_buys_minus_sells(self) -> None:
        mapping = suggest_mapping(_ZERODHA_HEADERS)["mapping"]
        rows = [
            ["TCS", "INE467B01029", "2024-01-01", "buy", "10", "100"],
            ["TCS", "INE467B01029", "2024-01-02", "sell", "3", "110"],
        ]
        import_rows(self.engine, rows, _ZERODHA_HEADERS, mapping, self.account_id, "zerodha")
        with self.engine.connect() as conn:
            from sqlalchemy import select
            from db.models import holdings
            units = conn.execute(select(holdings.c.units)).scalar()
        self.assertEqual(float(units), 7.0)

    def test_negative_derived_units_floored_to_zero_with_warning(self) -> None:
        mapping = suggest_mapping(_ZERODHA_HEADERS)["mapping"]
        rows = [["TCS", "INE467B01029", "2024-01-01", "sell", "10", "100"]]
        result = import_rows(self.engine, rows, _ZERODHA_HEADERS, mapping, self.account_id, "zerodha")
        with self.engine.connect() as conn:
            from sqlalchemy import select
            from db.models import holdings
            units = conn.execute(select(holdings.c.units)).scalar()
        self.assertEqual(float(units), 0.0)
        self.assertTrue(any("floored to 0" in w for w in result["warnings"]))

    def test_manual_and_other_broker_transactions_untouched_on_reimport(self) -> None:
        mapping = suggest_mapping(_ZERODHA_HEADERS)["mapping"]
        import_rows(self.engine, [_ZERODHA_ROW], _ZERODHA_HEADERS, mapping, self.account_id, "zerodha")
        with self.engine.begin() as conn:
            asset_id = conn.execute(assets.select()).mappings().first()["id"]
            conn.execute(insert(transactions).values(
                asset_id=asset_id, date=date(2024, 1, 1), type="buy",
                amount=1.0, units=1.0, meta={"source": "manual"},
            ))
        import_rows(self.engine, [_ZERODHA_ROW], _ZERODHA_HEADERS, mapping, self.account_id, "zerodha")
        with self.engine.connect() as conn:
            from sqlalchemy import func, select
            count = conn.execute(select(func.count()).select_from(transactions)).scalar()
        # 1 csv row (deduped on reimport, so still just 1) + 1 manual row
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
