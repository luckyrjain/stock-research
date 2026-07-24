import unittest

from main import _build_report, _strip_meta


class StripMetaTest(unittest.TestCase):
    def test_strips_meta_key(self) -> None:
        self.assertEqual(_strip_meta({"a": 1, "_meta": {"fetched_at": "x"}}), {"a": 1})

    def test_strips_any_underscore_prefixed_key(self) -> None:
        # _degraded marks a safe-fallback analysis payload (see crew._safe_analysis_fallback)
        # and must never leak into the report the frontend receives.
        self.assertEqual(_strip_meta({"a": 1, "_degraded": True}), {"a": 1})

    def test_leaves_non_underscore_keys_untouched(self) -> None:
        data = {"symbol": "TCS", "recommendation": "HOLD"}
        self.assertEqual(_strip_meta(data), data)


class BuildReportTest(unittest.TestCase):
    def test_degraded_marker_does_not_reach_the_report(self) -> None:
        analysis = {"symbol": "TCS", "recommendation": "HOLD", "_degraded": True}
        report = _build_report("TCS", {}, analysis, {})
        self.assertNotIn("_degraded", report["analysis"])
        self.assertEqual(report["analysis"]["recommendation"], "HOLD")

    def test_filings_flow_through_to_the_report(self) -> None:
        # filings is fetched and feeds signals.filings_signal, but used to be
        # dropped when the report dict was assembled — never reaching the
        # frontend despite being fully fetched and scored.
        all_data = {
            "filings": {
                "filings": [
                    {"title": "Board Meeting Intimation", "desc": "", "date": "2026-07-20",
                     "category": "Board Meeting", "attachment": "https://nse.example/x.pdf"},
                ],
                "_meta": {"fetched_at": "2026-07-20T00:00:00"},
            },
        }
        report = _build_report("TCS", all_data, {}, {})
        self.assertEqual(len(report["filings"]), 1)
        self.assertEqual(report["filings"][0]["title"], "Board Meeting Intimation")

    def test_missing_filings_task_defaults_to_empty_list(self) -> None:
        report = _build_report("TCS", {}, {}, {})
        self.assertEqual(report["filings"], [])


if __name__ == "__main__":
    unittest.main()
