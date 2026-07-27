import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import tools.sme_tools as sme_tools


class _FakeResponse:
    def __init__(self, json_data=None, status_code=200, raise_exc=None):
        self._json_data = json_data
        self.status_code = status_code
        self.ok = status_code < 400
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc

    def json(self):
        return self._json_data


class SmeToolsNetworkTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-sme-cache-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self.nse_cache = Path(self._tmpdir) / "_nse_emerge_master.json"
        self.bse_cache = Path(self._tmpdir) / "_bse_sme_master.json"
        self._patches = [
            patch.object(sme_tools, "_NSE_EMERGE_CACHE", self.nse_cache),
            patch.object(sme_tools, "_BSE_SME_CACHE", self.bse_cache),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)


class FetchNseEmergeStocksTest(SmeToolsNetworkTestBase):
    def test_fresh_cache_returns_without_network_call(self) -> None:
        cached = [{"symbol": "ABC", "name": "ABC Ltd", "isin": None, "series": "SM", "exchange": "NSE"}]
        self.nse_cache.write_text(json.dumps(cached))
        with patch("requests.Session.get", side_effect=AssertionError("should not hit network")):
            result = sme_tools.fetch_nse_emerge_stocks()
        self.assertEqual(result, cached)

    def test_fetches_parses_and_caches_on_success(self) -> None:
        api_response = _FakeResponse(json_data={"data": [{"symbol": "abc"}, {"symbol": " def "}, {"symbol": ""}]})

        def _session_get(self_, url, **kwargs):
            if "live-analysis-emerge" in url:
                return api_response
            return _FakeResponse(json_data={})  # session-priming GET

        with patch("requests.Session.get", _session_get), \
             patch.object(sme_tools, "_NSE_EMERGE_MIN_COUNT", 1), \
             patch.object(sme_tools, "_enrich_names", side_effect=lambda stocks: stocks):
            result = sme_tools.fetch_nse_emerge_stocks(force=True)

        symbols = {s["symbol"] for s in result}
        self.assertEqual(symbols, {"ABC", "DEF"})  # empty symbol dropped, whitespace stripped+uppercased
        self.assertTrue(all(s["exchange"] == "NSE" for s in result))
        self.assertTrue(self.nse_cache.exists())

    def test_network_failure_falls_back_to_stale_cache(self) -> None:
        stale = [{"symbol": "OLD", "name": None, "isin": None, "series": "SM", "exchange": "NSE"}]
        self.nse_cache.write_text(json.dumps(stale))
        with patch("requests.Session.get", side_effect=[_FakeResponse(), ConnectionError("boom")]):
            result = sme_tools.fetch_nse_emerge_stocks(force=True)
        self.assertEqual(result, stale)

    def test_network_failure_with_no_cache_returns_empty_list(self) -> None:
        with patch("requests.Session.get", side_effect=[_FakeResponse(), ConnectionError("boom")]):
            result = sme_tools.fetch_nse_emerge_stocks(force=True)
        self.assertEqual(result, [])

    def test_truncated_response_falls_back_to_stale_cache_rather_than_caching_it(self) -> None:
        # Regression test: a nonempty-but-suspiciously-small response (a
        # partial download, a rate-limited reply) used to be treated as a
        # complete, cacheable universe — silently truncating the SME
        # screener's coverage instead of degrading like any other failure.
        stale = [{"symbol": "OLD", "name": None, "isin": None, "series": "SM", "exchange": "NSE"}]
        self.nse_cache.write_text(json.dumps(stale))
        api_response = _FakeResponse(json_data={"data": [{"symbol": "abc"}]})

        def _session_get(self_, url, **kwargs):
            if "live-analysis-emerge" in url:
                return api_response
            return _FakeResponse(json_data={})

        with patch("requests.Session.get", _session_get):
            result = sme_tools.fetch_nse_emerge_stocks(force=True)
        self.assertEqual(result, stale)

    def test_corrupt_fresh_cache_falls_through_to_a_live_fetch_instead_of_raising(self) -> None:
        # Regression test for an adversarial-review finding: fetch_nse_emerge_stocks()
        # is documented as "never raises," but the fast-path cache read had no
        # guard at all -- a truncated/corrupt cache file (e.g. from an
        # interrupted write, before _save_cache became atomic) crashed
        # straight out of the function with a JSONDecodeError instead of
        # degrading to a fresh fetch.
        self.nse_cache.write_text('[{"symbol": "ABC"')  # truncated JSON

        api_response = _FakeResponse(json_data={"data": [{"symbol": "def"}]})

        def _session_get(self_, url, **kwargs):
            if "live-analysis-emerge" in url:
                return api_response
            return _FakeResponse(json_data={})

        with patch("requests.Session.get", _session_get), \
             patch.object(sme_tools, "_NSE_EMERGE_MIN_COUNT", 1), \
             patch.object(sme_tools, "_enrich_names", side_effect=lambda stocks: stocks):
            result = sme_tools.fetch_nse_emerge_stocks()

        self.assertEqual([s["symbol"] for s in result], ["DEF"])

    def test_corrupt_stale_cache_with_failed_fetch_returns_empty_list_instead_of_raising(self) -> None:
        self.nse_cache.write_text('[{"symbol": "OLD"')  # truncated JSON
        with patch("requests.Session.get", side_effect=[_FakeResponse(), ConnectionError("boom")]):
            result = sme_tools.fetch_nse_emerge_stocks(force=True)
        self.assertEqual(result, [])

    def test_row_with_null_series_is_skipped_not_fatal_to_the_whole_fetch(self) -> None:
        # Regression test for an adversarial-review finding: `.get("series",
        # "SM")` only falls back to the default when the key is MISSING —
        # if NSE ever returns a row with "series": null (key present, value
        # None), `None.strip()` raised AttributeError, which used to
        # propagate out of the whole list-comprehension building `stocks`
        # and get caught by the outer except, discarding every remaining
        # row (potentially the whole ~500-stock universe) over one bad row.
        # fetch_bse_sme_stocks() was already hardened per-row against this
        # exact failure mode; this closes the same gap on the NSE side.
        api_response = _FakeResponse(json_data={"data": [
            {"symbol": "good1", "series": "SM"},
            {"symbol": "bad", "series": None},
            {"symbol": "good2", "series": "SM"},
        ]})

        def _session_get(self_, url, **kwargs):
            if "live-analysis-emerge" in url:
                return api_response
            return _FakeResponse(json_data={})

        with patch("requests.Session.get", _session_get), \
             patch.object(sme_tools, "_NSE_EMERGE_MIN_COUNT", 1), \
             patch.object(sme_tools, "_enrich_names", side_effect=lambda stocks: stocks):
            result = sme_tools.fetch_nse_emerge_stocks(force=True)

        symbols = {s["symbol"] for s in result}
        self.assertEqual(symbols, {"GOOD1", "BAD", "GOOD2"})
        bad = next(s for s in result if s["symbol"] == "BAD")
        self.assertEqual(bad["series"], "SM")  # None falls back to the default, doesn't crash


