import os
import unittest
from unittest.mock import MagicMock, patch

from core import redis_client


class _RedisClientStateResetMixin:
    def setUp(self) -> None:
        redis_client._redis_client = None
        redis_client._redis_client_construction_failed = False
        self._redis_url = os.environ.pop("REDIS_URL", None)

    def tearDown(self) -> None:
        redis_client._redis_client = None
        redis_client._redis_client_construction_failed = False
        if self._redis_url is not None:
            os.environ["REDIS_URL"] = self._redis_url
        else:
            os.environ.pop("REDIS_URL", None)


class GetRedisClientTest(_RedisClientStateResetMixin, unittest.TestCase):
    """core/cache.py and core/rate_limiter.py each used to carry their own
    copy of this test coverage against their own private _get_redis_client();
    the actual logic now lives here, so this exercises it directly. core/cache.py
    in particular previously had no direct construction-failure test of its
    own at all — this closes that gap for both callers at once."""

    def test_returns_none_without_redis_url(self) -> None:
        self.assertIsNone(redis_client.get_redis_client())

    def test_constructs_client_lazily_once(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        with patch("redis.from_url") as from_url:
            fake = MagicMock()
            from_url.return_value = fake
            first = redis_client.get_redis_client()
            second = redis_client.get_redis_client()
        self.assertIs(first, fake)
        self.assertIs(second, fake)
        from_url.assert_called_once()

    def test_construction_failure_degrades_to_none_not_raise(self) -> None:
        # Regression test: redis.from_url() previously wasn't wrapped in
        # try/except, unlike every other Redis call site in this module —
        # a malformed REDIS_URL made every rate-limited endpoint 500 for
        # the life of the process instead of degrading gracefully.
        os.environ["REDIS_URL"] = "not-a-valid-redis-url"
        with patch("redis.from_url", side_effect=ValueError("bad scheme")):
            client = redis_client.get_redis_client()  # must not raise
        self.assertIsNone(client)

    def test_construction_failure_is_remembered_not_retried(self) -> None:
        os.environ["REDIS_URL"] = "not-a-valid-redis-url"
        with patch("redis.from_url", side_effect=ValueError("bad scheme")) as from_url:
            redis_client.get_redis_client()
            redis_client.get_redis_client()
        from_url.assert_called_once()


class WarnRedisFailureTest(_RedisClientStateResetMixin, unittest.TestCase):
    def test_logs_a_warning_event(self) -> None:
        with patch("core.redis_client.log_event") as mock_log_event:
            redis_client.warn_redis_failure("some_event", ValueError("boom"))
        mock_log_event.assert_called_once_with(
            redis_client.LOGGER, "some_event", level="warning", error="boom"
        )


if __name__ == "__main__":
    unittest.main()
