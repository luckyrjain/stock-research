import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import source_quality_report as sqr


def _write_run(dir_path: Path, run_id: str, timestamp: datetime, sources: dict) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "timestamp": timestamp.isoformat(), "sources": sources}
    (dir_path / f"{run_id}.json").write_text(json.dumps(payload))


class AggregateTest(unittest.TestCase):
    def test_sums_counts_across_runs_within_window(self) -> None:
        now = datetime(2026, 7, 12, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            orig_dir = sqr._DIR
            sqr._DIR = Path(tmp)
            try:
                _write_run(sqr._DIR, "run1", now - timedelta(days=1),
                           {"ET Markets": {"articles_fetched": 10, "picks_extracted": 4, "picks_validated": 3}})
                _write_run(sqr._DIR, "run2", now - timedelta(days=2),
                           {"ET Markets": {"articles_fetched": 8, "picks_extracted": 2, "picks_validated": 1}})
                totals = sqr.aggregate(days=14, now=now)
                self.assertEqual(totals["ET Markets"]["runs"], 2)
                self.assertEqual(totals["ET Markets"]["articles_fetched"], 18)
                self.assertEqual(totals["ET Markets"]["picks_extracted"], 6)
                self.assertEqual(totals["ET Markets"]["picks_validated"], 4)
            finally:
                sqr._DIR = orig_dir

    def test_excludes_runs_outside_window(self) -> None:
        now = datetime(2026, 7, 12, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            orig_dir = sqr._DIR
            sqr._DIR = Path(tmp)
            try:
                _write_run(sqr._DIR, "old", now - timedelta(days=30),
                           {"ET Markets": {"articles_fetched": 10, "picks_extracted": 4, "picks_validated": 3}})
                totals = sqr.aggregate(days=14, now=now)
                self.assertNotIn("ET Markets", totals)
            finally:
                sqr._DIR = orig_dir

    def test_ignores_unparseable_files(self) -> None:
        now = datetime(2026, 7, 12, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            orig_dir = sqr._DIR
            sqr._DIR = Path(tmp)
            try:
                sqr._DIR.mkdir(parents=True, exist_ok=True)
                (sqr._DIR / "broken.json").write_text("not json")
                totals = sqr.aggregate(days=14, now=now)
                self.assertEqual(totals, {})
            finally:
                sqr._DIR = orig_dir

    def test_ignores_naive_timestamp_instead_of_crashing(self) -> None:
        # Regression: comparing a naive (no-tzinfo) timestamp against the
        # tz-aware cutoff used to raise TypeError uncaught, crashing the
        # whole report run instead of skipping just that one file.
        now = datetime(2026, 7, 12, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            orig_dir = sqr._DIR
            sqr._DIR = Path(tmp)
            try:
                sqr._DIR.mkdir(parents=True, exist_ok=True)
                payload = {
                    "run_id": "naive",
                    "timestamp": "2026-07-12T00:00:00",  # no tzinfo
                    "sources": {"ET Markets": {"articles_fetched": 1, "picks_extracted": 0, "picks_validated": 0}},
                }
                (sqr._DIR / "naive.json").write_text(json.dumps(payload))
                totals = sqr.aggregate(days=14, now=now)
                self.assertEqual(totals, {})
            finally:
                sqr._DIR = orig_dir


class RenderTest(unittest.TestCase):
    def test_sorts_worst_survival_first(self) -> None:
        totals = {
            "Good Source": {"runs": 5, "articles_fetched": 50, "picks_extracted": 20, "picks_validated": 19},
            "Bad Source":  {"runs": 5, "articles_fetched": 50, "picks_extracted": 20, "picks_validated": 2},
        }
        output = sqr.render(totals)
        bad_pos  = output.index("Bad Source")
        good_pos = output.index("Good Source")
        self.assertLess(bad_pos, good_pos)

    def test_zero_extracted_shows_dash_not_crash(self) -> None:
        totals = {"Dead Source": {"runs": 3, "articles_fetched": 0, "picks_extracted": 0, "picks_validated": 0}}
        output = sqr.render(totals)
        self.assertIn("Dead Source", output)
        self.assertIn("—", output)


if __name__ == "__main__":
    unittest.main()
