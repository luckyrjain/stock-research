"""Success-path tests for the /api/analyse and /api/market-picks SSE generators.

These are the most intricate async code in the app (queue-bridged background
tasks, heartbeats, the LLM-concurrency ceiling) and previously had no test
driving a full success sequence — only the 429/error edge cases were covered.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import api
import cache
import market_picks_pipeline
from signals.models import Signal, SignalResult

client = TestClient(api.app)


def _parse_sse(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def _fake_signal_result(symbol: str) -> SignalResult:
    return SignalResult(
        symbol=symbol,
        signals={"volume": Signal("volume", "NORMAL", 0.0, {})},
        final_score=0.2,
        verdict="HOLD",
    )


def _fake_analysis(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "recommendation": "BUY",
        "confidence": "MEDIUM",
        "summary": "Test summary sentence one. Sentence two. Sentence three.",
        "valuation": {"verdict": "Fairly Valued", "comment": "P/E in line with peers."},
        "business_quality": "Solid fundamentals.",
        "bull_factors": ["Factor one", "Factor two", "Factor three"],
        "bear_factors": ["Risk one", "Risk two"],
        "key_risks": ["Risk one", "Risk two"],
        "news_sentiment": "Neutral",
        "news_highlights": "Nothing notable.",
        "institutional_trend": "Stable.",
    }


class AnalyseSuccessPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-analyse-sse-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        patch.object(cache, "CACHE_DIR", Path(self._tmpdir)).start()
        self.addCleanup(patch.stopall)
        api._RATE_LIMIT_CALLS.clear()
        api._llm_concurrency_count = 0

    def tearDown(self) -> None:
        api._RATE_LIMIT_CALLS.clear()
        api._llm_concurrency_count = 0

    def test_full_success_sequence_emits_start_task_done_analysing_done(self) -> None:
        def _fake_fetch_task(task_name, symbol, run_id, max_attempts=3):
            return {"symbol": symbol, "task": task_name}

        with patch("main._fetch_task", side_effect=_fake_fetch_task), \
             patch("schemas.normalize", side_effect=lambda name, data: data), \
             patch("schemas.validate", return_value=(True, "")), \
             patch("signals.engine.run_signal_engine", return_value=_fake_signal_result("TCS")), \
             patch("signals.store.save_signal"), \
             patch("crew.run_analysis_with_fallback", return_value=_fake_analysis("TCS")):
            resp = client.get("/api/analyse/TCS")

        self.assertEqual(resp.status_code, 200)
        events = _parse_sse(resp.text)
        event_names = [e.get("event") for e in events]

        self.assertEqual(event_names[0], "start")
        self.assertEqual(event_names.count("task_done"), 6)  # all six ALL_DATA_TASKS
        self.assertIn("analysing", event_names)
        self.assertEqual(event_names[-1], "done")

        done = events[-1]
        self.assertEqual(done["report"]["symbol"], "TCS")
        self.assertEqual(done["report"]["analysis"]["recommendation"], "BUY")
        # The analyst fallback's internal-only _degraded marker (and cache's _meta)
        # must never leak into the report the frontend receives.
        self.assertNotIn("_degraded", done["report"]["analysis"])

    def test_llm_capacity_rejection_emits_error_event(self) -> None:
        with patch("api._acquire_llm_slot", return_value=False), \
             patch("main._fetch_task", return_value={"symbol": "TCS"}), \
             patch("schemas.normalize", side_effect=lambda name, data: data), \
             patch("schemas.validate", return_value=(True, "")), \
             patch("signals.engine.run_signal_engine", return_value=_fake_signal_result("TCS")), \
             patch("signals.store.save_signal"):
            resp = client.get("/api/analyse/TCS")

        events = _parse_sse(resp.text)
        self.assertEqual(events[-1]["event"], "error")
        self.assertIn("capacity", events[-1]["message"].lower())


class MarketPicksSuccessPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-picks-sse-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        # load_picks_cache/save_picks_cache (re-exported into api.py under
        # their historical names) live in market_picks_pipeline.py and read
        # _PICKS_CACHE_PATH from that module's own globals — must patch it
        # there, not on api.py (which no longer defines that name itself).
        patch.object(market_picks_pipeline, "_PICKS_CACHE_PATH", Path(self._tmpdir) / "picks.json").start()
        self.addCleanup(patch.stopall)
        api._RATE_LIMIT_CALLS.clear()
        api._llm_concurrency_count = 0

    def tearDown(self) -> None:
        api._RATE_LIMIT_CALLS.clear()
        api._llm_concurrency_count = 0

    def _fake_pipeline(self, picks: list, healthy: bool = True):
        instance = MagicMock()
        instance.run.return_value = picks
        instance.healthy = healthy
        cls = MagicMock(return_value=instance)
        return cls

    def test_healthy_run_emits_done_and_writes_cache(self) -> None:
        picks = [{"symbol": "TCS", "confidence_score": 80}]
        with patch("market_picks_pipeline.MarketPicksPipeline", self._fake_pipeline(picks, healthy=True)):
            resp = client.get("/api/market-picks?force=true")

        events = _parse_sse(resp.text)
        self.assertEqual(events[-1]["event"], "done")
        self.assertEqual(events[-1]["total_picks"], 1)
        self.assertFalse(events[-1]["from_cache"])
        self.assertTrue(market_picks_pipeline._PICKS_CACHE_PATH.exists())

    def test_degraded_run_is_not_cached(self) -> None:
        picks = [{"symbol": "TCS", "confidence_score": 80}]
        with patch("market_picks_pipeline.MarketPicksPipeline", self._fake_pipeline(picks, healthy=False)):
            resp = client.get("/api/market-picks?force=true")

        events = _parse_sse(resp.text)
        self.assertEqual(events[-1]["event"], "done")
        self.assertFalse(market_picks_pipeline._PICKS_CACHE_PATH.exists())

    def test_empty_result_is_not_cached(self) -> None:
        with patch("market_picks_pipeline.MarketPicksPipeline", self._fake_pipeline([], healthy=True)):
            resp = client.get("/api/market-picks?force=true")

        events = _parse_sse(resp.text)
        self.assertEqual(events[-1]["event"], "done")
        self.assertEqual(events[-1]["total_picks"], 0)
        self.assertFalse(market_picks_pipeline._PICKS_CACHE_PATH.exists())

    def test_llm_capacity_rejection_emits_error_event(self) -> None:
        with patch("api._acquire_llm_slot", return_value=False):
            resp = client.get("/api/market-picks?force=true")

        events = _parse_sse(resp.text)
        self.assertEqual(events[-1]["event"], "error")
        self.assertIn("capacity", events[-1]["message"].lower())


if __name__ == "__main__":
    unittest.main()
