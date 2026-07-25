import unittest
from unittest.mock import MagicMock, patch

from tools.nse_bulk_block_deals import (
    _deal_to_article,
    _fetch_deals,
    _first,
    _fmt_qty,
    fetch_bulk_block_deals_for_symbol,
    fetch_nse_bulk_block_deals,
)


class FirstTest(unittest.TestCase):
    def test_returns_first_present_key(self) -> None:
        self.assertEqual(_first({"a": None, "b": "x"}, ("a", "b")), "x")

    def test_skips_empty_string(self) -> None:
        self.assertEqual(_first({"a": "", "b": "y"}, ("a", "b")), "y")

    def test_returns_none_when_no_key_present(self) -> None:
        self.assertIsNone(_first({}, ("a", "b")))


class FmtQtyTest(unittest.TestCase):
    def test_crores(self) -> None:
        self.assertEqual(_fmt_qty(15_000_000), "1.5 Cr")

    def test_lakhs(self) -> None:
        self.assertEqual(_fmt_qty(150_000), "1.5L")

    def test_plain_thousands(self) -> None:
        self.assertEqual(_fmt_qty(5000), "5,000")


class DealToArticleTest(unittest.TestCase):
    def _deal(self, **overrides) -> dict:
        base = {
            "symbol": "tcs", "clientName": "Big Fund", "bdQty": 60000,
            "bdPrice": 3500.5, "buySell": "BUY", "date": "01-JAN-2026",
        }
        base.update(overrides)
        return base

    def test_valid_buy_deal_produces_article(self) -> None:
        art = _deal_to_article(self._deal(), "Bulk Deal")
        self.assertIn("TCS", art["title"])
        self.assertIn("bought", art["title"])
        self.assertIn("BUY", art["summary"])
        self.assertTrue(art["published_at"].startswith("2026-01-01"))

    def test_sell_deal_uses_sold_verb(self) -> None:
        art = _deal_to_article(self._deal(buySell="SELL"), "Block Deal")
        self.assertIn("sold", art["title"])

    def test_missing_symbol_returns_none(self) -> None:
        self.assertIsNone(_deal_to_article(self._deal(symbol=""), "Bulk Deal"))

    def test_missing_client_returns_none(self) -> None:
        self.assertIsNone(_deal_to_article(self._deal(clientName=""), "Bulk Deal"))

    def test_zero_qty_returns_none(self) -> None:
        self.assertIsNone(_deal_to_article(self._deal(bdQty=0), "Bulk Deal"))

    def test_zero_price_returns_none(self) -> None:
        self.assertIsNone(_deal_to_article(self._deal(bdPrice=0), "Bulk Deal"))

    def test_unrecognized_buy_sell_returns_none(self) -> None:
        self.assertIsNone(_deal_to_article(self._deal(buySell="X"), "Bulk Deal"))

    def test_non_numeric_qty_returns_none_not_raise(self) -> None:
        self.assertIsNone(_deal_to_article(self._deal(bdQty="not-a-number"), "Bulk Deal"))

    def test_unparseable_date_leaves_published_at_none(self) -> None:
        art = _deal_to_article(self._deal(date="not-a-date"), "Bulk Deal")
        self.assertIsNone(art["published_at"])

    def test_alias_field_names_are_supported(self) -> None:
        deal = {
            "BD_SYMBOL": "infy", "BD_CLIENT_NAME": "Some Fund",
            "BD_QTY_TRD": "75000", "BD_TP_WATP": "1500", "BD_BUY_SELL": "S",
            "BD_DT_DATE": "2026-01-15",
        }
        art = _deal_to_article(deal, "Bulk Deal")
        self.assertIn("INFY", art["title"])
        self.assertIn("sold", art["title"])


