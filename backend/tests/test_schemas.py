import unittest

from core import schemas


class NormalizeTest(unittest.TestCase):
    def test_passes_through_error_payload_unchanged(self) -> None:
        payload = {"error": "boom", "symbol": "TCS"}
        self.assertEqual(schemas.normalize("stock_info", payload), payload)

    def test_unknown_task_returns_data_unchanged(self) -> None:
        payload = {"foo": "bar"}
        self.assertEqual(schemas.normalize("price_history", payload), payload)

    def test_research_defaults_and_passes_through_quarterly_trend(self) -> None:
        result = schemas.normalize("research", {
            "symbol": "TCS",
            "ratios": {"P/E": "28"},
            "about": "An IT company.",
            "quarterly_trend": {"quarters": ["Mar 2024"], "revenue": [1000.0], "eps": [10.0]},
        })
        self.assertEqual(result["quarterly_trend"]["quarters"], ["Mar 2024"])

    def test_research_defaults_quarterly_trend_to_empty_dict(self) -> None:
        result = schemas.normalize("research", {"symbol": "TCS", "ratios": {}})
        self.assertEqual(result["quarterly_trend"], {})

    def test_research_passes_through_nse_fallback_ratios_when_present(self) -> None:
        result = schemas.normalize("research", {
            "symbol": "TCS",
            "ratios": {},
            "nse_fallback_ratios": {"eps": 12.5, "source": "nse_xbrl"},
        })
        self.assertEqual(result["nse_fallback_ratios"], {"eps": 12.5, "source": "nse_xbrl"})

    def test_research_omits_nse_fallback_ratios_when_absent(self) -> None:
        result = schemas.normalize("research", {"symbol": "TCS", "ratios": {"P/E": "28"}})
        self.assertNotIn("nse_fallback_ratios", result)

    def test_shareholding_passes_through_pledge_pct(self) -> None:
        result = schemas.normalize("shareholding", {
            "symbol": "TCS",
            "shareholding_pattern": {"Promoters": 52.5},
            "pledge_pct": 3.2,
        })
        self.assertEqual(result["pledge_pct"], 3.2)

    def test_shareholding_defaults_pledge_pct_to_none(self) -> None:
        result = schemas.normalize("shareholding", {"symbol": "TCS", "shareholding_pattern": {}})
        self.assertIsNone(result["pledge_pct"])

    def test_filings_normalizes_without_raising(self) -> None:
        # Regression test: CONTRACTS["filings"] used to be a bare function
        # instead of a {required, normalize} dict, so contract.get("normalize")
        # raised AttributeError on every call — meaning schema_normalize("filings", ...)
        # crashed the CLI pipeline outright and silently emptied the web report's
        # filings section every time filings data was freshly fetched (its cache
        # TTL defaults to 1h, so this fired routinely).
        result = schemas.normalize("filings", {"filings": [{"title": "Q1 Results"}]})
        self.assertEqual(result["filings"], [{"title": "Q1 Results"}])

    def test_filings_defaults_to_empty_list(self) -> None:
        result = schemas.normalize("filings", {})
        self.assertEqual(result["filings"], [])

    def test_normalizer_exception_degrades_to_raw_data(self) -> None:
        payload = {"symbol": "TCS", "shareholding_pattern": "not-a-dict-but-fine-here"}
        # Shouldn't raise even with unexpected shapes; normalize() catches and
        # falls back to the raw payload.
        result = schemas.normalize("shareholding", payload)
        self.assertIsInstance(result, dict)


class ValidateTest(unittest.TestCase):
    def test_error_payload_fails_validation(self) -> None:
        ok, err = schemas.validate("stock_info", {"error": "boom"})
        self.assertFalse(ok)
        self.assertIn("boom", err)

    def test_missing_required_field_fails(self) -> None:
        ok, err = schemas.validate("stock_info", {"symbol": "TCS"})
        self.assertFalse(ok)
        self.assertIn("current_price", err)

    def test_all_required_fields_present_passes(self) -> None:
        ok, _ = schemas.validate("stock_info", {"symbol": "TCS", "current_price": 100})
        self.assertTrue(ok)

    def test_unknown_task_always_passes(self) -> None:
        ok, _ = schemas.validate("price_history", {})
        self.assertTrue(ok)

    def test_filings_has_no_required_fields(self) -> None:
        # filings is never validated in production (only stock_info is), but
        # an empty "required" list should still mean an empty filings list is
        # a valid, non-error state rather than a validation failure.
        ok, _ = schemas.validate("filings", {"filings": []})
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
