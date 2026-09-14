import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.atomic_file import atomic_write_text


class AtomicWriteTextTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="stock-research-atomic-file-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self.dir = Path(self._tmpdir)

    def test_writes_given_text_to_target_path(self) -> None:
        target = self.dir / "out.json"
        atomic_write_text(target, "hello world")

        self.assertEqual(target.read_text(encoding="utf-8"), "hello world")

    def test_temp_file_created_in_same_directory_as_target(self) -> None:
        target = self.dir / "out.json"
        seen_dirs = []
        real_mkstemp = tempfile.mkstemp

        def spy_mkstemp(*args, **kwargs):
            seen_dirs.append(kwargs.get("dir"))
            return real_mkstemp(*args, **kwargs)

        with patch("tempfile.mkstemp", side_effect=spy_mkstemp):
            atomic_write_text(target, "data")

        self.assertEqual(seen_dirs, [target.parent])

    def test_cleans_up_temp_file_and_leaves_no_partial_target_on_failure(self) -> None:
        target = self.dir / "out.json"

        with patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                atomic_write_text(target, "data")

        self.assertFalse(target.exists())
        leftover = list(self.dir.iterdir())
        self.assertEqual(leftover, [])


if __name__ == "__main__":
    unittest.main()
