import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from market_picks_pipeline import _SOURCE_CREDIBILITY
from tools.market_picks_tools import (
    SCRAPER_FNS,
    SOURCES,
    _current_year,
    _parse_rss,
    fetch_gnews_ms_jpm,
)
from tools.nse_insider_trades import _trade_to_article, fetch_insider_trades, fetch_insider_trades_for_symbol


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


class GnewsQueryYearTest(unittest.TestCase):
    def test_brokerage_query_uses_current_year_not_a_hardcoded_one(self) -> None:
        # Regression test: this query used to bake in a fixed literal year,
        # which would silently degrade recall once real articles started
        # saying the following year instead.
        year = str(_current_year())
        with patch("tools.market_picks_tools._gnews", return_value=[]) as mocked:
            fetch_gnews_ms_jpm()
        query = mocked.call_args[0][0]
        self.assertIn(year, query)

    def test_current_year_matches_utc_now(self) -> None:
        self.assertEqual(_current_year(), datetime.now(timezone.utc).year)


class ParseRssEncodingTest(unittest.TestCase):
    def test_parses_from_raw_response_bytes_not_decoded_text(self) -> None:
        # Regression test: feedparser used to be handed requests' text-
        # decoded string, whose encoding guess can silently mojibake
        # non-ASCII bytes before feedparser ever sees them. Parsing the
        # raw bytes directly avoids that double-decoding.
        rss_bytes = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b"<rss><channel><item><title>Sensex hits \xe2\x82\xb9 milestone</title>"
            b"<link>https://example.com/a</link></item></channel></rss>"
        )
        fake_resp = MagicMock()
        fake_resp.content = rss_bytes
        fake_resp.text = rss_bytes.decode("latin-1")  # a plausible wrong guess
        fake_resp.raise_for_status.return_value = None
        fake_session = MagicMock()
        fake_session.get.return_value = fake_resp
        with patch("tools.market_picks_tools._session", return_value=fake_session):
            articles = _parse_rss("https://example.com/feed")
        self.assertEqual(len(articles), 1)
        self.assertIn("₹", articles[0]["title"])


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


class FetchInsiderTradesMarketWideTest(unittest.TestCase):
    def _session(self, rows: list[dict]) -> MagicMock:
        sess = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"data": rows}
        sess.get.return_value = resp
        return sess

    def test_exact_duplicate_row_is_deduped(self) -> None:
        rows = [_pit_row(), dict(_pit_row())]
        with patch("tools.nse_insider_trades._nse_session", return_value=self._session(rows)):
            result = fetch_insider_trades()
        self.assertEqual(len(result["articles"]), 1)

    def test_distinct_disclosures_with_the_same_rounded_title_are_not_collapsed(self) -> None:
        # Regression test for an adversarial-review finding: the market-wide
        # dedup used to key on the built article's own title string, which
        # deliberately omits quantity/date and rounds value to 1 decimal
        # place in Cr/L buckets (_fmt_value) for readability. Two genuinely
        # distinct real-world disclosures -- same person/category/symbol,
        # different day, slightly different value that rounds the same way
        # (₹1.05 Cr and ₹1.08 Cr both format as "₹1.1 Cr") -- used to
        # collide on an identical title and the second was silently dropped,
        # losing a real "insider bought again" signal. Deduping on the
        # underlying parsed fields (including date and exact value) instead
        # must keep both.
        rows = [
            _pit_row(intimDt="01-Jul-2026", secVal="10500000"),  # ₹1.05 Cr
            _pit_row(intimDt="02-Jul-2026", secVal="10800000"),  # ₹1.08 Cr -- same rounded title
        ]
        with patch("tools.nse_insider_trades._nse_session", return_value=self._session(rows)):
            result = fetch_insider_trades()
        self.assertEqual(len(result["articles"]), 2)
        # Confirms the premise: both really do round to the same title text.
        titles = {a["title"] for a in result["articles"]}
        self.assertEqual(len(titles), 1)

    def test_below_value_threshold_rows_produce_no_articles(self) -> None:
        rows = [_pit_row(secVal="100000")]
        with patch("tools.nse_insider_trades._nse_session", return_value=self._session(rows)):
            result = fetch_insider_trades()
        self.assertEqual(result["articles"], [])


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

    def test_unparseable_date_sorts_without_crashing(self) -> None:
        # A row whose date NSE sent in a format _parse_pit_date() doesn't
        # recognize gets date_iso=None — the sort key must tolerate a mix of
        # None and real ISO strings without raising, and a None entry should
        # land predictably (last, since "" is the minimum under reverse=True).
        rows = [
            _pit_row(intimDt="garbage-date", acqName="Unparseable"),
            _pit_row(intimDt="24-Jul-2026", acqName="Parseable"),
        ]
        with patch("tools.nse_insider_trades._nse_session", return_value=self._session(rows)):
            result = fetch_insider_trades_for_symbol("TESTCO")
        self.assertEqual(len(result["trades"]), 2)
        self.assertEqual(result["trades"][0]["person"], "Parseable")
        self.assertEqual(result["trades"][1]["person"], "Unparseable")
        self.assertIsNone(result["trades"][1]["date_iso"])

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

    def test_non_string_symbol_does_not_raise(self) -> None:
        # Regression test: symbol.upper() used to be called unguarded —
        # any non-string caller input raised AttributeError instead of
        # degrading to an empty result.
        with patch("tools.nse_insider_trades._nse_session", return_value=self._session([])):
            result = fetch_insider_trades_for_symbol(None)
        self.assertEqual(result, {"symbol": "", "trades": []})

    def test_duplicate_row_is_deduped_like_the_market_wide_function(self) -> None:
        # Regression test: fetch_insider_trades() (market-wide) dedupes by
        # article title, but this per-symbol function used to have no dedup
        # at all — NSE's PIT feed can return the same disclosure more than
        # once (e.g. an amended/re-filed row), which used to double-count it.
        rows = [_pit_row(symbol="TESTCO"), _pit_row(symbol="TESTCO")]
        with patch("tools.nse_insider_trades._nse_session", return_value=self._session(rows)):
            result = fetch_insider_trades_for_symbol("testco")
        self.assertEqual(len(result["trades"]), 1)

    def test_comma_formatted_quantity_and_value_parse_correctly(self) -> None:
        # Regression test: nse_fii_dii_tools._to_float() strips thousands-
        # separator commas, but this row parser didn't — a comma-formatted
        # value used to raise ValueError, silently dropping an otherwise
        # valid trade instead of being parsed.
        rows = [_pit_row(symbol="TESTCO", secAcq="1,00,000", secVal="5,00,00,000")]
        with patch("tools.nse_insider_trades._nse_session", return_value=self._session(rows)):
            result = fetch_insider_trades_for_symbol("testco")
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["trades"][0]["quantity"], 100000)
        self.assertEqual(result["trades"][0]["value"], 50000000.0)


if __name__ == "__main__":
    unittest.main()
