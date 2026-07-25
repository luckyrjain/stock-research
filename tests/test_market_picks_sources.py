import unittest
from unittest.mock import MagicMock, patch

from market_picks_pipeline import _SOURCE_CREDIBILITY
from tools.market_picks_tools import SCRAPER_FNS, SOURCES
from tools.nse_insider_trades import _trade_to_article, fetch_insider_trades_for_symbol


def _pit_row(**overrides) -> dict:
    row = {
        "symbol":             "TESTCO",
        "acqName":            "Ramesh Kumar",
        "personCategory":     "Promoters",
        "tdpTransactionType": "Buy",
        "acqMode":            "Market Purchase",
        "intimDt":            "01-Jul-2026",
        "secAcq":             "100000",
        "secVal":             "50000000",
    }
    row.update(overrides)
    return row


class SourceRegistryTest(unittest.TestCase):
    def test_every_source_has_a_scraper_fn(self) -> None:
        for name, _type, _fn in SOURCES:
            self.assertIn(name, SCRAPER_FNS, f"Source '{name}' missing from SCRAPER_FNS")

    def test_every_source_has_a_credibility_weight(self) -> None:
        for name, _type, _fn in SOURCES:
            self.assertIn(
                name, _SOURCE_CREDIBILITY,
                f"Source '{name}' missing from _SOURCE_CREDIBILITY (would default to 0.50)",
            )

    def test_scraper_fn_names_match_registry(self) -> None:
        for name, _type, fn_name in SOURCES:
            self.assertEqual(SCRAPER_FNS[name].__name__, fn_name)


class InsiderTradeArticleTest(unittest.TestCase):
    def test_promoter_buy_formats_as_bought_article(self) -> None:
        art = _trade_to_article(_pit_row())
        self.assertIsNotNone(art)
        self.assertIn("bought", art["title"])
        self.assertIn("TESTCO", art["title"])
        self.assertIn("₹5.0 Cr", art["title"])
        self.assertIn("(BUY)", art["summary"])
        self.assertTrue(art["published_at"].startswith("2026-07-01"))

    def test_director_disposal_formats_as_sold_article(self) -> None:
        art = _trade_to_article(_pit_row(
            personCategory="Director", tdpTransactionType="Disposal", acqMode="Market Sale",
        ))
        self.assertIsNotNone(art)
        self.assertIn("sold", art["title"])
        self.assertIn("(SELL)", art["summary"])

    def test_esop_trade_is_excluded(self) -> None:
        self.assertIsNone(_trade_to_article(_pit_row(acqMode="ESOP")))

    def test_pledge_is_excluded(self) -> None:
        self.assertIsNone(_trade_to_article(_pit_row(
            acqMode="Pledge Creation", tdpTransactionType="Pledge",
        )))

    def test_non_insider_category_is_excluded(self) -> None:
        self.assertIsNone(_trade_to_article(_pit_row(personCategory="Designated Person")))

    def test_below_value_threshold_is_excluded(self) -> None:
        self.assertIsNone(_trade_to_article(_pit_row(secVal="100000")))

    def test_malformed_row_returns_none(self) -> None:
        self.assertIsNone(_trade_to_article({"symbol": "X", "secAcq": "abc"}))


class FetchInsiderTradesForSymbolTest(unittest.TestCase):
    def _session(self, rows: list[dict]) -> MagicMock:
        sess = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"data": rows}
        sess.get.return_value = resp
        return sess

    def test_filters_to_requested_symbol_only(self) -> None:
        rows = [_pit_row(symbol="TESTCO"), _pit_row(symbol="OTHERCO")]
        with patch("tools.nse_insider_trades._nse_session", return_value=self._session(rows)):
            result = fetch_insider_trades_for_symbol("testco")
        self.assertEqual(result["symbol"], "TESTCO")
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["trades"][0]["person"], "Ramesh Kumar")

    def test_structured_not_article_shaped(self) -> None:
        with patch("tools.nse_insider_trades._nse_session", return_value=self._session([_pit_row()])):
            result = fetch_insider_trades_for_symbol("TESTCO")
        trade = result["trades"][0]
        self.assertEqual(trade["action"], "BUY")
        self.assertEqual(trade["quantity"], 100000)
        self.assertNotIn("title", trade)  # not the LLM-article shape
        self.assertNotIn("symbol", trade)  # already the dict key, not repeated per-row

    def test_same_noise_filters_as_market_wide(self) -> None:
        rows = [_pit_row(acqMode="ESOP"), _pit_row(secVal="100000")]
        with patch("tools.nse_insider_trades._nse_session", return_value=self._session(rows)):
            result = fetch_insider_trades_for_symbol("TESTCO")
        self.assertEqual(result["trades"], [])

    def test_sorted_newest_first_by_parsed_date_not_raw_string(self) -> None:
        # Raw NSE date strings ("01-Jan-2026" vs "24-Jul-2026") aren't
        # lexically sortable in calendar order — must sort on the parsed ISO
        # date, not the display string.
        rows = [
            _pit_row(intimDt="01-Jan-2026", acqName="Earlier"),
            _pit_row(intimDt="24-Jul-2026", acqName="Later"),
        ]
        with patch("tools.nse_insider_trades._nse_session", return_value=self._session(rows)):
            result = fetch_insider_trades_for_symbol("TESTCO")
        self.assertEqual(result["trades"][0]["person"], "Later")
        self.assertEqual(result["trades"][1]["person"], "Earlier")

    def test_no_rows_returns_empty_trades_not_error(self) -> None:
        with patch("tools.nse_insider_trades._nse_session", return_value=self._session([])):
            result = fetch_insider_trades_for_symbol("TESTCO")
        self.assertEqual(result, {"symbol": "TESTCO", "trades": []})

    def test_network_exception_returns_empty_trades(self) -> None:
        sess = MagicMock()
        sess.get.side_effect = ConnectionError("boom")
        with patch("tools.nse_insider_trades._nse_session", return_value=sess):
            result = fetch_insider_trades_for_symbol("TESTCO")
        self.assertEqual(result, {"symbol": "TESTCO", "trades": []})


if __name__ == "__main__":
    unittest.main()
