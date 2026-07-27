import multiprocessing
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import scraper_error_counters


def _mp_record_error(counters_dir: str, scraper_name: str) -> None:
    """Top-level (picklable) worker for MultiProcessConcurrencySafetyTest —
    runs in a genuinely separate OS process, not a thread, to exercise the
    actual cross-process guarantee fcntl.flock provides — same pattern as
    tests/test_llm_cost.py's own _mp_record_call."""
    import scraper_error_counters as _sec

    _sec._COUNTERS_DIR = Path(counters_dir)
    _sec.record_scraper_error(scraper_name)


class RecordScraperErrorTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-scraper-error-counters-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._patch = patch.object(scraper_error_counters, "_COUNTERS_DIR", Path(self._tmpdir))
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_first_error_is_recorded_and_logged(self) -> None:
        with patch("scraper_error_counters.log_event") as mock_log:
            scraper_error_counters.record_scraper_error("peers", symbol="TCS")
        self.assertEqual(scraper_error_counters.get_error_count("peers"), 1)
        mock_log.assert_called_once()
        _, args, kwargs = mock_log.mock_calls[0]
        self.assertEqual(kwargs.get("level"), "warning")
        self.assertEqual(kwargs.get("scraper"), "peers")
        self.assertEqual(kwargs.get("symbol"), "TCS")

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

    def test_scraper_name_is_sanitized_for_the_filesystem(self) -> None:
        scraper_error_counters.record_scraper_error("Trendlyne / Numeric-Consensus")
        path = scraper_error_counters._path("Trendlyne / Numeric-Consensus")
        self.assertTrue(path.exists())
        self.assertNotIn("/", path.name)

    def test_a_broken_counters_dir_never_raises(self) -> None:
        # Simulate a filesystem failure by pointing at a path that can't be
        # created (a file, not a directory, in the way) — record_scraper_error
        # must swallow this, matching the "tools must not raise" convention
        # this module observes rather than participates in.
        blocked = Path(self._tmpdir) / "blocked"
        blocked.write_text("not a directory")
        with patch.object(scraper_error_counters, "_COUNTERS_DIR", blocked / "nested"):
            with patch("scraper_error_counters.log_event") as mock_log:
                scraper_error_counters.record_scraper_error("peers", symbol="TCS")
            mock_log.assert_called_once()
            self.assertEqual(mock_log.mock_calls[0].kwargs.get("level"), "warning")

    def test_get_error_count_never_raises_on_corrupt_file(self) -> None:
        path = scraper_error_counters._path("peers")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json")
        self.assertEqual(scraper_error_counters.get_error_count("peers"), 0)


class ConcurrencySafetyTest(unittest.TestCase):
    """Regression coverage for an adversarial-review finding: an earlier
    version of this module guarded its read-modify-write with only an
    in-process threading.Lock, which does nothing to prevent two backend
    *worker processes* (the exact multi-worker/REDIS_URL topology
    docs/deployment.md's "Scaling" section documents as supported) from
    both reading the same prior error_count and one write silently
    clobbering the other — permanently undercounting with no warning
    logged. Same fcntl.flock-based fix and same test shape as
    tests/test_llm_cost.py's own ConcurrencySafetyTest."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-scraper-error-counters-lock-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._patch = patch.object(scraper_error_counters, "_COUNTERS_DIR", Path(self._tmpdir))
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_locked_excludes_concurrent_holders(self) -> None:
        intervals = []
        lock_guard = threading.Lock()

        def hold(label: str) -> None:
            with scraper_error_counters._locked("peers"):
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
    """The gap ConcurrencySafetyTest's own comments disclose: its tests
    only spawn threads within one process, proving the file-level lock
    serializes correctly in-process — but the bug this module's _locked()
    fix actually targets is two separate *processes* racing to update the
    same scraper's counter file, which a plain threading.Lock cannot
    prevent at all. This spawns real OS processes (multiprocessing, fork
    start method) to exercise fcntl.flock's actual cross-process
    guarantee — same pattern as tests/test_llm_cost.py's own
    MultiProcessConcurrencySafetyTest."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-scraper-error-counters-mp-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)

    def test_concurrent_processes_lose_no_calls(self) -> None:
        ctx = multiprocessing.get_context("fork")
        procs = [
            ctx.Process(target=_mp_record_error, args=(self._tmpdir, "peers"))
            for _ in range(12)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=30)
            self.assertEqual(p.exitcode, 0)

        with patch.object(scraper_error_counters, "_COUNTERS_DIR", Path(self._tmpdir)):
            count = scraper_error_counters.get_error_count("peers")

        self.assertEqual(count, 12)


if __name__ == "__main__":
    unittest.main()
