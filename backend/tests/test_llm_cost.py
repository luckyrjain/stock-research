import multiprocessing
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

import llm_cost
from state_store_harness import isolated_state_store, shared_state_store


def _mp_record_call(state_dir: str, date: str) -> None:
    """Top-level (picklable) worker for MultiProcessConcurrencySafetyTest —
    runs in a genuinely separate OS process, not a thread, to exercise the
    actual cross-process guarantee state_store.mutate()'s row lock provides
    rather than only the in-process serialization threads already prove.

    Builds its own engine over the parent's SQLite file rather than
    inheriting one: a SQLAlchemy engine is not fork-safe, since its pooled
    connections are file descriptors the parent also holds."""
    import llm_cost as _llm_cost

    with shared_state_store(state_dir, create=False):
        with patch.object(_llm_cost, "_today", return_value=date):
            _llm_cost.record_call_cost("TCS", "claude-sonnet-4-6", "anthropic", 0.01, 100, 50)


class LlmCostTest(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(isolated_state_store().close)

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

    def test_multiple_days_are_tracked_independently(self) -> None:
        with patch("llm_cost._today", return_value="2026-01-01"):
            llm_cost.record_call_cost("TCS", "claude-sonnet-4-6", "anthropic", 0.01, 100, 50)
        with patch("llm_cost._today", return_value="2026-01-02"):
            llm_cost.record_call_cost("TCS", "claude-sonnet-4-6", "anthropic", 0.05, 100, 50)

        self.assertEqual(llm_cost.get_daily_totals("2026-01-01")["total_cost_usd"], 0.01)
        self.assertEqual(llm_cost.get_daily_totals("2026-01-02")["total_cost_usd"], 0.05)


class BrokenStateStoreTest(unittest.TestCase):
    """Cost tracking must degrade, never break the analysis request it's
    observing — the same guarantee the old "a broken _COST_DIR never raises"
    and "a corrupt counter file reads as zeros" tests covered when this was a
    directory of JSON files."""

    def setUp(self) -> None:
        self.addCleanup(isolated_state_store().close)

    def test_record_call_cost_never_raises_when_the_store_is_broken(self) -> None:
        with patch("core.state_store._get_engine", side_effect=RuntimeError("db down")):
            llm_cost.record_call_cost("TCS", "claude-sonnet-4-6", "anthropic", 0.01, 100, 50)  # must not raise

    def test_get_daily_totals_degrades_to_zeros_when_the_store_is_broken(self) -> None:
        with patch("core.state_store._get_engine", side_effect=RuntimeError("db down")):
            totals = llm_cost.get_daily_totals()
        self.assertEqual(totals, {"call_count": 0, "total_cost_usd": 0.0, "calls_with_unknown_cost": 0})


class ConcurrencySafetyTest(unittest.TestCase):
    """Regression coverage for the lost-update race a plain in-memory
    threading.Lock can't prevent: two backend workers (the multi-worker
    topology docs/deployment.md's "Scaling" section documents as supported)
    racing to record a call on the same UTC day must not silently undercount
    call_count/total_cost_usd — same shape as tests/test_source_health.py's
    own ConcurrencySafetyTest.

    File-backed, not in-memory: StaticPool would hand every thread the same
    DBAPI connection, which can't hold concurrent transactions at all."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-llm-cost-lock-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self.addCleanup(shared_state_store(self._tmpdir).close)

    def test_concurrent_record_call_cost_loses_no_calls(self) -> None:
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

    def test_concurrent_calls_never_corrupt_the_record(self) -> None:
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
        self.assertEqual(llm_cost.get_daily_totals()["call_count"], 20)


class MultiProcessConcurrencySafetyTest(unittest.TestCase):
    """The gap ConcurrencySafetyTest's own comments disclose: its tests only
    spawn threads within one process. The race state_store.mutate() actually
    targets (docs/deployment.md's supported multi-worker topology) is two
    separate *processes* contending for the same day's counter, which a plain
    threading.Lock cannot prevent at all. This spawns real OS processes."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-llm-cost-mp-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        # create=True here so the child processes (create=False) find the
        # tables already there rather than 12 of them racing to create them.
        self.addCleanup(shared_state_store(self._tmpdir).close)

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
            p.join(timeout=60)
            self.assertEqual(p.exitcode, 0)

        totals = llm_cost.get_daily_totals(date)

        self.assertEqual(totals["call_count"], 12)
        self.assertAlmostEqual(totals["total_cost_usd"], 0.12, places=6)


if __name__ == "__main__":
    unittest.main()
