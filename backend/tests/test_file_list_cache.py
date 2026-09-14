import json
import logging
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.file_list_cache import is_fresh, load_json, save_json


class TestFileListCache(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "sub" / "cache.json"

    def test_is_fresh_missing_file(self):
        self.assertFalse(is_fresh(self.path, ttl_hours=24))

    def test_is_fresh_recent_write(self):
        save_json(self.path, [1, 2, 3])
        self.assertTrue(is_fresh(self.path, ttl_hours=24))

    def test_is_fresh_stale_by_mtime(self):
        save_json(self.path, [1, 2, 3])
        old = time.time() - 25 * 3600  # older than a 24h TTL
        os.utime(self.path, (old, old))
        self.assertFalse(is_fresh(self.path, ttl_hours=24))

    def test_save_then_load_round_trip(self):
        data = [{"symbol": "TCS"}, {"symbol": "INFY"}]
        save_json(self.path, data)
        self.assertEqual(load_json(self.path), data)

    def test_load_missing_file_returns_none(self):
        self.assertIsNone(load_json(self.path))

    def test_load_corrupt_file_returns_none(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not valid json")
        self.assertIsNone(load_json(self.path))

    def test_load_corrupt_file_logs_when_logger_given(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not valid json")
        logger = logging.getLogger("test_file_list_cache")
        with self.assertLogs(logger, level="WARNING") as cm:
            result = load_json(self.path, logger=logger, log_label="Widget cache")
        self.assertIsNone(result)
        self.assertIn("Widget cache", cm.output[0])


if __name__ == "__main__":
    unittest.main()
