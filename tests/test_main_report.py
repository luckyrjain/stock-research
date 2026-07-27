import unittest
from unittest.mock import MagicMock, patch

from main import _build_report, _fetch_task, _strip_meta


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

    def test_degraded_marker_is_promoted_to_a_top_level_report_field(self) -> None:
        # _degraded is stripped from `analysis` (see the test above) but must
        # still reach the frontend somehow, or a provider outage's safe
        # fallback is indistinguishable from a real HOLD verdict — see
        # crew.py::_safe_analysis_fallback and types/index.ts's `degraded`.
        analysis = {"symbol": "TCS", "recommendation": "HOLD", "_degraded": True}
        report = _build_report("TCS", {}, analysis, {})
        self.assertTrue(report["degraded"])

    def test_degraded_defaults_to_false_for_a_real_analysis(self) -> None:
        analysis = {"symbol": "TCS", "recommendation": "BUY"}
        report = _build_report("TCS", {}, analysis, {})
        self.assertFalse(report["degraded"])

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

    def test_filings_summary_is_classified_from_the_same_filings_list(self) -> None:
        all_data = {
            "filings": {
                "filings": [
                    {"title": "Dividend declared", "desc": "", "date": "01-Jan-2026", "category": "", "attachment": None},
                ],
            },
        }
        report = _build_report("TCS", all_data, {}, {})
        self.assertEqual(len(report["filings_summary"]["corporate_actions"]), 1)
        self.assertEqual(report["filings_summary"]["corporate_actions"][0]["type"], "dividend")

    def test_missing_filings_task_still_returns_empty_filings_summary_shape(self) -> None:
        report = _build_report("TCS", {}, {}, {})
        self.assertEqual(
            report["filings_summary"],
            {"corporate_actions": [], "rating_action": None, "next_results_date": None},
        )

    def test_data_freshness_captures_each_tasks_own_fetched_at(self) -> None:
        # report.generated_at is stamped fresh on every _build_report() call
        # regardless of whether any underlying data was actually refetched
        # — data_freshness is the real per-task fetch timestamp, captured
        # before _strip_meta() discards _meta. See the deep gap analysis
        # finding this closes: a 6-day-stale shareholding table (168h TTL)
        # previously still read as "Updated today" in the hero.
        all_data = {
            "stock_info":   {"price": 100, "_meta": {"fetched_at": "2026-07-27T09:00:00+00:00"}},
            "shareholding": {"promoters_pct": 50, "_meta": {"fetched_at": "2026-07-21T09:00:00+00:00"}},
        }
        report = _build_report("TCS", all_data, {}, {})
        self.assertEqual(report["data_freshness"]["stock_info"], "2026-07-27T09:00:00+00:00")
        self.assertEqual(report["data_freshness"]["shareholding"], "2026-07-21T09:00:00+00:00")

    def test_data_freshness_is_none_for_a_task_with_no_meta(self) -> None:
        # An error payload or a task that never ran has no _meta at all —
        # None (never guessed), not a fabricated "just fetched" timestamp.
        report = _build_report("TCS", {}, {}, {})
        self.assertEqual(
            report["data_freshness"],
            {"stock_info": None, "research": None, "news": None,
             "shareholding": None, "mf_holdings": None, "filings": None},
        )

    def test_mf_holdings_trend_defaults_to_empty_list(self) -> None:
        report = _build_report("TCS", {}, {}, {})
        self.assertEqual(report["mf_holdings_trend"], [])

    def test_mf_holdings_trend_passes_through_when_provided(self) -> None:
        trend = [{"fund": "HDFC MF", "holding_pct": 4.0, "delta_pct": 0.5}]
        report = _build_report("TCS", {}, {}, {}, mf_holdings_trend=trend)
        self.assertEqual(report["mf_holdings_trend"], trend)


class FetchTaskMfHoldingsSnapshotTest(unittest.TestCase):
    """Confirms main._fetch_task — the single choke point both the CLI and
    api.py's SSE endpoint already funnel every data-slice fetch through —
    saves an mf_holdings snapshot on every successful fetch of that task,
    and only that task (same wiring pattern as schema_drift's
    log_drift_if_any, see tests/test_schema_drift.py)."""

    def test_dict_payload_triggers_snapshot_save(self) -> None:
        fake_tool = MagicMock()
        fake_tool.run.return_value = {
            "symbol": "TCS", "as_of_date": "2026-03-31",
            "mutual_funds": [{"fund": "HDFC MF", "holding_pct": 3.5}],
        }
        with patch("main.get_mf_holdings", fake_tool), \
             patch("main.save_mf_holdings_snapshot") as mock_save:
            result = _fetch_task("mf_holdings", "TCS", "run-1")

        mock_save.assert_called_once_with("TCS", result)

    def test_json_text_payload_triggers_snapshot_save(self) -> None:
        fake_tool = MagicMock()
        fake_tool.run.return_value = (
            '{"symbol": "TCS", "as_of_date": "2026-03-31", '
            '"mutual_funds": [{"fund": "HDFC MF", "holding_pct": 3.5}]}'
        )
        with patch("main.get_mf_holdings", fake_tool), \
             patch("main.save_mf_holdings_snapshot") as mock_save:
            result = _fetch_task("mf_holdings", "TCS", "run-1")

        mock_save.assert_called_once_with("TCS", result)

    def test_other_tasks_never_trigger_mf_holdings_snapshot_save(self) -> None:
        fake_tool = MagicMock()
        fake_tool.run.return_value = {"symbol": "TCS", "ratios": {}}
        with patch("main.get_fundamentals", fake_tool), \
             patch("main.save_mf_holdings_snapshot") as mock_save:
            _fetch_task("research", "TCS", "run-1")

        mock_save.assert_not_called()

    def test_error_payload_still_reaches_save_snapshot_as_a_noop(self) -> None:
        # save_snapshot() itself is what skips error payloads — _fetch_task
        # doesn't special-case this, same convention as schema_drift's own
        # wiring (see test_schema_drift.py's equivalent test).
        fake_tool = MagicMock()
        fake_tool.run.return_value = {"error": "NSE unreachable", "symbol": "TCS"}
        with patch("main.get_mf_holdings", fake_tool), \
             patch("main.save_mf_holdings_snapshot") as mock_save:
            _fetch_task("mf_holdings", "TCS", "run-1")

        mock_save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
