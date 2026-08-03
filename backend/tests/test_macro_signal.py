import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from core import cache
from signals.macro import macro_signal
from state_store_harness import isolated_state_store


class MacroSignalTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-macro-signal-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._cache_patch = patch.object(cache, "CACHE_DIR", Path(self._tmpdir))
        self._cache_patch.start()
        self.addCleanup(self._cache_patch.stop)
        # source_health now persists through state_store, so point it at a
        # throwaway in-memory DB rather than a temp directory.
        self.addCleanup(isolated_state_store().close)

    def _run(self, flow: dict, macro: dict):
        with patch("signals.macro.get_fii_dii_flow", return_value=flow), \
             patch("signals.macro.get_macro_context", return_value=macro):
            return macro_signal()

    def test_no_usable_data_returns_unknown(self) -> None:
        sig = self._run({"error": "boom"}, {"error": "boom"})
        self.assertEqual(sig.value, "UNKNOWN")
        self.assertEqual(sig.score, 0)

    def test_strong_net_inflow_is_supportive(self) -> None:
        sig = self._run({"fii_net_cr": 2500.0, "dii_net_cr": 1000.0}, {})
        self.assertEqual(sig.value, "SUPPORTIVE")
        self.assertGreater(sig.score, 0)
        self.assertEqual(sig.meta["net_institutional_flow_cr"], 3500.0)

    def test_strong_net_outflow_is_headwind(self) -> None:
        sig = self._run({"fii_net_cr": -2500.0, "dii_net_cr": -1000.0}, {})
        self.assertEqual(sig.value, "HEADWIND")
        self.assertLess(sig.score, 0)

    def test_mild_flow_within_neutral_band(self) -> None:
        sig = self._run({"fii_net_cr": 200.0, "dii_net_cr": 100.0}, {})
        self.assertEqual(sig.value, "NEUTRAL")
        self.assertEqual(sig.score, 0)

    def test_missing_dii_still_scores_from_fii_alone(self) -> None:
        sig = self._run({"fii_net_cr": 4000.0}, {})
        self.assertEqual(sig.meta["net_institutional_flow_cr"], 4000.0)
        self.assertEqual(sig.value, "SUPPORTIVE")

    def test_elevated_cpi_is_a_headwind_component(self) -> None:
        sig = self._run({}, {"repo_rate_pct": 6.5, "cpi_inflation_pct": 7.2})
        self.assertLess(sig.score, 0)
        self.assertEqual(sig.meta["cpi_inflation_pct"], 7.2)
        self.assertEqual(sig.meta["repo_rate_pct"], 6.5)

    def test_low_cpi_is_a_supportive_component(self) -> None:
        sig = self._run({}, {"cpi_inflation_pct": 3.0})
        self.assertGreater(sig.score, 0)

    def test_moderate_cpi_contributes_nothing(self) -> None:
        sig = self._run({}, {"cpi_inflation_pct": 5.0})
        self.assertEqual(sig.score, 0)
        self.assertEqual(sig.value, "NEUTRAL")

    def test_flow_and_macro_components_combine(self) -> None:
        sig = self._run({"fii_net_cr": 4000.0}, {"cpi_inflation_pct": 7.5})
        # flow: 0.6 * 0.6 = 0.36 ; macro: -0.4 * 0.4 = -0.16 ; total 0.20
        self.assertAlmostEqual(sig.score, 0.2, places=2)
        self.assertEqual(sig.value, "SUPPORTIVE")

    def test_second_call_is_served_from_cache_not_refetched(self) -> None:
        with patch("signals.macro.get_fii_dii_flow", return_value={"fii_net_cr": 1000.0}) as flow_fn, \
             patch("signals.macro.get_macro_context", return_value={}) as macro_fn:
            macro_signal()
            macro_signal()
        flow_fn.assert_called_once()
        macro_fn.assert_called_once()

    def test_health_is_recorded_once_per_real_fetch_not_per_cache_hit(self) -> None:
        with patch("signals.macro.get_fii_dii_flow", return_value={"fii_net_cr": 1000.0}), \
             patch("signals.macro.get_macro_context", return_value={"repo_rate_pct": 6.5}), \
             patch("telemetry.source_health.record_and_check") as mock_record:
            macro_signal()
            macro_signal()
        calls = {c.args[0]: c.args[1] for c in mock_record.call_args_list}
        self.assertEqual(calls, {"fii_dii_flow": True, "macro_context": True})
        self.assertEqual(mock_record.call_count, 2)  # one per source, not one per macro_signal() call

    def test_stale_flow_date_is_excluded_from_scoring(self) -> None:
        # Regression test for the deep gap analysis finding: core/cache.py's own
        # freshness check only knows when the HTTP call succeeded, not
        # whether NSE's response actually carried a current-session row —
        # a stale (e.g. pre-long-weekend) flow figure must not be blended
        # into the score as if it were today's.
        stale_date = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%d-%b-%Y")
        sig = self._run({"date": stale_date, "fii_net_cr": 5000.0, "dii_net_cr": 5000.0}, {})
        self.assertEqual(sig.value, "UNKNOWN")
        self.assertEqual(sig.score, 0)
        self.assertIn("flow_status", sig.meta)
        self.assertEqual(sig.meta["flow_date"], stale_date)

    def test_fresh_flow_date_scores_normally(self) -> None:
        fresh_date = datetime.now(timezone.utc).strftime("%d-%b-%Y")
        sig = self._run({"date": fresh_date, "fii_net_cr": 4000.0}, {})
        self.assertEqual(sig.value, "SUPPORTIVE")
        self.assertNotIn("flow_status", sig.meta)

    def test_stale_flow_still_lets_macro_context_score_on_its_own(self) -> None:
        stale_date = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%d-%b-%Y")
        sig = self._run(
            {"date": stale_date, "fii_net_cr": 5000.0},
            {"cpi_inflation_pct": 7.5},
        )
        self.assertLess(sig.score, 0)
        self.assertIn("flow_status", sig.meta)
        self.assertNotIn("net_institutional_flow_cr", sig.meta)

    def test_unparseable_flow_date_does_not_trigger_staleness_check(self) -> None:
        # Never guessed further than the known NSE date formats — an
        # unrecognized string fails open (uses the data as before) rather
        # than being treated as "definitely stale".
        sig = self._run({"date": "not-a-real-date", "fii_net_cr": 4000.0}, {})
        self.assertEqual(sig.value, "SUPPORTIVE")
        self.assertNotIn("flow_status", sig.meta)

    def test_health_reports_not_ok_when_fetch_returns_no_usable_fields(self) -> None:
        with patch("signals.macro.get_fii_dii_flow", return_value={"error": "boom"}), \
             patch("signals.macro.get_macro_context", return_value={"error": "boom"}), \
             patch("telemetry.source_health.record_and_check") as mock_record:
            macro_signal()
        calls = {c.args[0]: c.args[1] for c in mock_record.call_args_list}
        self.assertEqual(calls, {"fii_dii_flow": False, "macro_context": False})


if __name__ == "__main__":
    unittest.main()
