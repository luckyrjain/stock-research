import os
import unittest
import uuid
from unittest.mock import MagicMock, patch

import rate_limiter


class _MemoryStateResetMixin:
    def setUp(self) -> None:
        rate_limiter._memory_calls.clear()
        rate_limiter._memory_slots.clear()
        rate_limiter._memory_locks.clear()
        rate_limiter._redis_client = None
        rate_limiter._redis_client_construction_failed = False
        self._redis_url = os.environ.pop("REDIS_URL", None)

    def tearDown(self) -> None:
        rate_limiter._memory_calls.clear()
        rate_limiter._memory_slots.clear()
        rate_limiter._memory_locks.clear()
        rate_limiter._redis_client = None
        rate_limiter._redis_client_construction_failed = False
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

    def test_construction_failure_degrades_to_none_not_raise(self) -> None:
        # Regression test: redis.from_url() previously wasn't wrapped in
        # try/except, unlike every other Redis call site in this module —
        # a malformed REDIS_URL made every rate-limited endpoint 500 for
        # the life of the process instead of degrading gracefully.
        os.environ["REDIS_URL"] = "not-a-valid-redis-url"
        with patch("redis.from_url", side_effect=ValueError("bad scheme")):
            client = rate_limiter._get_redis_client()  # must not raise
        self.assertIsNone(client)

    def test_construction_failure_is_remembered_not_retried(self) -> None:
        os.environ["REDIS_URL"] = "not-a-valid-redis-url"
        with patch("redis.from_url", side_effect=ValueError("bad scheme")) as from_url:
            rate_limiter._get_redis_client()
            rate_limiter._get_redis_client()
        from_url.assert_called_once()

    def test_construction_failure_falls_back_to_memory_rate_limit(self) -> None:
        # End-to-end: is_allowed() must still work via the in-memory path
        # when Redis client construction itself blows up, not just
        # _get_redis_client() in isolation.
        os.environ["REDIS_URL"] = "not-a-valid-redis-url"
        with patch("redis.from_url", side_effect=ValueError("bad scheme")):
            self.assertTrue(rate_limiter.is_allowed("k", max_calls=1, window_seconds=60))
            self.assertFalse(rate_limiter.is_allowed("k", max_calls=1, window_seconds=60))


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
        fixed_uuid = uuid.UUID(int=0)
        with patch("rate_limiter._get_redis_client", return_value=fake_client), \
             patch("time.time", return_value=1234.5), \
             patch("uuid.uuid4", return_value=fixed_uuid):
            allowed = rate_limiter.is_allowed("k", max_calls=5, window_seconds=60)
        self.assertTrue(allowed)
        fake_client.eval.assert_called_once_with(
            rate_limiter._SLIDING_WINDOW_SCRIPT, 1, "ratelimit:k", 1234.5, 60, 5, f"1234.5:{fixed_uuid.hex}",
        )

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


class GetUsageCountMemoryTest(_MemoryStateResetMixin, unittest.TestCase):
    def test_reports_count_without_counting_as_a_call(self) -> None:
        with patch("time.monotonic", return_value=1000.0):
            rate_limiter.is_allowed("k", max_calls=10, window_seconds=60)
            rate_limiter.is_allowed("k", max_calls=10, window_seconds=60)
            # A peek must not itself add a call — asking twice reports the
            # same count both times.
            self.assertEqual(rate_limiter.get_usage_count("k", 60), 2)
            self.assertEqual(rate_limiter.get_usage_count("k", 60), 2)

    def test_zero_for_unseen_key(self) -> None:
        self.assertEqual(rate_limiter.get_usage_count("never-called", 60), 0)

    def test_window_expiry_drops_stale_calls(self) -> None:
        with patch("time.monotonic", return_value=1000.0):
            rate_limiter.is_allowed("k", max_calls=10, window_seconds=10)
        with patch("time.monotonic", return_value=1011.0):
            self.assertEqual(rate_limiter.get_usage_count("k", 10), 0)


