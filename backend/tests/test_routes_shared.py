import asyncio
import unittest

from fastapi import HTTPException

import api  # noqa: F401  -- import before routes._shared to avoid a circular
# import: routes/_shared.py itself does `import api`, and api.py imports
# routes/watchlist.py, which imports names from routes/_shared.py -- if
# routes/_shared is the very first of these three modules to start
# importing, that watchlist-> _shared import happens while _shared is still
# mid-initialization and fails. Every other test module that touches
# routes/_shared goes through `import api` first for the same reason.
from routes._shared import read_upload_capped


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


if __name__ == "__main__":
    unittest.main()
