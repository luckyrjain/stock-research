import os
import unittest
from unittest.mock import MagicMock, patch

import rate_limiter


class _MemoryStateResetMixin:
    def setUp(self) -> None:
        rate_limiter._memory_calls.clear()
        rate_limiter._memory_slots.clear()
        rate_limiter._memory_locks.clear()
        rate_limiter._redis_client = None
        self._redis_url = os.environ.pop("REDIS_URL", None)

    def tearDown(self) -> None:
        rate_limiter._memory_calls.clear()
        rate_limiter._memory_slots.clear()
        rate_limiter._memory_locks.clear()
        rate_limiter._redis_client = None
        if self._redis_url is not None:
            os.environ["REDIS_URL"] = self._redis_url
        else:
            os.environ.pop("REDIS_URL", None)


class GetRedisClientTest(_MemoryStateResetMixin, unittest.TestCase):
    def test_returns_none_without_redis_url(self) -> None:
        self.assertIsNone(rate_limiter._get_redis_client())

    def test_constructs_client_lazily_once(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        with patch("redis.from_url") as from_url:
            fake = MagicMock()
            from_url.return_value = fake
            first = rate_limiter._get_redis_client()
            second = rate_limiter._get_redis_client()
        self.assertIs(first, fake)
        self.assertIs(second, fake)
        from_url.assert_called_once()


class IsAllowedMemoryTest(_MemoryStateResetMixin, unittest.TestCase):
    def test_allows_up_to_max_then_blocks(self) -> None:
        with patch("time.monotonic", return_value=1000.0):
            for _ in range(3):
                self.assertTrue(rate_limiter.is_allowed("k", max_calls=3, window_seconds=60))
            self.assertFalse(rate_limiter.is_allowed("k", max_calls=3, window_seconds=60))

    def test_window_expiry_allows_again(self) -> None:
        with patch("time.monotonic", return_value=1000.0):
            for _ in range(2):
                rate_limiter.is_allowed("k", max_calls=2, window_seconds=10)
            self.assertFalse(rate_limiter.is_allowed("k", max_calls=2, window_seconds=10))
        with patch("time.monotonic", return_value=1011.0):
            self.assertTrue(rate_limiter.is_allowed("k", max_calls=2, window_seconds=10))

    def test_different_keys_are_independent(self) -> None:
        with patch("time.monotonic", return_value=1000.0):
            rate_limiter.is_allowed("a", max_calls=1, window_seconds=60)
            self.assertFalse(rate_limiter.is_allowed("a", max_calls=1, window_seconds=60))
            self.assertTrue(rate_limiter.is_allowed("b", max_calls=1, window_seconds=60))


class IsAllowedRedisTest(_MemoryStateResetMixin, unittest.TestCase):
    def test_uses_redis_when_available(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        fake_client = MagicMock()
        fake_client.eval.return_value = 1
        with patch("rate_limiter._get_redis_client", return_value=fake_client):
            allowed = rate_limiter.is_allowed("k", max_calls=5, window_seconds=60)
        self.assertTrue(allowed)
        fake_client.eval.assert_called_once()

    def test_redis_rejection_returns_false(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        fake_client = MagicMock()
        fake_client.eval.return_value = 0
        with patch("rate_limiter._get_redis_client", return_value=fake_client):
            self.assertFalse(rate_limiter.is_allowed("k", max_calls=5, window_seconds=60))

    def test_falls_back_to_memory_on_redis_error(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        fake_client = MagicMock()
        fake_client.eval.side_effect = ConnectionError("boom")
        with patch("rate_limiter._get_redis_client", return_value=fake_client), \
             patch("time.monotonic", return_value=1000.0):
            self.assertTrue(rate_limiter.is_allowed("k", max_calls=1, window_seconds=60))
            self.assertFalse(rate_limiter.is_allowed("k", max_calls=1, window_seconds=60))


class SlotConcurrencyMemoryTest(_MemoryStateResetMixin, unittest.TestCase):
    def test_acquire_up_to_limit_then_rejects(self) -> None:
        self.assertTrue(rate_limiter.try_acquire_slot("s", limit=2))
        self.assertTrue(rate_limiter.try_acquire_slot("s", limit=2))
        self.assertFalse(rate_limiter.try_acquire_slot("s", limit=2))

    def test_release_frees_a_slot(self) -> None:
        rate_limiter.try_acquire_slot("s", limit=1)
        self.assertFalse(rate_limiter.try_acquire_slot("s", limit=1))
        rate_limiter.release_slot("s")
        self.assertTrue(rate_limiter.try_acquire_slot("s", limit=1))

    def test_release_below_zero_is_clamped(self) -> None:
        rate_limiter.release_slot("s")
        rate_limiter.release_slot("s")
        self.assertEqual(rate_limiter._memory_slots.get("s", 0), 0)

    def test_named_slots_are_independent(self) -> None:
        rate_limiter.try_acquire_slot("a", limit=1)
        self.assertFalse(rate_limiter.try_acquire_slot("a", limit=1))
        self.assertTrue(rate_limiter.try_acquire_slot("b", limit=1))


class SlotConcurrencyRedisTest(_MemoryStateResetMixin, unittest.TestCase):
    def test_acquire_success_calls_redis(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        fake_client = MagicMock()
        fake_client.eval.return_value = 1
        with patch("rate_limiter._get_redis_client", return_value=fake_client):
            self.assertTrue(rate_limiter.try_acquire_slot("llm", limit=4))
        fake_client.eval.assert_called_once()

    def test_acquire_rejected_by_redis(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        fake_client = MagicMock()
        fake_client.eval.return_value = 0
        with patch("rate_limiter._get_redis_client", return_value=fake_client):
            self.assertFalse(rate_limiter.try_acquire_slot("llm", limit=4))

    def test_release_calls_redis(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        fake_client = MagicMock()
        with patch("rate_limiter._get_redis_client", return_value=fake_client):
            rate_limiter.release_slot("llm")
        fake_client.eval.assert_called_once()

    def test_acquire_falls_back_to_memory_on_redis_error(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        fake_client = MagicMock()
        fake_client.eval.side_effect = ConnectionError("boom")
        with patch("rate_limiter._get_redis_client", return_value=fake_client):
            self.assertTrue(rate_limiter.try_acquire_slot("llm", limit=1))
            self.assertFalse(rate_limiter.try_acquire_slot("llm", limit=1))

    def test_release_falls_back_to_memory_on_redis_error(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        rate_limiter._memory_slots["llm"] = 1
        fake_client = MagicMock()
        fake_client.eval.side_effect = ConnectionError("boom")
        with patch("rate_limiter._get_redis_client", return_value=fake_client):
            rate_limiter.release_slot("llm")
        self.assertEqual(rate_limiter._memory_slots["llm"], 0)


class LockMemoryTest(_MemoryStateResetMixin, unittest.TestCase):
    def test_acquire_then_second_caller_rejected(self) -> None:
        self.assertTrue(rate_limiter.try_acquire_lock("job", ttl_seconds=60))
        self.assertFalse(rate_limiter.try_acquire_lock("job", ttl_seconds=60))

    def test_release_allows_reacquire(self) -> None:
        rate_limiter.try_acquire_lock("job", ttl_seconds=60)
        rate_limiter.release_lock("job")
        self.assertTrue(rate_limiter.try_acquire_lock("job", ttl_seconds=60))

    def test_is_locked_reflects_state(self) -> None:
        self.assertFalse(rate_limiter.is_locked("job"))
        rate_limiter.try_acquire_lock("job", ttl_seconds=60)
        self.assertTrue(rate_limiter.is_locked("job"))
        rate_limiter.release_lock("job")
        self.assertFalse(rate_limiter.is_locked("job"))


class LockRedisTest(_MemoryStateResetMixin, unittest.TestCase):
    def test_acquire_uses_set_nx(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        fake_client = MagicMock()
        fake_client.set.return_value = True
        with patch("rate_limiter._get_redis_client", return_value=fake_client):
            self.assertTrue(rate_limiter.try_acquire_lock("job", ttl_seconds=60))
        _, kwargs = fake_client.set.call_args
        self.assertTrue(kwargs.get("nx"))
        self.assertEqual(kwargs.get("ex"), 60)

    def test_acquire_rejected_when_set_returns_falsy(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        fake_client = MagicMock()
        fake_client.set.return_value = None
        with patch("rate_limiter._get_redis_client", return_value=fake_client):
            self.assertFalse(rate_limiter.try_acquire_lock("job", ttl_seconds=60))

    def test_release_deletes_key(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        fake_client = MagicMock()
        with patch("rate_limiter._get_redis_client", return_value=fake_client):
            rate_limiter.release_lock("job")
        fake_client.delete.assert_called_once()

    def test_is_locked_uses_exists(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        fake_client = MagicMock()
        fake_client.exists.return_value = 1
        with patch("rate_limiter._get_redis_client", return_value=fake_client):
            self.assertTrue(rate_limiter.is_locked("job"))

    def test_acquire_falls_back_to_memory_on_redis_error(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        fake_client = MagicMock()
        fake_client.set.side_effect = ConnectionError("boom")
        with patch("rate_limiter._get_redis_client", return_value=fake_client):
            self.assertTrue(rate_limiter.try_acquire_lock("job", ttl_seconds=60))
            self.assertFalse(rate_limiter.try_acquire_lock("job", ttl_seconds=60))

    def test_is_locked_falls_back_to_memory_on_redis_error(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        rate_limiter._memory_locks["job"] = True
        fake_client = MagicMock()
        fake_client.exists.side_effect = ConnectionError("boom")
        with patch("rate_limiter._get_redis_client", return_value=fake_client):
            self.assertTrue(rate_limiter.is_locked("job"))


if __name__ == "__main__":
    unittest.main()
