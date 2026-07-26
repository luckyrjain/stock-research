import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scraper_error_counters


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


if __name__ == "__main__":
    unittest.main()
