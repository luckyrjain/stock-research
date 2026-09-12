"""Shared atomic-write-to-disk helper.

Five modules independently reimplemented the same mkstemp/fdopen/os.replace
sequence for writing a JSON/text cache file without ever leaving a truncated
or corrupt file behind on an interrupted write (process killed/OOM/container
restart mid-write, or two workers racing on the same file). This is that
logic, extracted to one place.
"""

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` atomically (tempfile in the same directory,
    then os.replace) — a direct write_text()'s interleaved writes could
    otherwise leave invalid/partial content on disk for the next reader.

    Caller is responsible for ensuring `path.parent` exists.
    """
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_path, path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise
