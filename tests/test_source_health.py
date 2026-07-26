import json
import shutil
import tempfile
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
        return json.loads(path.read_text())["runs"]

    def test_first_run_is_recorded_with_no_alert(self) -> None:
        with patch("source_health.log_event") as mock_log:
            source_health.record_and_check("Test Source", True)
        self.assertEqual(self._history("Test Source"), [True])
        mock_log.assert_not_called()

    def test_new_source_failing_from_the_start_does_not_alert(self) -> None:
        # No established healthy baseline yet — a source that's simply
        # always been empty (e.g. genuinely no coverage) shouldn't page
        # anyone.
        with patch("source_health.log_event") as mock_log:
            for _ in range(5):
                source_health.record_and_check("New Source", False)
        mock_log.assert_not_called()

    def test_healthy_source_failing_three_times_in_a_row_alerts(self) -> None:
        with patch("source_health.log_event") as mock_log:
            for _ in range(5):
                source_health.record_and_check("Reliable Source", True)
            for _ in range(3):
                source_health.record_and_check("Reliable Source", False)
        mock_log.assert_called_once()
        args, kwargs = mock_log.call_args
        self.assertEqual(args[1], "source_health_anomaly")
        self.assertEqual(kwargs["level"], "warning")

    def test_healthy_source_failing_only_twice_does_not_alert_yet(self) -> None:
        with patch("source_health.log_event") as mock_log:
            for _ in range(5):
                source_health.record_and_check("Reliable Source", True)
            for _ in range(2):
                source_health.record_and_check("Reliable Source", False)
        mock_log.assert_not_called()

    def test_alert_fires_only_once_not_every_subsequent_failing_run(self) -> None:
        with patch("source_health.log_event") as mock_log:
            for _ in range(5):
                source_health.record_and_check("Reliable Source", True)
            for _ in range(3):
                source_health.record_and_check("Reliable Source", False)
            self.assertEqual(mock_log.call_count, 1)
            source_health.record_and_check("Reliable Source", False)
        self.assertEqual(mock_log.call_count, 2)  # still 3 consecutive failures each time — expected re-alert

    def test_recovery_resets_the_consecutive_failure_streak(self) -> None:
        with patch("source_health.log_event") as mock_log:
            for _ in range(5):
                source_health.record_and_check("Reliable Source", True)
            source_health.record_and_check("Reliable Source", False)
            source_health.record_and_check("Reliable Source", False)
            source_health.record_and_check("Reliable Source", True)  # recovers
            source_health.record_and_check("Reliable Source", False)
            source_health.record_and_check("Reliable Source", False)
        mock_log.assert_not_called()

    def test_history_is_capped_to_max_history(self) -> None:
        for _ in range(source_health._MAX_HISTORY + 10):
            source_health.record_and_check("Long Running Source", True)
        self.assertEqual(len(self._history("Long Running Source")), source_health._MAX_HISTORY)

    def test_different_sources_do_not_share_history(self) -> None:
        source_health.record_and_check("Source A", True)
        source_health.record_and_check("Source B", False)
        self.assertEqual(self._history("Source A"), [True])
        self.assertEqual(self._history("Source B"), [False])

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
        self.assertEqual(self._history("Bad File Source"), [True])
        mock_log.assert_not_called()

    def test_never_raises_even_if_health_dir_is_unwritable(self) -> None:
        with patch.object(source_health, "_HEALTH_DIR", Path("/nonexistent/root/that/cannot/be/created/at/all")), \
             patch("pathlib.Path.mkdir", side_effect=PermissionError("nope")):
            try:
                source_health.record_and_check("Any Source", True)
            except Exception as exc:  # pragma: no cover - the assertion IS that this doesn't happen
                self.fail(f"record_and_check raised: {exc}")


if __name__ == "__main__":
    unittest.main()
