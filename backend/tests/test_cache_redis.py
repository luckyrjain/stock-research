import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import cache


class _CacheRedisTestBase(unittest.TestCase):
    """Shared setup for every Redis-backed cache test: a scratch disk
    directory (same convention as test_cache_failures.py) plus a reset of
    cache.py's own lazily-constructed Redis client state, mirroring
    tests/test_rate_limiter.py's _MemoryStateResetMixin for the equivalent
    module-level Redis client cache in rate_limiter.py."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-cache-redis-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self.cache_dir = Path(self._tmpdir)
        self._cache_dir_patch = patch.object(cache, "CACHE_DIR", self.cache_dir)
        self._cache_dir_patch.start()
        self.addCleanup(self._cache_dir_patch.stop)

        self._redis_url = os.environ.pop("REDIS_URL", None)
        cache._redis_client = None
        cache._redis_client_construction_failed = False

    def tearDown(self) -> None:
        cache._redis_client = None
        cache._redis_client_construction_failed = False
        if self._redis_url is not None:
            os.environ["REDIS_URL"] = self._redis_url
        else:
            os.environ.pop("REDIS_URL", None)

    def _fresh_payload(self, symbol: str, **extra) -> dict:
        return {
            "symbol": symbol,
            "_meta": {"fetched_at": datetime.now(timezone.utc).isoformat()},
            **extra,
        }

    def _stale_payload(self, symbol: str, task_name: str, **extra) -> dict:
        return {
            "symbol": symbol,
            "_meta": {
                "fetched_at": (
                    datetime.now(timezone.utc) - timedelta(hours=cache.TTL_HOURS[task_name] + 1)
                ).isoformat(),
            },
            **extra,
        }


class SaveWritesThroughToRedisTest(_CacheRedisTestBase):
    def test_save_writes_to_redis_and_disk_when_configured(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        fake_client = MagicMock()

        with patch("cache._get_redis_client", return_value=fake_client):
            cache.save("TCS", "news", {"symbol": "TCS", "articles": []})

        fake_client.set.assert_called_once()
        (redis_key, serialized), kwargs = fake_client.set.call_args
        self.assertEqual(redis_key, "cache:TCS:news")
        self.assertEqual(kwargs["ex"], int(cache.TTL_HOURS["news"] * 3600))
        self.assertEqual(json.loads(serialized)["symbol"], "TCS")
        # Disk is still written even though Redis is configured -- the
        # persistent store when REDIS_URL is unset, a local mirror when set.
        self.assertTrue(cache.cache_path("TCS", "news").exists())

    def test_failed_payload_is_never_written_to_redis(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        fake_client = MagicMock()

        with patch("cache._get_redis_client", return_value=fake_client):
            cache.save("TCS", "news", {"error": "temporary upstream failure", "symbol": "TCS"})

        fake_client.set.assert_not_called()
        self.assertFalse(cache.cache_path("TCS", "news").exists())

    def test_redis_write_failure_does_not_prevent_the_disk_write(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        fake_client = MagicMock()
        fake_client.set.side_effect = ConnectionError("redis unreachable")

        with patch("cache._get_redis_client", return_value=fake_client):
            cache.save("TCS", "news", {"symbol": "TCS", "articles": []})  # must not raise

        self.assertTrue(cache.cache_path("TCS", "news").exists())
        loaded = json.loads(cache.cache_path("TCS", "news").read_text())
        self.assertEqual(loaded["symbol"], "TCS")

    def test_without_redis_url_behaves_exactly_like_before(self) -> None:
        # REDIS_URL is already unset by setUp() -- no patch needed here.
        cache.save("TCS", "news", {"symbol": "TCS", "articles": []})
        self.assertTrue(cache.cache_path("TCS", "news").exists())


class LoadPrefersRedisTest(_CacheRedisTestBase):
    def test_fresh_redis_entry_is_returned_without_touching_disk(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        fake_client = MagicMock()
        fake_client.get.return_value = json.dumps(self._fresh_payload("TCS", articles=[{"title": "from redis"}]))

        with patch("cache._get_redis_client", return_value=fake_client):
            result = cache.load("TCS", "news")

        self.assertIsNotNone(result)
        self.assertEqual(result["articles"][0]["title"], "from redis")
        self.assertFalse(cache.cache_path("TCS", "news").exists())  # disk was never consulted or written

    def test_stale_redis_entry_returns_none_even_if_disk_has_a_fresher_copy(self) -> None:
        # The core cross-host-consistency guarantee: once Redis has an
        # opinion on a key, every host must agree with it, even if this
        # particular host's own disk happens to have something newer.
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        cache.cache_path("TCS", "news").parent.mkdir(parents=True, exist_ok=True)
        cache.cache_path("TCS", "news").write_text(json.dumps(self._fresh_payload("TCS", articles=[])))

        fake_client = MagicMock()
        fake_client.get.return_value = json.dumps(self._stale_payload("TCS", "news", articles=[]))

        with patch("cache._get_redis_client", return_value=fake_client):
            result = cache.load("TCS", "news")

        self.assertIsNone(result)

    def test_failed_redis_entry_returns_none_without_falling_back_to_disk(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        cache.cache_path("TCS", "news").parent.mkdir(parents=True, exist_ok=True)
        cache.cache_path("TCS", "news").write_text(json.dumps(self._fresh_payload("TCS", articles=[])))

        fake_client = MagicMock()
        fake_client.get.return_value = json.dumps({"error": "boom", "symbol": "TCS"})

        with patch("cache._get_redis_client", return_value=fake_client):
            result = cache.load("TCS", "news")

        self.assertIsNone(result)

    def test_redis_miss_falls_back_to_disk(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        cache.cache_path("TCS", "news").parent.mkdir(parents=True, exist_ok=True)
        cache.cache_path("TCS", "news").write_text(json.dumps(self._fresh_payload("TCS", articles=[{"title": "from disk"}])))

        fake_client = MagicMock()
        fake_client.get.return_value = None

        with patch("cache._get_redis_client", return_value=fake_client):
            result = cache.load("TCS", "news")

        self.assertIsNotNone(result)
        self.assertEqual(result["articles"][0]["title"], "from disk")

    def test_redis_read_failure_falls_back_to_disk(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        cache.cache_path("TCS", "news").parent.mkdir(parents=True, exist_ok=True)
        cache.cache_path("TCS", "news").write_text(json.dumps(self._fresh_payload("TCS", articles=[{"title": "from disk"}])))

        fake_client = MagicMock()
        fake_client.get.side_effect = ConnectionError("redis unreachable")

        with patch("cache._get_redis_client", return_value=fake_client):
            result = cache.load("TCS", "news")  # must not raise

        self.assertIsNotNone(result)
        self.assertEqual(result["articles"][0]["title"], "from disk")


class IsFreshPrefersRedisTest(_CacheRedisTestBase):
    def test_fresh_redis_entry_is_fresh(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        fake_client = MagicMock()
        fake_client.get.return_value = json.dumps(self._fresh_payload("TCS"))

        with patch("cache._get_redis_client", return_value=fake_client):
            self.assertTrue(cache.is_fresh("TCS", "news"))

    def test_stale_redis_entry_is_not_fresh_even_with_a_fresh_disk_copy(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        cache.cache_path("TCS", "news").parent.mkdir(parents=True, exist_ok=True)
        cache.cache_path("TCS", "news").write_text(json.dumps(self._fresh_payload("TCS")))

        fake_client = MagicMock()
        fake_client.get.return_value = json.dumps(self._stale_payload("TCS", "news"))

        with patch("cache._get_redis_client", return_value=fake_client):
            self.assertFalse(cache.is_fresh("TCS", "news"))

    def test_redis_miss_falls_back_to_disk(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        cache.cache_path("TCS", "news").parent.mkdir(parents=True, exist_ok=True)
        cache.cache_path("TCS", "news").write_text(json.dumps(self._fresh_payload("TCS")))

        fake_client = MagicMock()
        fake_client.get.return_value = None

        with patch("cache._get_redis_client", return_value=fake_client):
            self.assertTrue(cache.is_fresh("TCS", "news"))


if __name__ == "__main__":
    unittest.main()
