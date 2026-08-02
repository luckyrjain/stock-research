import multiprocessing
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from telemetry import scraper_error_counters
from core import state_store
from state_store_harness import isolated_state_store, shared_state_store


def _mp_record_error(state_dir: str, scraper_name: str) -> None:
    """Top-level (picklable) worker for MultiProcessConcurrencySafetyTest —
    runs in a genuinely separate OS process, not a thread, to exercise the
    actual cross-process guarantee state_store.mutate()'s row lock provides —
    same pattern as tests/test_llm_cost.py's own _mp_record_call."""
    from telemetry import scraper_error_counters as _sec

    with shared_state_store(state_dir, create=False):
        _sec.record_scraper_error(scraper_name)


class RecordScraperErrorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(isolated_state_store().close)

    def test_first_error_is_recorded_and_logged(self) -> None:
        with patch("telemetry.scraper_error_counters.log_event") as mock_log:
            scraper_error_counters.record_scraper_error("peers", symbol="TCS")
        self.assertEqual(scraper_error_counters.get_error_count("peers"), 1)
        mock_log.assert_called_once()
        _, args, kwargs = mock_log.mock_calls[0]
        self.assertEqual(kwargs.get("level"), "warning")
        self.assertEqual(kwargs.get("scraper"), "peers")
        self.assertEqual(kwargs.get("symbol"), "TCS")
        self.assertEqual(kwargs.get("error_count"), 1)

    def test_errors_accumulate_across_calls(self) -> None:
        scraper_error_counters.record_scraper_error("insider_trades", symbol="TCS")
        scraper_error_counters.record_scraper_error("insider_trades", symbol="INFY")
        scraper_error_counters.record_scraper_error("insider_trades", symbol="WIPRO")
        self.assertEqual(scraper_error_counters.get_error_count("insider_trades"), 3)

    def test_distinct_scrapers_have_independent_counters(self) -> None:
        scraper_error_counters.record_scraper_error("peers", symbol="TCS")
        scraper_error_counters.record_scraper_error("financials", symbol="TCS")
        scraper_error_counters.record_scraper_error("financials", symbol="TCS")
        self.assertEqual(scraper_error_counters.get_error_count("peers"), 1)
        self.assertEqual(scraper_error_counters.get_error_count("financials"), 2)

    def test_get_error_count_for_unknown_scraper_is_zero_not_an_error(self) -> None:
        self.assertEqual(scraper_error_counters.get_error_count("never_recorded"), 0)

    def test_scraper_name_is_sanitized_into_the_record_key(self) -> None:
        scraper_error_counters.record_scraper_error("Trendlyne / Numeric-Consensus")
        key = scraper_error_counters._safe_name("Trendlyne / Numeric-Consensus")
        self.assertNotIn("/", key)
        self.assertIsNotNone(state_store.load(scraper_error_counters._NAMESPACE, key))
        self.assertEqual(scraper_error_counters.get_error_count("Trendlyne / Numeric-Consensus"), 1)

    def test_a_broken_state_store_never_raises_and_still_logs_the_error(self) -> None:
        # The counter is the lesser half of this module's job — the warning
        # line is what an operator actually greps for, so a store failure
        # must still emit it (with a null count rather than a guessed one)
        # instead of swallowing the scraper error entirely.
        with patch("core.state_store._get_engine", side_effect=RuntimeError("db down")):
            with patch("telemetry.scraper_error_counters.log_event") as mock_log:
                scraper_error_counters.record_scraper_error("peers", symbol="TCS")

        mock_log.assert_called_once()
        kwargs = mock_log.mock_calls[0].kwargs
        self.assertEqual(kwargs.get("level"), "warning")
        self.assertEqual(kwargs.get("scraper"), "peers")
        self.assertIsNone(kwargs.get("error_count"))

    def test_get_error_count_never_raises_when_the_store_is_broken(self) -> None:
        with patch("core.state_store._get_engine", side_effect=RuntimeError("db down")):
            self.assertEqual(scraper_error_counters.get_error_count("peers"), 0)


class ConcurrencySafetyTest(unittest.TestCase):
    """Regression coverage for an adversarial-review finding: an earlier
    version of this module guarded its read-modify-write with only an
    in-process threading.Lock, which does nothing to prevent two backend
    *worker processes* (the exact multi-worker/REDIS_URL topology
    docs/deployment.md's "Scaling" section documents as supported) from
    both reading the same prior error_count and one write silently
    clobbering the other — permanently undercounting with no warning
    logged. Same shape as tests/test_llm_cost.py's own ConcurrencySafetyTest.

    File-backed, not in-memory: StaticPool would hand every thread the same
    DBAPI connection, which can't hold concurrent transactions at all."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-scraper-error-counters-lock-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self.addCleanup(shared_state_store(self._tmpdir).close)

    def test_concurrent_record_scraper_error_loses_no_calls(self) -> None:
        threads = [
            threading.Thread(target=scraper_error_counters.record_scraper_error, args=("peers",))
            for _ in range(30)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(scraper_error_counters.get_error_count("peers"), 30)


class MultiProcessConcurrencySafetyTest(unittest.TestCase):
    """The gap ConcurrencySafetyTest's own comments disclose: its tests only
    spawn threads within one process. The bug this module's locking actually
    targets is two separate *processes* contending for the same scraper's
    counter, which a plain threading.Lock cannot prevent at all. This spawns
    real OS processes — same pattern as tests/test_llm_cost.py's own
    MultiProcessConcurrencySafetyTest."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-scraper-error-counters-mp-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self.addCleanup(shared_state_store(self._tmpdir).close)

    def test_concurrent_processes_lose_no_calls(self) -> None:
        ctx = multiprocessing.get_context("fork")
        procs = [
            ctx.Process(target=_mp_record_error, args=(self._tmpdir, "peers"))
            for _ in range(12)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=60)
            self.assertEqual(p.exitcode, 0)

        self.assertEqual(scraper_error_counters.get_error_count("peers"), 12)


if __name__ == "__main__":
    unittest.main()
