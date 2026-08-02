import json
import multiprocessing
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import llm_cost


def _mp_record_call(cost_dir: str, date: str) -> None:
    """Top-level (picklable) worker for MultiProcessConcurrencySafetyTest —
    runs in a genuinely separate OS process, not a thread, to exercise the
    actual guarantee fcntl.flock provides (keyed on the open file
    description across processes) rather than only the in-process
    serialization threads already prove. Reassigns _COST_DIR directly
    (not via unittest.mock.patch, which doesn't need to cross a process
    boundary here since the fork start method copies this module's already-
    imported state, but setting it explicitly keeps this independent of
    that assumption)."""
    import llm_cost as _llm_cost

    _llm_cost._COST_DIR = Path(cost_dir)
    with patch.object(_llm_cost, "_today", return_value=date):
        _llm_cost.record_call_cost("TCS", "claude-sonnet-4-6", "anthropic", 0.01, 100, 50)


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

    def test_a_broken_first_log_event_call_never_raises(self) -> None:
        # Regression test for an adversarial-review finding: the initial
        # log_event(..., "llm_call_cost", ...) call used to execute BEFORE
        # the try: block guarding the rest of the function, contradicting
        # this function's own "never raises" docstring contract -- a
        # failure there (or in whatever log_event forwards to, e.g. the
        # optional Sentry hook) would have propagated straight out of
        # record_call_cost() and broken the analysis request it's
        # observing. The second side_effect entry lets the failure-warning
        # log_event call inside the except block still succeed, so this
        # test isolates the FIRST call's own coverage rather than the
        # already-covered write-failure path.
        with patch("llm_cost.log_event", side_effect=[Exception("boom"), None]):
            llm_cost.record_call_cost("TCS", "claude-sonnet-4-6", "anthropic", 0.01, 100, 50)  # must not raise

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


class ConcurrencySafetyTest(unittest.TestCase):
    """Regression coverage for the cross-process lost-update race a plain
    in-memory threading.Lock can't prevent: two backend *worker processes*
    (the exact multi-worker/REDIS_URL topology docs/deployment.md's
    "Scaling" section documents as supported) racing to record a call on
    the same UTC day must not silently undercount call_count/total_cost_usd
    — same fcntl.flock-based fix and same test shape as
    tests/test_source_health.py's own ConcurrencySafetyTest."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-llm-cost-lock-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._patch = patch.object(llm_cost, "_COST_DIR", Path(self._tmpdir))
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_locked_excludes_concurrent_holders(self) -> None:
        intervals = []
        lock_guard = threading.Lock()

        def hold(label: str) -> None:
            with llm_cost._locked("2026-01-01"):
                start = time.monotonic()
                time.sleep(0.05)
                end = time.monotonic()
            with lock_guard:
                intervals.append((label, start, end))

        threads = [threading.Thread(target=hold, args=(f"t{i}",)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(intervals), 4)
        intervals.sort(key=lambda x: x[1])
        for (_, _, end_a), (_, start_b, _) in zip(intervals, intervals[1:]):
            self.assertLessEqual(end_a, start_b)  # no overlap between consecutive holders

    def test_concurrent_record_call_cost_loses_no_calls(self) -> None:
        # Regression test: an in-process threading.Lock alone can't prevent
        # a lost update across two *processes* -- this exercises many
        # threads racing for the same file to at least prove the
        # file-level lock serializes correctly within one process; the
        # real multi-process guarantee comes from fcntl.flock being keyed
        # on the open file description, not the process.
        with patch("llm_cost._today", return_value="2026-03-01"):
            def worker() -> None:
                llm_cost.record_call_cost("TCS", "claude-sonnet-4-6", "anthropic", 0.01, 100, 50)

            threads = [threading.Thread(target=worker) for _ in range(30)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            totals = llm_cost.get_daily_totals("2026-03-01")

        self.assertEqual(totals["call_count"], 30)
        self.assertAlmostEqual(totals["total_cost_usd"], 0.30)

    def test_concurrent_calls_never_corrupt_the_file(self) -> None:
        errors = []

        def worker(i: int) -> None:
            try:
                llm_cost.record_call_cost("TCS", "claude-sonnet-4-6", "anthropic", 0.01 * i, 100, 50)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        path = llm_cost._COST_DIR / f"{llm_cost._today()}.json"
        data = json.loads(path.read_text())  # raises if corrupted
        self.assertEqual(data["call_count"], 20)


class MultiProcessConcurrencySafetyTest(unittest.TestCase):
    """The gap ConcurrencySafetyTest's own comments disclose: its tests
    only spawn threads within one process, proving the file-level lock
    serializes correctly in-process — but the bug this module's _locked()
    fix actually targets (docs/deployment.md's supported multi-worker
    topology) is two separate *processes* racing to update the same day's
    counter file, which a plain threading.Lock cannot prevent at all. This
    spawns real OS processes (multiprocessing, fork start method) to
    exercise fcntl.flock's actual cross-process guarantee."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-llm-cost-mp-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)

    def test_concurrent_processes_lose_no_calls(self) -> None:
        ctx = multiprocessing.get_context("fork")
        date = "2026-04-01"
        procs = [
            ctx.Process(target=_mp_record_call, args=(self._tmpdir, date))
            for _ in range(12)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=30)
            self.assertEqual(p.exitcode, 0)

        with patch.object(llm_cost, "_COST_DIR", Path(self._tmpdir)):
            totals = llm_cost.get_daily_totals(date)

        self.assertEqual(totals["call_count"], 12)
        self.assertAlmostEqual(totals["total_cost_usd"], 0.12, places=6)


if __name__ == "__main__":
    unittest.main()
