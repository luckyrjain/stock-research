import unittest
from datetime import datetime, timedelta, timezone

import source_quality_report as sqr
import state_store
from source_quality import NAMESPACE
from state_store_harness import isolated_state_store


def _write_run(run_id: str, timestamp: datetime, sources: dict) -> None:
    state_store.save(NAMESPACE, run_id, {
        "run_id": run_id, "timestamp": timestamp.isoformat(), "sources": sources,
    })


class AggregateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(isolated_state_store().close)

    def test_sums_counts_across_runs_within_window(self) -> None:
        now = datetime(2026, 7, 12, tzinfo=timezone.utc)
        _write_run("run1", now - timedelta(days=1),
                   {"ET Markets": {"articles_fetched": 10, "picks_extracted": 4, "picks_validated": 3}})
        _write_run("run2", now - timedelta(days=2),
                   {"ET Markets": {"articles_fetched": 8, "picks_extracted": 2, "picks_validated": 1}})
        totals = sqr.aggregate(days=14, now=now)
        self.assertEqual(totals["ET Markets"]["runs"], 2)
        self.assertEqual(totals["ET Markets"]["articles_fetched"], 18)
        self.assertEqual(totals["ET Markets"]["picks_extracted"], 6)
        self.assertEqual(totals["ET Markets"]["picks_validated"], 4)

    def test_excludes_runs_outside_window(self) -> None:
        now = datetime(2026, 7, 12, tzinfo=timezone.utc)
        _write_run("old", now - timedelta(days=30),
                   {"ET Markets": {"articles_fetched": 10, "picks_extracted": 4, "picks_validated": 3}})
        totals = sqr.aggregate(days=14, now=now)
        self.assertNotIn("ET Markets", totals)

    def test_ignores_records_with_no_timestamp(self) -> None:
        now = datetime(2026, 7, 12, tzinfo=timezone.utc)
        state_store.save(NAMESPACE, "broken", {"run_id": "broken", "sources": {
            "ET Markets": {"articles_fetched": 1, "picks_extracted": 0, "picks_validated": 0}}})
        self.assertEqual(sqr.aggregate(days=14, now=now), {})

    def test_ignores_naive_timestamp_instead_of_crashing(self) -> None:
        # Regression: comparing a naive (no-tzinfo) timestamp against the
        # tz-aware cutoff used to raise TypeError uncaught, crashing the
        # whole report run instead of skipping just that one record.
        now = datetime(2026, 7, 12, tzinfo=timezone.utc)
        state_store.save(NAMESPACE, "naive", {
            "run_id": "naive",
            "timestamp": "2026-07-12T00:00:00",  # no tzinfo
            "sources": {"ET Markets": {"articles_fetched": 1, "picks_extracted": 0, "picks_validated": 0}},
        })
        self.assertEqual(sqr.aggregate(days=14, now=now), {})

    def test_no_stored_runs_is_empty_not_an_error(self) -> None:
        self.assertEqual(sqr.aggregate(days=14, now=datetime(2026, 7, 12, tzinfo=timezone.utc)), {})


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
