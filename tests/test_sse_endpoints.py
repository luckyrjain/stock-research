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
import rate_limiter
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
        rate_limiter._memory_calls.clear()
        rate_limiter._memory_slots.clear()

    def tearDown(self) -> None:
        rate_limiter._memory_calls.clear()
        rate_limiter._memory_slots.clear()

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

    def test_llm_slot_released_exactly_once_on_success(self) -> None:
        # Regression test: _release_llm_slot() used to be tied to the SSE
        # consumer's own wait-loop finally block rather than to when the
        # background analyst call actually finished, so a client disconnect
        # could release the slot while the LLM call kept running — allowing
        # more concurrent calls than _LLM_CONCURRENCY_LIMIT. The fix moved
        # the release into the background task's own finally, with the
        # outer safety-net release stood down right after handoff so it
        # can't fire a second time once responsibility is handed off.
        #
        # Asserts the real call count via a wraps= mock, not the in-memory
        # slot counter — rate_limiter._release_slot_memory() clamps at 0
        # (max(0, count - 1)), so a double-release would be silently
        # absorbed and a counter-based assertion wouldn't catch it.
        def _fake_fetch_task(task_name, symbol, run_id, max_attempts=3):
            return {"symbol": symbol, "task": task_name}

        with patch("main._fetch_task", side_effect=_fake_fetch_task), \
             patch("schemas.normalize", side_effect=lambda name, data: data), \
             patch("schemas.validate", return_value=(True, "")), \
             patch("signals.engine.run_signal_engine", return_value=_fake_signal_result("TCS")), \
             patch("signals.store.save_signal"), \
             patch("crew.run_analysis_with_fallback", return_value=_fake_analysis("TCS")), \
             patch("api._release_llm_slot", wraps=api._release_llm_slot) as mock_release:
            resp = client.get("/api/analyse/TCS")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_release.call_count, 1)
        self.assertEqual(rate_limiter._memory_slots.get(api._LLM_SLOT_NAME, 0), 0)

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

    def test_unexpected_failure_sanitizes_the_sse_error_message(self) -> None:
        # Security regression: this SSE event reaches the browser directly —
        # a raw exception message (potential file paths, driver internals)
        # must never leak there, same sanitization convention every REST
        # endpoint already follows for its own errors.
        with patch("main._fetch_task", side_effect=RuntimeError("connection refused: password=hunter2")), \
             patch("schemas.normalize", side_effect=lambda name, data: data), \
             patch("schemas.validate", return_value=(True, "")), \
             patch("signals.engine.run_signal_engine", side_effect=RuntimeError("connection refused: password=hunter2")):
            resp = client.get("/api/analyse/TCS")

        events = _parse_sse(resp.text)
        error_events = [e for e in events if e.get("event") == "error"]
        self.assertTrue(error_events)
        for event in error_events:
            self.assertEqual(event["message"], api._SANITIZED_ERROR)
            self.assertNotIn("hunter2", event["message"])
        task_done_errors = [e for e in events if e.get("event") == "task_done" and not e.get("ok")]
        for event in task_done_errors:
            self.assertEqual(event["error"], api._SANITIZED_ERROR)
            self.assertNotIn("hunter2", event["error"])


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
        rate_limiter._memory_calls.clear()
        rate_limiter._memory_slots.clear()
        rate_limiter._memory_locks.clear()

    def tearDown(self) -> None:
        rate_limiter._memory_calls.clear()
        rate_limiter._memory_slots.clear()
        rate_limiter._memory_locks.clear()

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
        # The single-run lock must be released once the pipeline completes,
        # not left claimed — otherwise every subsequent force-refresh would
        # 409 forever.
        self.assertFalse(rate_limiter.is_locked("market_picks_refresh"))

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
        # Capacity rejection happens before the pipeline ever launches — the
        # lock must still be released here too, not just on the success path.
        self.assertFalse(rate_limiter.is_locked("market_picks_refresh"))

    def test_pipeline_failure_sanitizes_the_sse_error_message(self) -> None:
        # Security regression: same sanitization as the analyse endpoint's
        # equivalent test — a pipeline exception must never reach the
        # browser as raw text via this SSE event.
        failing_pipeline = MagicMock()
        failing_pipeline.run.side_effect = RuntimeError("connection refused: password=hunter2")
        with patch("market_picks_pipeline.MarketPicksPipeline", MagicMock(return_value=failing_pipeline)):
            resp = client.get("/api/market-picks?force=true")

        events = _parse_sse(resp.text)
        self.assertEqual(events[-1]["event"], "error")
        self.assertEqual(events[-1]["message"], api._SANITIZED_ERROR)
        self.assertNotIn("hunter2", events[-1]["message"])


if __name__ == "__main__":
    unittest.main()
