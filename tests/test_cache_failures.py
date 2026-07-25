import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import cache
import schemas


class CacheFailureHandlingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-cache-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self.cache_dir = Path(self._tmpdir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _write_cache_file(self, symbol: str, task_name: str, payload: dict | str) -> Path:
        path = self.cache_dir / symbol.upper() / f"{task_name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_successful_payload_is_saved_and_treated_as_fresh(self) -> None:
        with patch.object(cache, "CACHE_DIR", self.cache_dir):
            cache.save("TCS", "news", {"symbol": "TCS", "articles": [{"title": "OK"}]})

            path = cache.cache_path("TCS", "news")
            self.assertTrue(path.exists())

            loaded = cache.load("TCS", "news")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["symbol"], "TCS")
            self.assertTrue(cache.is_fresh("TCS", "news"))

    def test_failed_payload_is_not_saved(self) -> None:
        with patch.object(cache, "CACHE_DIR", self.cache_dir):
            cache.save("TCS", "news", {"error": "temporary upstream failure", "symbol": "TCS"})

            self.assertFalse(cache.cache_path("TCS", "news").exists())
            self.assertIsNone(cache.load("TCS", "news"))
            self.assertFalse(cache.is_fresh("TCS", "news"))

    def test_degraded_payload_is_not_saved(self) -> None:
        # A safe-fallback analysis (e.g. crew._safe_analysis_fallback) has no "error"
        # key but must still be excluded from the cache, or it gets served as a
        # genuine 24h-fresh result — see the analysis-cache-poisoning fix.
        with patch.object(cache, "CACHE_DIR", self.cache_dir):
            cache.save("TCS", "analysis", {"symbol": "TCS", "recommendation": "HOLD", "_degraded": True})

            self.assertFalse(cache.cache_path("TCS", "analysis").exists())
            self.assertIsNone(cache.load("TCS", "analysis"))
            self.assertFalse(cache.is_fresh("TCS", "analysis"))

    def test_cached_failed_payload_is_treated_as_stale(self) -> None:
        failed_payload = {
            "error": "temporary upstream failure",
            "symbol": "TCS",
            "_meta": {"fetched_at": datetime.now(timezone.utc).isoformat()},
        }

        with patch.object(cache, "CACHE_DIR", self.cache_dir):
            self._write_cache_file("TCS", "news", failed_payload)

            self.assertIsNone(cache.load("TCS", "news"))
            self.assertFalse(cache.is_fresh("TCS", "news"))

    def test_payload_without_timestamp_is_treated_as_stale(self) -> None:
        with patch.object(cache, "CACHE_DIR", self.cache_dir):
            self._write_cache_file("TCS", "news", {"symbol": "TCS", "articles": []})

            self.assertIsNone(cache.load("TCS", "news"))
            self.assertFalse(cache.is_fresh("TCS", "news"))

    def test_malformed_cache_file_is_treated_as_stale(self) -> None:
        with patch.object(cache, "CACHE_DIR", self.cache_dir):
            self._write_cache_file("TCS", "news", "{not-valid-json")

            self.assertIsNone(cache.load("TCS", "news"))
            self.assertFalse(cache.is_fresh("TCS", "news"))

    def test_expired_payload_is_treated_as_stale(self) -> None:
        stale_payload = {
            "symbol": "TCS",
            "articles": [{"title": "Old"}],
            "_meta": {
                "fetched_at": (
                    datetime.now(timezone.utc)
                    - timedelta(hours=cache.TTL_HOURS["news"] + 1)
                ).isoformat()
            },
        }

        with patch.object(cache, "CACHE_DIR", self.cache_dir):
            self._write_cache_file("TCS", "news", stale_payload)

            self.assertIsNone(cache.load("TCS", "news"))
            self.assertFalse(cache.is_fresh("TCS", "news"))

    def test_save_writes_atomically_leaving_no_temp_files_behind(self) -> None:
        # save() writes via a tempfile + os.replace rather than a direct
        # write_text — this matters for the market-wide "_MACRO" pseudo-symbol
        # (see signals/macro.py), where several ThreadPoolExecutor workers can
        # race to fill the same cache entry; a direct write_text's interleaved
        # writes could otherwise leave invalid JSON on disk for the next reader.
        with patch.object(cache, "CACHE_DIR", self.cache_dir):
            cache.save("TCS", "news", {"symbol": "TCS", "articles": []})

            symbol_dir = self.cache_dir / "TCS"
            leftover_temp_files = [p for p in symbol_dir.iterdir() if p.name.endswith(".tmp")]
            self.assertEqual(leftover_temp_files, [])
            self.assertTrue(cache.cache_path("TCS", "news").exists())

    def test_save_cleans_up_temp_file_on_write_failure(self) -> None:
        with patch.object(cache, "CACHE_DIR", self.cache_dir), \
             patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                cache.save("TCS", "news", {"symbol": "TCS", "articles": []})

            symbol_dir = self.cache_dir / "TCS"
            leftover_temp_files = list(symbol_dir.iterdir()) if symbol_dir.exists() else []
            self.assertEqual(leftover_temp_files, [])

    def test_normalize_preserves_error_payloads_for_cache_guard(self) -> None:
        payload = {"error": "temporary upstream failure", "symbol": "TCS"}

        self.assertEqual(schemas.normalize("news", payload), payload)

    def test_transient_task_failure_is_retried_on_next_run(self) -> None:
        symbol = "TCS"
        task_name = "news"
        attempts: list[str] = []

        def fetch_task(sym: str, task: str) -> dict:
            attempts.append(task)
            if len(attempts) == 1:
                return {"error": "temporary upstream failure", "symbol": sym}
            return {
                "symbol": sym,
                "articles": [{"title": "Recovered", "url": "https://example.com"}],
            }

        def run_once() -> dict | None:
            if cache.is_fresh(symbol, task_name):
                return cache.load(symbol, task_name)

            result = fetch_task(symbol, task_name)
            normalized = schemas.normalize(task_name, result)
            cache.save(symbol, task_name, normalized)
            return normalized

        with patch.object(cache, "CACHE_DIR", self.cache_dir):
            first = run_once()
            self.assertEqual(first["error"], "temporary upstream failure")
            self.assertFalse(cache.cache_path(symbol, task_name).exists())
            self.assertFalse(cache.is_fresh(symbol, task_name))

            second = run_once()
            self.assertEqual(len(attempts), 2)
            self.assertEqual(second["articles"][0]["title"], "Recovered")
            self.assertTrue(cache.is_fresh(symbol, task_name))
            self.assertIsNotNone(cache.load(symbol, task_name))


if __name__ == "__main__":
    unittest.main()