class FetchDealsTest(unittest.TestCase):
    def _session(self, json_data, raise_exc=None):
        sess = MagicMock()
        resp = MagicMock()
        resp.json.return_value = json_data
        if raise_exc:
            resp.raise_for_status.side_effect = raise_exc
        sess.get.return_value = resp
        return sess

    def test_filters_below_min_qty(self) -> None:
        sess = self._session({"data": [
            {"symbol": "A", "clientName": "X", "bdQty": 1000, "bdPrice": 10, "buySell": "BUY", "date": "01-JAN-2026"},
        ]})
        result = _fetch_deals(sess, "bulk-deals", min_qty=50_000, deal_type="Bulk Deal")
        self.assertEqual(result, [])

    def test_deduplicates_identical_titles(self) -> None:
        deal = {"symbol": "A", "clientName": "X", "bdQty": 60000, "bdPrice": 10, "buySell": "BUY", "date": "01-JAN-2026"}
        sess = self._session({"data": [deal, dict(deal)]})
        result = _fetch_deals(sess, "bulk-deals", min_qty=50_000, deal_type="Bulk Deal")
        self.assertEqual(len(result), 1)

    def test_malformed_row_is_skipped_not_fatal(self) -> None:
        sess = self._session({"data": [
            {"symbol": "A", "clientName": "X", "bdQty": "garbage", "bdPrice": 10, "buySell": "BUY", "date": "x"},
            {"symbol": "B", "clientName": "Y", "bdQty": 60000, "bdPrice": 10, "buySell": "BUY", "date": "01-JAN-2026"},
        ]})
        result = _fetch_deals(sess, "bulk-deals", min_qty=50_000, deal_type="Bulk Deal")
        self.assertEqual(len(result), 1)
        self.assertIn("B", result[0]["title"])

    def test_null_data_field_returns_empty_list_not_crash(self) -> None:
        sess = self._session({"data": None})
        result = _fetch_deals(sess, "bulk-deals", min_qty=50_000, deal_type="Bulk Deal")
        self.assertEqual(result, [])

    def test_network_exception_returns_empty_list(self) -> None:
        sess = MagicMock()
        sess.get.side_effect = ConnectionError("boom")
        result = _fetch_deals(sess, "bulk-deals", min_qty=50_000, deal_type="Bulk Deal")
        self.assertEqual(result, [])


class FetchNseBulkBlockDealsTest(unittest.TestCase):
    def test_combines_bulk_and_block_deals(self) -> None:
        with patch("tools.nse_bulk_block_deals._nse_session", return_value=MagicMock()), \
             patch("tools.nse_bulk_block_deals._fetch_deals", side_effect=[
                 [{"title": "bulk one"}], [{"title": "block one"}],
             ]):
            result = fetch_nse_bulk_block_deals()
        self.assertEqual(result["source"], "NSE Bulk/Block Deals")
        self.assertEqual(len(result["articles"]), 2)


