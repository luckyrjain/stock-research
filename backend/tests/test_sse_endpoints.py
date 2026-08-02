"""Success-path tests for the /api/analyse and /api/market-picks SSE generators.

These are the most intricate async code in the app (queue-bridged background
tasks, heartbeats, the LLM-concurrency ceiling) and previously had no test
driving a full success sequence — only the 429/error edge cases were covered.
"""
import asyncio
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


class AnalysisPersistedDespiteDisconnectTest(unittest.IsolatedAsyncioTestCase):
    """Regression test for an adversarial-review finding: cache.save() and
    save_verdict_snapshot() used to run only AFTER the SSE consumer's own
    `await asyncio.wait_for(done_q.get(), timeout=15)` returned. A real
    client disconnect while the generator is suspended there (a realistic
    window — LLM calls routinely take 10s+, spanning many 15s heartbeat
    cycles) tears down the generator at that await point, but the
    background `_run_and_signal()` task — created via asyncio.create_task,
    not a child of the generator — keeps running independently, computes a
    real (already-billed) LLM result, and then has nobody left to persist
    it. The fix moved persistence into `_run_and_signal()` itself so it
    happens unconditionally, before `done_q.put()`, regardless of whether
    the consumer is still around to read the queue.

    Simulated here by driving the real `stream()` async generator by hand
    (via `analyse()`'s returned StreamingResponse.body_iterator) up through
    the "analysing" event — the point where `asyncio.create_task(
    _run_and_signal())` fires — then abandoning the generator's next
    `__anext__()` call via a short outer timeout, which injects a
    CancelledError at the exact `await asyncio.wait_for(done_q.get(), ...)`
    suspension point a real disconnect would interrupt. The independently
    created background task is untouched by that cancellation and keeps
    running, exactly matching the real bug scenario."""

    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-analyse-disconnect-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        patch.object(cache, "CACHE_DIR", Path(self._tmpdir)).start()
        self.addCleanup(patch.stopall)
        rate_limiter._memory_calls.clear()
        rate_limiter._memory_slots.clear()
        self.addCleanup(rate_limiter._memory_calls.clear)
        self.addCleanup(rate_limiter._memory_slots.clear)

    async def test_llm_result_is_cached_even_if_consumer_abandons_the_stream(self) -> None:
        import time as _time
        from starlette.requests import Request

        def _fake_fetch_task(task_name, symbol, run_id, max_attempts=3):
            return {"symbol": symbol, "task": task_name}

        def _slow_analysis(*_args, **_kwargs):
            # Runs inside run_in_executor's own worker thread (not the event
            # loop), so a real sleep here is safe and gives us a wide,
            # reliable window to abandon the generator's __anext__() call
            # before this "LLM call" finishes -- a real analyst call
            # routinely takes 10s+, this just simulates that on a much
            # shorter, test-friendly timescale.
            _time.sleep(0.2)
            return _fake_analysis("TCS")

        patch("main._fetch_task", side_effect=_fake_fetch_task).start()
        patch("schemas.normalize", side_effect=lambda name, data: data).start()
        patch("schemas.validate", return_value=(True, "")).start()
        patch("signals.engine.run_signal_engine", return_value=_fake_signal_result("TCS")).start()
        patch("signals.store.save_signal").start()
        patch("crew.run_analysis_with_fallback", side_effect=_slow_analysis).start()
        patch("verdict_history.save_snapshot").start()

        scope = {"type": "http", "method": "GET", "headers": [], "client": ("testclient", 12345)}
        request = Request(scope)

        response = await api.analyse("TCS", request, force=False)
        gen = response.body_iterator

        # Drive the generator through "start", 6x "task_done", and
        # "analysing" -- the events emitted BEFORE asyncio.create_task(
        # _run_and_signal()) is called.
        for _ in range(8):
            await gen.__anext__()

        # This next __anext__() call resumes the generator past the
        # "analysing" yield, which creates the background task and then
        # suspends the generator on `await asyncio.wait_for(done_q.get(),
        # timeout=15)`. Wrapping it in a short outer timeout (well inside
        # the 0.2s the mocked "LLM call" takes) and abandoning it there
        # injects a CancelledError at that exact suspension point --
        # simulating the client disconnecting at that moment -- while
        # leaving the already-created background task to keep running
        # independently on the event loop, exactly as a real disconnect
        # would.
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(gen.__anext__(), timeout=0.05)

        # Give the background task (already scheduled before the
        # cancellation above) time to actually finish.
        for _ in range(20):
            await asyncio.sleep(0.05)
            if cache.load("TCS", "analysis"):
                break

        cached = cache.load("TCS", "analysis")
        self.assertIsNotNone(cached, "analysis result was not persisted despite the background task completing")
        self.assertEqual(cached.get("recommendation"), "BUY")


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

    def test_cache_miss_without_force_also_acquires_the_single_flight_lock(self) -> None:
        # Regression test: previously the single-flight lock was only ever
        # acquired inside the `if force:` branch — a cold/expired cache (no
        # ?force=true at all) reached the full pipeline run with the lock
        # never even attempted, so several concurrent visitors hitting a
        # cold cache could each launch a full, expensive duplicate pipeline
        # run. This confirms an ordinary cache-miss request now acquires
        # and cleanly releases the same lock as an explicit ?force=true.
        picks = [{"symbol": "TCS", "confidence_score": 80}]
        with patch("market_picks_pipeline.MarketPicksPipeline", self._fake_pipeline(picks, healthy=True)):
            resp = client.get("/api/market-picks")

        events = _parse_sse(resp.text)
        self.assertEqual(events[-1]["event"], "done")
        self.assertFalse(events[-1]["from_cache"])
        self.assertFalse(rate_limiter.is_locked("market_picks_refresh"))

    def test_concurrent_cache_miss_without_force_is_rejected_not_double_run(self) -> None:
        # The other half of the same regression: with the lock already
        # held (simulating a concurrent request that got there first), a
        # second cache-miss request must be rejected with a clear SSE error
        # rather than silently launching its own redundant pipeline run.
        self.assertTrue(rate_limiter.try_acquire_lock("market_picks_refresh", 60))
        self.addCleanup(rate_limiter.release_lock, "market_picks_refresh")

        with patch("market_picks_pipeline.MarketPicksPipeline") as mock_pipeline_cls:
            resp = client.get("/api/market-picks")

        events = _parse_sse(resp.text)
        self.assertEqual(events[-1]["event"], "error")
        self.assertIn("already in progress", events[-1]["message"].lower())
        mock_pipeline_cls.assert_not_called()

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