class FetchBseSmeStocksTest(SmeToolsNetworkTestBase):
    def _group_response(self, rows):
        return _FakeResponse(json_data=rows)

    def test_merges_both_groups_and_dedups_by_scrip_code(self) -> None:
        group_m = self._group_response([
            {"SCRIP_CD": "543210", "Scrip_Name": "ABC Ltd", "ISIN_NUMBER": "INE000A01001"},
        ])
        group_ms = self._group_response([
            {"SCRIP_CD": "543210", "Scrip_Name": "ABC Ltd (dup)", "ISIN_NUMBER": "INE000A01001"},
            {"SCRIP_CD": "543211", "Scrip_Name": "XYZ Ltd", "ISIN_NUMBER": "INE000B01002"},
        ])
        with patch("requests.get", side_effect=[group_m, group_ms]), \
             patch.object(sme_tools, "_BSE_SME_MIN_COUNT", 1):
            result = sme_tools.fetch_bse_sme_stocks(force=True)

        codes = [s["symbol"] for s in result]
        self.assertEqual(codes, ["543210", "543211"])  # second 543210 dropped as a within-BSE dup

    def test_one_group_failing_still_returns_the_other(self) -> None:
        group_m = self._group_response([{"SCRIP_CD": "1", "Scrip_Name": "A", "ISIN_NUMBER": "X"}])
        with patch("requests.get", side_effect=[group_m, ConnectionError("boom")]), \
             patch.object(sme_tools, "_BSE_SME_MIN_COUNT", 1):
            result = sme_tools.fetch_bse_sme_stocks(force=True)
        self.assertEqual(len(result), 1)

    def test_malformed_row_is_skipped_not_fatal_to_the_rest_of_the_group(self) -> None:
        # Regression test: a single row that doesn't behave like a dict
        # (e.g. a bare string) used to raise out of the per-row loop,
        # silently discarding every remaining row in that group via the
        # outer except — reported only as an indistinguishable "group fetch
        # failed" warning, with no row-index or partial-completion signal.
        group_m = self._group_response([
            {"SCRIP_CD": "1", "Scrip_Name": "Good Row", "ISIN_NUMBER": "X"},
            "not-a-dict",
            {"SCRIP_CD": "2", "Scrip_Name": "Also Good", "ISIN_NUMBER": "Y"},
        ])
        with patch("requests.get", side_effect=[group_m, self._group_response([])]), \
             patch.object(sme_tools, "_BSE_SME_MIN_COUNT", 1):
            result = sme_tools.fetch_bse_sme_stocks(force=True)
        codes = {s["symbol"] for s in result}
        self.assertEqual(codes, {"1", "2"})

    def test_truncated_response_falls_back_to_stale_cache_rather_than_caching_it(self) -> None:
        stale = [{"symbol": "999", "name": "Stale Co", "isin": None, "series": "M", "exchange": "BSE"}]
        self.bse_cache.write_text(json.dumps(stale))
        group_m = self._group_response([{"SCRIP_CD": "1", "Scrip_Name": "A", "ISIN_NUMBER": "X"}])
        with patch("requests.get", side_effect=[group_m, self._group_response([])]):
            result = sme_tools.fetch_bse_sme_stocks(force=True)
        self.assertEqual(result, stale)

    def test_total_failure_falls_back_to_stale_cache(self) -> None:
        stale = [{"symbol": "999", "name": "Stale Co", "isin": None, "series": "M", "exchange": "BSE"}]
        self.bse_cache.write_text(json.dumps(stale))
        with patch("requests.get", side_effect=ConnectionError("boom")):
            result = sme_tools.fetch_bse_sme_stocks(force=True)
        self.assertEqual(result, stale)

    def test_total_failure_with_no_cache_returns_empty_list(self) -> None:
        with patch("requests.get", side_effect=ConnectionError("boom")):
            result = sme_tools.fetch_bse_sme_stocks(force=True)
        self.assertEqual(result, [])

    def test_corrupt_fresh_cache_falls_through_to_a_live_fetch_instead_of_raising(self) -> None:
        # Regression test for an adversarial-review finding: fetch_bse_sme_stocks()
        # is documented as "never raises," but the fast-path cache read had no
        # guard at all -- a truncated/corrupt cache file crashed straight out
        # of the function with a JSONDecodeError instead of degrading to a
        # fresh fetch.
        self.bse_cache.write_text('[{"symbol": "543210"')  # truncated JSON
        group_m = self._group_response([{"SCRIP_CD": "1", "Scrip_Name": "A", "ISIN_NUMBER": "X"}])
        with patch("requests.get", side_effect=[group_m, self._group_response([])]), \
             patch.object(sme_tools, "_BSE_SME_MIN_COUNT", 1):
            result = sme_tools.fetch_bse_sme_stocks()
        self.assertEqual([s["symbol"] for s in result], ["1"])

    def test_corrupt_stale_cache_with_failed_fetch_returns_empty_list_instead_of_raising(self) -> None:
        self.bse_cache.write_text('[{"symbol": "999"')  # truncated JSON
        with patch("requests.get", side_effect=ConnectionError("boom")):
            result = sme_tools.fetch_bse_sme_stocks(force=True)
        self.assertEqual(result, [])