class FetchBulkBlockDealsForSymbolTest(unittest.TestCase):
    def _deal(self, **overrides) -> dict:
        base = {
            "symbol": "TCS", "clientName": "Big Fund", "bdQty": 60000,
            "bdPrice": 3500.5, "buySell": "BUY", "date": "01-Jan-2026",
        }
        base.update(overrides)
        return base

    def _session_pair(self, bulk_rows: list[dict], block_rows: list[dict]) -> MagicMock:
        sess = MagicMock()
        bulk_resp, block_resp = MagicMock(), MagicMock()
        bulk_resp.json.return_value = {"data": bulk_rows}
        block_resp.json.return_value = {"data": block_rows}
        sess.get.side_effect = [bulk_resp, block_resp]
        return sess

    def test_filters_to_requested_symbol_across_both_endpoints(self) -> None:
        sess = self._session_pair(
            [self._deal(symbol="TCS"), self._deal(symbol="INFY")],
            [self._deal(symbol="OTHER", bdQty=200_000)],
        )
        with patch("tools.nse_bulk_block_deals._nse_session", return_value=sess):
            result = fetch_bulk_block_deals_for_symbol("tcs")
        self.assertEqual(result["symbol"], "TCS")
        self.assertEqual(len(result["deals"]), 1)
        self.assertEqual(result["deals"][0]["deal_type"], "Bulk Deal")

    def test_structured_not_article_shaped(self) -> None:
        sess = self._session_pair([self._deal()], [])
        with patch("tools.nse_bulk_block_deals._nse_session", return_value=sess):
            result = fetch_bulk_block_deals_for_symbol("TCS")
        deal = result["deals"][0]
        self.assertEqual(deal["action"], "BUY")
        self.assertEqual(deal["quantity"], 60000)
        self.assertNotIn("title", deal)
        self.assertNotIn("symbol", deal)

    def test_below_min_qty_is_excluded_same_as_market_wide(self) -> None:
        sess = self._session_pair([self._deal(bdQty=1000)], [])
        with patch("tools.nse_bulk_block_deals._nse_session", return_value=sess):
            result = fetch_bulk_block_deals_for_symbol("TCS")
        self.assertEqual(result["deals"], [])

    def test_block_deal_uses_its_own_higher_threshold(self) -> None:
        # 60,000 clears the bulk-deal floor but not the block-deal one.
        sess = self._session_pair([], [self._deal(bdQty=60000)])
        with patch("tools.nse_bulk_block_deals._nse_session", return_value=sess):
            result = fetch_bulk_block_deals_for_symbol("TCS")
        self.assertEqual(result["deals"], [])

    def test_sorted_newest_first_by_parsed_date_not_raw_string(self) -> None:
        sess = self._session_pair(
            [self._deal(date="01-Jan-2026", clientName="Earlier"),
             self._deal(date="24-Jul-2026", clientName="Later")],
            [],
        )
        with patch("tools.nse_bulk_block_deals._nse_session", return_value=sess):
            result = fetch_bulk_block_deals_for_symbol("TCS")
        self.assertEqual(result["deals"][0]["client"], "Later")
        self.assertEqual(result["deals"][1]["client"], "Earlier")

    def test_unparseable_date_sorts_without_crashing(self) -> None:
        sess = self._session_pair(
            [self._deal(date="garbage-date", clientName="Unparseable"),
             self._deal(date="24-Jul-2026", clientName="Parseable")],
            [],
        )
        with patch("tools.nse_bulk_block_deals._nse_session", return_value=sess):
            result = fetch_bulk_block_deals_for_symbol("TCS")
        self.assertEqual(len(result["deals"]), 2)
        self.assertEqual(result["deals"][0]["client"], "Parseable")
        self.assertEqual(result["deals"][1]["client"], "Unparseable")
        self.assertIsNone(result["deals"][1]["date_iso"])

    def test_no_rows_returns_empty_deals_not_error(self) -> None:
        sess = self._session_pair([], [])
        with patch("tools.nse_bulk_block_deals._nse_session", return_value=sess):
            result = fetch_bulk_block_deals_for_symbol("TCS")
        self.assertEqual(result, {"symbol": "TCS", "deals": []})

    def test_network_exception_returns_empty_deals(self) -> None:
        sess = MagicMock()
        sess.get.side_effect = ConnectionError("boom")
        with patch("tools.nse_bulk_block_deals._nse_session", return_value=sess):
            result = fetch_bulk_block_deals_for_symbol("TCS")
        self.assertEqual(result, {"symbol": "TCS", "deals": []})

    def test_non_string_symbol_does_not_raise(self) -> None:
        # Regression test: symbol.upper() used to be called unguarded —
        # any non-string caller input raised AttributeError instead of
        # degrading to an empty result.
        sess = self._session_pair([], [])
        with patch("tools.nse_bulk_block_deals._nse_session", return_value=sess):
            result = fetch_bulk_block_deals_for_symbol(None)
        self.assertEqual(result, {"symbol": "", "deals": []})


if __name__ == "__main__":
    unittest.main()
