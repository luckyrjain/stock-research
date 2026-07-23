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


if __name__ == "__main__":
    unittest.main()
