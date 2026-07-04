import unittest

from market_picks_pipeline import _SOURCE_CREDIBILITY
from tools.market_picks_tools import SCRAPER_FNS, SOURCES
from tools.nse_insider_trades import _trade_to_article


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


if __name__ == "__main__":
    unittest.main()
