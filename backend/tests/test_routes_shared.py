import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from routes._shared import rate_limited_upload, read_upload_capped


class _FakeUploadFile:
    """Minimal async-read stand-in for fastapi.UploadFile — only the
    `.read(size)` chunked-read shape read_upload_capped() actually uses.

    Deliberately has no cursor/stream position, unlike a real UploadFile —
    `.read(size)` always slices from byte 0. Safe only because
    read_upload_capped() calls `.read()` exactly once per invocation; if it
    (or any other caller) is ever changed to read in a loop, this fake would
    silently return the same prefix every time instead of advancing, and
    would need real cursor tracking to keep testing the real thing."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self._data
        return self._data[:size]


class ReadUploadCappedTest(unittest.TestCase):
    def test_file_within_limit_is_read_in_full(self) -> None:
        data = b"x" * 100
        result = asyncio.run(read_upload_capped(_FakeUploadFile(data), max_bytes=1000))
        self.assertEqual(result, data)

    def test_file_exactly_at_limit_succeeds(self) -> None:
        data = b"x" * 100
        result = asyncio.run(read_upload_capped(_FakeUploadFile(data), max_bytes=100))
        self.assertEqual(result, data)

    def test_file_over_limit_raises_413(self) -> None:
        # Regression test: routes/portfolio_aggregator.py's CAS/CSV import
        # endpoints used to call `await file.read()` unconditionally,
        # buffering an arbitrarily large request body fully into memory
        # before any parsing could reject it — an unauthenticated
        # memory-exhaustion DoS vector.
        data = b"x" * 101
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(read_upload_capped(_FakeUploadFile(data), max_bytes=100))
        self.assertEqual(ctx.exception.status_code, 413)

    def test_never_buffers_more_than_one_byte_past_the_cap(self) -> None:
        # The underlying file's read() is only ever asked for max_bytes + 1
        # bytes, never the file's true (possibly huge) full size.
        requested_sizes: list[int] = []

        class _RecordingFile(_FakeUploadFile):
            async def read(self, size: int = -1) -> bytes:
                requested_sizes.append(size)
                return await super().read(size)

        asyncio.run(read_upload_capped(_RecordingFile(b"x" * 5), max_bytes=1000))
        self.assertEqual(requested_sizes, [1001])


class RateLimitedUploadTest(unittest.TestCase):
    """rate_limited_upload() couples the rate-limit check to the capped
    read as one call -- see its own docstring for why (an adversarial-
    review finding on the 3 upload endpoints that used to do these as two
    separate statements)."""

    def test_calls_rate_limit_before_reading_the_file(self) -> None:
        # If the rate limit rejects the request, the file must never be
        # read at all -- not read-then-discarded.
        read_calls: list[int] = []

        class _CountingFile(_FakeUploadFile):
            async def read(self, size: int = -1) -> bytes:
                read_calls.append(size)
                return await super().read(size)

        with patch("routes._shared._rate_limit", side_effect=HTTPException(status_code=429)):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(rate_limited_upload(
                    request=object(), rate_limit_name="test_bucket", max_calls=10,
                    file=_CountingFile(b"x" * 5),
                ))
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(read_calls, [])

    def test_reads_the_file_after_rate_limit_passes(self) -> None:
        data = b"hello world"
        fake_request = object()
        with patch("routes._shared._rate_limit") as rate_limit:
            result = asyncio.run(rate_limited_upload(
                request=fake_request, rate_limit_name="test_bucket", max_calls=10,
                file=_FakeUploadFile(data), max_bytes=1000,
            ))
        rate_limit.assert_called_once_with(
            fake_request, "test_bucket", max_calls=10, window_seconds=60)
        self.assertEqual(result, data)

    def test_still_enforces_the_upload_cap_after_rate_limit_passes(self) -> None:
        with patch("routes._shared._rate_limit"):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(rate_limited_upload(
                    request=object(), rate_limit_name="test_bucket", max_calls=10,
                    file=_FakeUploadFile(b"x" * 101), max_bytes=100,
                ))
        self.assertEqual(ctx.exception.status_code, 413)


if __name__ == "__main__":
    unittest.main()
