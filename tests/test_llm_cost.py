import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import llm_cost


class LlmCostTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-llm-cost-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._patch = patch.object(llm_cost, "_COST_DIR", Path(self._tmpdir))
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_estimate_cost_usd_never_raises_on_an_unrecognized_model(self) -> None:
        fake_response = object()  # not a real litellm response shape
        cost = llm_cost.estimate_cost_usd(fake_response, "totally-made-up-model")
        self.assertIsNone(cost)

    def test_record_call_cost_accumulates_across_calls(self) -> None:
        llm_cost.record_call_cost("TCS", "claude-sonnet-4-6", "anthropic", 0.01, 100, 50)
        llm_cost.record_call_cost("INFY", "claude-sonnet-4-6", "anthropic", 0.02, 100, 50)

        totals = llm_cost.get_daily_totals()
        self.assertEqual(totals["call_count"], 2)
        self.assertAlmostEqual(totals["total_cost_usd"], 0.03)
        self.assertEqual(totals["calls_with_unknown_cost"], 0)

    def test_record_call_cost_tracks_unknown_cost_calls_separately(self) -> None:
        llm_cost.record_call_cost("TCS", "some-model", "openai", None, None, None)

        totals = llm_cost.get_daily_totals()
        self.assertEqual(totals["call_count"], 1)
        self.assertEqual(totals["total_cost_usd"], 0.0)
        self.assertEqual(totals["calls_with_unknown_cost"], 1)

    def test_get_daily_totals_for_a_day_with_no_calls_is_all_zero(self) -> None:
        totals = llm_cost.get_daily_totals("2020-01-01")
        self.assertEqual(totals, {"call_count": 0, "total_cost_usd": 0.0, "calls_with_unknown_cost": 0})

    def test_get_daily_totals_never_raises_on_a_corrupt_file(self) -> None:
        path = llm_cost._COST_DIR / f"{llm_cost._today()}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json")
        totals = llm_cost.get_daily_totals()
        self.assertEqual(totals["call_count"], 0)

    def test_record_call_cost_logs_a_structured_event(self) -> None:
        with patch("llm_cost.log_event") as mock_log:
            llm_cost.record_call_cost("TCS", "claude-sonnet-4-6", "anthropic", 0.01, 100, 50, run_id="run-1")

        mock_log.assert_called_once()
        _logger, event_name = mock_log.call_args.args[:2]
        self.assertEqual(event_name, "llm_call_cost")
        self.assertEqual(mock_log.call_args.kwargs["symbol"], "TCS")
        self.assertEqual(mock_log.call_args.kwargs["cost_usd"], 0.01)
        self.assertEqual(mock_log.call_args.kwargs["run_id"], "run-1")

    def test_a_broken_cost_dir_never_raises(self) -> None:
        blocked = Path(self._tmpdir) / "blocked"
        blocked.write_text("not a directory")
        with patch.object(llm_cost, "_COST_DIR", blocked / "nested"):
            llm_cost.record_call_cost("TCS", "claude-sonnet-4-6", "anthropic", 0.01, 100, 50)  # must not raise

    def test_save_writes_atomically_leaving_no_temp_files_behind(self) -> None:
        llm_cost.record_call_cost("TCS", "claude-sonnet-4-6", "anthropic", 0.01, 100, 50)

        leftover_temp_files = [p for p in Path(self._tmpdir).iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftover_temp_files, [])

    def test_multiple_days_are_tracked_independently(self) -> None:
        with patch("llm_cost._today", return_value="2026-01-01"):
            llm_cost.record_call_cost("TCS", "claude-sonnet-4-6", "anthropic", 0.01, 100, 50)
        with patch("llm_cost._today", return_value="2026-01-02"):
            llm_cost.record_call_cost("TCS", "claude-sonnet-4-6", "anthropic", 0.05, 100, 50)

        self.assertEqual(llm_cost.get_daily_totals("2026-01-01")["total_cost_usd"], 0.01)
        self.assertEqual(llm_cost.get_daily_totals("2026-01-02")["total_cost_usd"], 0.05)


if __name__ == "__main__":
    unittest.main()