class EnrichNamesTest(unittest.TestCase):
    def test_fills_in_name_from_screener_search(self) -> None:
        stocks = [{"symbol": "ABC", "name": None}]
        resp = _FakeResponse(json_data=[{"name": "ABC Industries Limited"}], status_code=200)
        with patch("requests.get", return_value=resp), patch("time.sleep"):
            result = sme_tools._enrich_names(stocks)
        self.assertEqual(result[0]["name"], "ABC Industries Limited")

    def test_leaves_name_none_when_search_has_no_results(self) -> None:
        stocks = [{"symbol": "NOSUCH", "name": None}]
        resp = _FakeResponse(json_data=[], status_code=200)
        with patch("requests.get", return_value=resp), patch("time.sleep"):
            result = sme_tools._enrich_names(stocks)
        self.assertIsNone(result[0]["name"])

    def test_leaves_name_none_on_request_exception(self) -> None:
        stocks = [{"symbol": "ABC", "name": None}]
        with patch("requests.get", side_effect=ConnectionError("boom")), patch("time.sleep"):
            result = sme_tools._enrich_names(stocks)
        self.assertIsNone(result[0]["name"])

    def test_skips_stocks_that_already_have_a_name(self) -> None:
        stocks = [{"symbol": "ABC", "name": "Already Known"}]
        with patch("requests.get", side_effect=AssertionError("should not be called")):
            result = sme_tools._enrich_names(stocks)
        self.assertEqual(result[0]["name"], "Already Known")


if __name__ == "__main__":
    unittest.main()
