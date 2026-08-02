import unittest
from unittest.mock import patch

from pipelines import market_picks_pipeline as mpp
import source_quality as sq
from core import state_store
from state_store_harness import isolated_state_store


class RecordRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(isolated_state_store().close)

    def test_stores_expected_shape_under_the_run_id(self) -> None:
        sq.record_run(
            "abc123",
            {
                "ET Markets": {"articles_fetched": 12, "picks_extracted": 4, "picks_validated": 3},
                "GNews — Financial Express": {"articles_fetched": 8, "picks_extracted": 1, "picks_validated": 0},
            },
        )
        stored = state_store.items(sq.NAMESPACE)
        self.assertEqual([key for key, _ in stored], ["abc123"])
        data = stored[0][1]
        self.assertEqual(data["run_id"], "abc123")
        self.assertIn("timestamp", data)
        self.assertEqual(
            data["sources"]["ET Markets"],
            {"articles_fetched": 12, "picks_extracted": 4, "picks_validated": 3},
        )
        self.assertEqual(
            data["sources"]["GNews — Financial Express"],
            {"articles_fetched": 8, "picks_extracted": 1, "picks_validated": 0},
        )

    def test_swallows_write_failure(self) -> None:
        with patch("core.state_store._get_engine", side_effect=RuntimeError("db down")):
            try:
                sq.record_run("xyz789", {"ET Markets": {"articles_fetched": 1, "picks_extracted": 0, "picks_validated": 0}})
            except Exception as exc:  # pragma: no cover - test fails via assertion below, not exception
                self.fail(f"record_run raised: {exc}")


class AggregateSourceStatsTest(unittest.TestCase):
    def test_tallies_all_three_metrics_per_source(self) -> None:
        raw_sources = {
            "ET Markets": {"source": "ET Markets", "type": "news", "articles": [{"title": "a"}, {"title": "b"}]},
            "LiveMint":   {"source": "LiveMint", "type": "news", "articles": [{"title": "c"}]},
        }
        raw_picks = [
            {"source": "ET Markets", "ticker": "TCS"},
            {"source": "ET Markets", "ticker": "INFY"},
            {"source": "LiveMint",   "ticker": "TCS"},
        ]
        consolidated = [
            {
                "symbol": "TCS",
                "sources": [
                    {"name": "ET Markets"},
                    {"name": "LiveMint"},
                ],
            },
            # INFY's pick from ET Markets did NOT survive validation — absent here.
        ]
        stats = mpp._aggregate_source_stats(raw_sources, raw_picks, consolidated)

        self.assertEqual(stats["ET Markets"], {"articles_fetched": 2, "picks_extracted": 2, "picks_validated": 1})
        self.assertEqual(stats["LiveMint"],   {"articles_fetched": 1, "picks_extracted": 1, "picks_validated": 1})

    def test_every_source_in_registry_present_even_with_zero_activity(self) -> None:
        from tools.market_picks_tools import SOURCES
        stats = mpp._aggregate_source_stats({}, [], [])
        self.assertEqual(set(stats.keys()), {name for name, _type, _fn in SOURCES})
        for entry in stats.values():
            self.assertEqual(entry, {"articles_fetched": 0, "picks_extracted": 0, "picks_validated": 0})

    def test_unknown_source_name_in_inputs_is_ignored(self) -> None:
        # Defensive: a source name that isn't in SOURCES (e.g. stale data) must not
        # crash or silently create a new key outside the registry.
        raw_sources = {"Some Removed Source": {"articles": [{"title": "x"}]}}
        stats = mpp._aggregate_source_stats(raw_sources, [], [])
        self.assertNotIn("Some Removed Source", stats)


class RunRecordsTelemetryTest(unittest.TestCase):
    def test_run_calls_record_run_with_aggregated_stats(self) -> None:
        pipeline = mpp.MarketPicksPipeline()
        pipeline._run_id = "test-run-id"

        raw_sources  = {"ET Markets": {"articles": [{"title": "a"}]}}
        raw_picks    = [{"source": "ET Markets", "ticker": "TCS"}]
        consolidated = [{"symbol": "TCS", "sources": [{"name": "ET Markets"}]}]

        pipeline._phase_scrape      = lambda emit: raw_sources
        pipeline._phase_extract     = lambda raw, emit: raw_picks
        pipeline._phase_consolidate = lambda picks, emit: consolidated
        pipeline._phase_research    = lambda cons, emit: {}
        pipeline._phase_analyze     = lambda cons, research, emit: {}
        pipeline._phase_score       = lambda cons, research, analyses, emit: [{"symbol": "TCS"}]

        with patch("pipelines.market_picks_pipeline.source_quality.record_run") as mock_record:
            result = pipeline.run()

        self.assertEqual(result, [{"symbol": "TCS"}])
        mock_record.assert_called_once()
        called_run_id, called_stats = mock_record.call_args[0]
        self.assertEqual(called_run_id, "test-run-id")
        self.assertEqual(called_stats["ET Markets"]["articles_fetched"], 1)
        self.assertEqual(called_stats["ET Markets"]["picks_extracted"], 1)
        self.assertEqual(called_stats["ET Markets"]["picks_validated"], 1)


if __name__ == "__main__":
    unittest.main()
