import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import source_health


class RecordAndCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-source-health-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._patch = patch.object(source_health, "_HEALTH_DIR", Path(self._tmpdir))
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _history(self, source: str) -> list:
        path = source_health._path(source)
        if not path.exists():
            return []
        return json.loads(path.read_text())["days"]

    def _oks(self, source: str) -> list:
        return [d["ok"] for d in self._history(source)]

    def _record_on_days(self, source: str, dates_and_oks: list) -> None:
        """Simulate `len(dates_and_oks)` distinct-day calls without sleeping
        in real time, via record_and_check's explicit `date` override."""
        for d, ok in dates_and_oks:
            source_health.record_and_check(source, ok, date=d)

    def test_first_run_is_recorded_with_no_alert(self) -> None:
        with patch("source_health.log_event") as mock_log:
            source_health.record_and_check("Test Source", True)
        self.assertEqual(self._oks("Test Source"), [True])
        mock_log.assert_not_called()

    def test_new_source_failing_from_the_start_does_not_alert(self) -> None:
        # No established healthy baseline yet — a source that's simply
        # always been empty (e.g. genuinely no coverage) shouldn't page
        # anyone.
        with patch("source_health.log_event") as mock_log:
            self._record_on_days(
                "New Source",
                [(f"2026-01-0{i}", False) for i in range(1, 6)],
            )
        mock_log.assert_not_called()

    def test_healthy_source_failing_three_days_in_a_row_alerts(self) -> None:
        with patch("source_health.log_event") as mock_log:
            self._record_on_days(
                "Reliable Source",
                [(f"2026-01-0{i}", True) for i in range(1, 6)]
                + [(f"2026-01-0{i}", False) for i in range(6, 9)],
            )
        mock_log.assert_called_once()
        args, kwargs = mock_log.call_args
        self.assertEqual(args[1], "source_health_anomaly")
        self.assertEqual(kwargs["level"], "warning")

    def test_healthy_source_failing_only_two_days_does_not_alert_yet(self) -> None:
        with patch("source_health.log_event") as mock_log:
            self._record_on_days(
                "Reliable Source",
                [(f"2026-01-0{i}", True) for i in range(1, 6)]
                + [(f"2026-01-0{i}", False) for i in range(6, 8)],
            )
        mock_log.assert_not_called()

    def test_alert_fires_only_once_not_every_subsequent_failing_day(self) -> None:
        with patch("source_health.log_event") as mock_log:
            self._record_on_days(
                "Reliable Source",
                [(f"2026-01-0{i}", True) for i in range(1, 6)]
                + [(f"2026-01-0{i}", False) for i in range(6, 9)],
            )
            self.assertEqual(mock_log.call_count, 1)
            self._record_on_days("Reliable Source", [("2026-01-09", False)])
        self.assertEqual(mock_log.call_count, 2)  # still 3 consecutive bad days each time — expected re-alert

    def test_recovery_resets_the_consecutive_failure_streak(self) -> None:
        with patch("source_health.log_event") as mock_log:
            self._record_on_days(
                "Reliable Source",
                [(f"2026-01-0{i}", True) for i in range(1, 6)]
                + [("2026-01-06", False)]
                + [("2026-01-07", False)]
                + [("2026-01-08", True)]  # recovers
                + [("2026-01-09", False)]
                + [("2026-01-10", False)],
            )
        mock_log.assert_not_called()

    def test_multiple_calls_on_the_same_day_collapse_to_one_entry(self) -> None:
        # A burst of same-day force-refresh retries must not each count as
        # their own data point — otherwise 3 quick retries within an hour
        # would trip the "3 consecutive bad days" threshold, which is
        # meant to mean 3 distinct calendar days, not 3 raw calls.
        source_health.record_and_check("Bursty Source", True, date="2026-01-01")
        source_health.record_and_check("Bursty Source", False, date="2026-01-01")
        source_health.record_and_check("Bursty Source", False, date="2026-01-01")
        self.assertEqual(self._history("Bursty Source"), [{"date": "2026-01-01", "ok": False}])

    def test_same_day_collapse_does_not_falsely_alert(self) -> None:
        with patch("source_health.log_event") as mock_log:
            self._record_on_days(
                "Reliable Source",
                [(f"2026-01-0{i}", True) for i in range(1, 6)],
            )
            for _ in range(5):  # a burst of failures, but all on ONE day
                source_health.record_and_check("Reliable Source", False, date="2026-01-06")
        mock_log.assert_not_called()  # only 1 distinct bad day so far, not 3

    def test_history_is_capped_to_max_history_days(self) -> None:
        self._record_on_days(
            "Long Running Source",
            [(f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", True) for i in range(source_health._MAX_HISTORY + 10)],
        )
        self.assertEqual(len(self._history("Long Running Source")), source_health._MAX_HISTORY)

    def test_different_sources_do_not_share_history(self) -> None:
        source_health.record_and_check("Source A", True)
        source_health.record_and_check("Source B", False)
        self.assertEqual(self._oks("Source A"), [True])
        self.assertEqual(self._oks("Source B"), [False])

    def test_special_characters_in_source_name_do_not_raise(self) -> None:
        with patch("source_health.log_event"):
            source_health.record_and_check("Motilal Oswal / ICICI Direct / Axis Securities", True)
        # No exception is the assertion here.

    def test_corrupt_history_file_is_treated_as_empty_not_fatal(self) -> None:
        path = source_health._path("Bad File Source")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json")
        with patch("source_health.log_event") as mock_log:
            source_health.record_and_check("Bad File Source", True)
        self.assertEqual(len(self._oks("Bad File Source")), 1)
        mock_log.assert_not_called()

    def test_never_raises_even_if_health_dir_is_unwritable(self) -> None:
        with patch.object(source_health, "_HEALTH_DIR", Path("/nonexistent/root/that/cannot/be/created/at/all")), \
             patch("pathlib.Path.mkdir", side_effect=PermissionError("nope")):
            try:
                source_health.record_and_check("Any Source", True)
            except Exception as exc:  # pragma: no cover - the assertion IS that this doesn't happen
                self.fail(f"record_and_check raised: {exc}")


class ConcurrencySafetyTest(unittest.TestCase):
    """Regression coverage for the reproduced concurrent-writer race: two
    callers racing to update the same source's file must not corrupt the
    JSON on disk, and must not silently lose one caller's update."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-source-health-lock-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self._patch = patch.object(source_health, "_HEALTH_DIR", Path(self._tmpdir))
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_locked_excludes_concurrent_holders(self) -> None:
        """Two threads racing for the same source's lock must never hold
        it at the same time — the property the old unlocked
        read-modify-write violated."""
        intervals = []
        lock_guard = threading.Lock()

        def hold_and_record(label: str) -> None:
            with source_health._locked("Same Source"):
                start = time.monotonic()
                time.sleep(0.05)
                end = time.monotonic()
            with lock_guard:
                intervals.append((label, start, end))

        threads = [threading.Thread(target=hold_and_record, args=(f"t{i}",)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(intervals), 4)
        intervals.sort(key=lambda x: x[1])
        for (_, _, end_a), (_, start_b, _) in zip(intervals, intervals[1:]):
            self.assertLessEqual(end_a, start_b)  # no overlap between consecutive holders

    def test_concurrent_record_and_check_never_corrupts_the_file(self) -> None:
        """Many threads hammering record_and_check for the same source
        concurrently must always leave valid, parseable JSON on disk —
        the old unlocked version could interleave two writers' output
        into invalid JSON."""
        errors = []

        def worker(i: int) -> None:
            try:
                source_health.record_and_check("Hammered Source", i % 2 == 0)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        path = source_health._path("Hammered Source")
        data = json.loads(path.read_text())  # raises if corrupted
        self.assertIn("days", data)

    def test_concurrent_calls_on_distinct_days_lose_no_updates(self) -> None:
        """Simulates several worker threads each recording a DIFFERENT
        day's result for the same source at once (e.g. a backfill or
        several pipeline runs racing) — every distinct day's entry must
        survive, none silently dropped by a lost-update race."""
        dates = [f"2026-02-{i:02d}" for i in range(1, 11)]

        def worker(date: str) -> None:
            source_health.record_and_check("Backfilled Source", True, date=date)

        threads = [threading.Thread(target=worker, args=(d,)) for d in dates]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        path = source_health._path("Backfilled Source")
        recorded_dates = {d["date"] for d in json.loads(path.read_text())["days"]}
        self.assertEqual(recorded_dates, set(dates))


if __name__ == "__main__":
    unittest.main()