class GetUsageCountRedisTest(_MemoryStateResetMixin, unittest.TestCase):
    def test_uses_redis_when_available(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        fake_client = MagicMock()
        fake_client.eval.return_value = 3
        with patch("rate_limiter._get_redis_client", return_value=fake_client), \
             patch("time.time", return_value=1234.5):
            count = rate_limiter.get_usage_count("k", 60)
        self.assertEqual(count, 3)
        fake_client.eval.assert_called_once_with(
            rate_limiter._USAGE_COUNT_SCRIPT, 1, "ratelimit:k", 1234.5, 60,
        )

    def test_falls_back_to_memory_on_redis_error(self) -> None:
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        fake_client = MagicMock()
        fake_client.eval.side_effect = ConnectionError("boom")
        with patch("rate_limiter._get_redis_client", return_value=fake_client), \
             patch("time.monotonic", return_value=1000.0):
            rate_limiter._is_allowed_memory("k", max_calls=10, window_seconds=60)
            self.assertEqual(rate_limiter.get_usage_count("k", 60), 1)


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
        fake_client.eval.assert_called_once_with(
            rate_limiter._ACQUIRE_SLOT_SCRIPT, 1, "slot:llm", 4, rate_limiter._SLOT_TTL_SECONDS,
        )

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
        fake_client.eval.assert_called_once_with(rate_limiter._RELEASE_SLOT_SCRIPT, 1, "slot:llm")

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

    def test_acquire_script_refreshes_ttl_on_every_call_not_just_the_first(self) -> None:
        # Regression guard: an earlier version only called EXPIRE when
        # count == 1 (i.e. only on the call that created the key), so a key
        # under sustained traffic could expire out from under slots that
        # were still legitimately held, silently resetting the counter and
        # letting the concurrency ceiling be exceeded. EXPIRE must now fire
        # unconditionally on both the accept and reject paths — not gated
        # behind a "was this the very first acquire" check.
        script = rate_limiter._ACQUIRE_SLOT_SCRIPT
        self.assertNotIn("count == 1", script)
        self.assertEqual(script.count("EXPIRE"), 2)  # once on reject, once on accept


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

    def test_is_locked_fails_closed_on_redis_error_even_when_memory_was_never_populated(self) -> None:
        # Regression test for an adversarial-review finding: is_locked() used
        # to fall back to _memory_locks on a Redis read failure -- but
        # _memory_locks is only ever populated by try_acquire_lock()/
        # release_lock()'s OWN fallback path, which never runs when those
        # calls succeeded via Redis. A lock genuinely acquired via Redis (as
        # here -- _memory_locks is deliberately left untouched, matching
        # that real scenario) followed by a transient failure on the is_locked()
        # peek itself used to silently report "not locked" (False, from the
        # never-populated dict) while the lock was genuinely still held in
        # Redis. Must fail closed (True) instead.
        fake_client = MagicMock()
        fake_client.exists.side_effect = ConnectionError("boom")
        with patch("rate_limiter._get_redis_client", return_value=fake_client):
            self.assertTrue(rate_limiter.is_locked("job"))

    def test_is_locked_uses_memory_state_during_a_sustained_redis_outage(self) -> None:
        # Regression test for an adversarial-review finding: unlike the
        # test above (where a lock genuinely acquired via Redis left
        # _memory_locks untouched, so failing closed was correct),
        # is_locked() used to fail closed (return True) unconditionally on
        # ANY Redis read failure -- even when Redis has been unreachable
        # since before the very first acquire/release call on this lock,
        # so every prior operation already fell back to _memory_locks and
        # has been correctly tracking real state there the whole time.
        # api.py's "refreshing" status field (GET /api/sme-signals,
        # GET /api/screener) reads is_locked() -- during a sustained Redis
        # outage this used to report "refreshing": true forever, even
        # immediately after a refresh genuinely completed.
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        fake_client = MagicMock()
        fake_client.set.side_effect = ConnectionError("boom")
        fake_client.delete.side_effect = ConnectionError("boom")
        fake_client.exists.side_effect = ConnectionError("boom")
        with patch("rate_limiter._get_redis_client", return_value=fake_client):
            self.assertTrue(rate_limiter.try_acquire_lock("job", ttl_seconds=60))
            self.assertTrue(rate_limiter.is_locked("job"))  # genuinely held (in memory)
            rate_limiter.release_lock("job")
            self.assertFalse(rate_limiter.is_locked("job"))  # genuinely released -- must not still report locked


if __name__ == "__main__":
    unittest.main()
