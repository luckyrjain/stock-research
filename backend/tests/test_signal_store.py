import json
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import signals.store as store
from signals.models import Signal, SignalResult


class SaveSignalTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-signals-store-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        patch.object(store, "BASE", Path(self._tmpdir)).start()
        self.addCleanup(patch.stopall)

    def _result(self, symbol: str = "TCS") -> SignalResult:
        return SignalResult(
            symbol=symbol,
            signals={"volume": Signal("volume", "NORMAL", 0.0, {})},
            final_score=0.3,
            verdict="BUY",
        )

    def test_writes_a_dated_json_file(self) -> None:
        store.save_signal(self._result())
        today_file = store.BASE / "TCS" / f"{date.today()}.json"
        self.assertTrue(today_file.exists())
        data = json.loads(today_file.read_text())
        self.assertEqual(data["verdict"], "BUY")

    def test_prunes_snapshots_older_than_retention_window(self) -> None:
        symbol_dir = store.BASE / "TCS"
        symbol_dir.mkdir(parents=True)
        old_date = date.today() - timedelta(days=store._RETENTION_DAYS + 5)
        recent_date = date.today() - timedelta(days=5)
        (symbol_dir / f"{old_date}.json").write_text("{}")
        (symbol_dir / f"{recent_date}.json").write_text("{}")

        store.save_signal(self._result())

        remaining = {p.stem for p in symbol_dir.glob("*.json")}
        self.assertNotIn(str(old_date), remaining)
        self.assertIn(str(recent_date), remaining)
        self.assertIn(str(date.today()), remaining)

    def test_write_failure_never_raises(self) -> None:
        # Regression test for an adversarial-review finding: save_signal()
        # had no exception handling of its own, unlike every sibling
        # best-effort persistence module -- both main.py and api.py's SSE
        # handler call this unguarded, right before the expensive LLM
        # analyst call, so a filesystem hiccup here (disk full, a
        # read-only mount) aborted the entire analysis over a write-only
        # audit-trail file nothing else in the codebase reads back.
        with patch("builtins.open", side_effect=OSError("disk full")):
            store.save_signal(self._result())  # must not raise

    def test_unexpected_filename_is_skipped_not_fatal(self) -> None:
        symbol_dir = store.BASE / "TCS"
        symbol_dir.mkdir(parents=True)
        (symbol_dir / "not-a-date.json").write_text("{}")

        store.save_signal(self._result())  # must not raise

        self.assertTrue((symbol_dir / "not-a-date.json").exists())


if __name__ == "__main__":
    unittest.main()
