"""Filesystem helpers shared by every artifact writer."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

# Windows refuses to replace a file another process holds open (a TUI preview,
# an antivirus scan); those handles are short-lived, so retry briefly.
_REPLACE_ATTEMPTS = 5
_REPLACE_BACKOFF_S = 0.05


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` so readers never observe a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        _replace(Path(tmp), path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _replace(source: Path, target: Path) -> None:
    for attempt in range(1, _REPLACE_ATTEMPTS + 1):
        try:
            source.replace(target)
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS:
                raise
            time.sleep(_REPLACE_BACKOFF_S * attempt)
        else:
            return
