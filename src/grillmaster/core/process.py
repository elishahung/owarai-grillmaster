"""Child-process helpers shared by `media.ffmpeg` and `agents.process`.

Both spawn their child with `start_new_session` on POSIX, so the child leads
a process group that `kill_process_tree` can signal as a whole; on Windows
`taskkill /T` walks the tree instead. `Popen.kill` alone reaches only the
direct child, and a surviving descendant keeps the stdout pipe open.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from collections import deque
from typing import IO


def kill_process_tree(process: subprocess.Popen[str]) -> None:
    """Forcefully end `process` and every descendant; an exited process is fine."""
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],
            check=False,
            capture_output=True,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()


class StderrTail:
    """Drains a text stream on a daemon thread, keeping its last `max_lines` lines.

    Draining also keeps the child from blocking on a full stderr pipe.
    """

    def __init__(self, stream: IO[str], max_lines: int) -> None:
        self._lines: deque[str] = deque(maxlen=max_lines)
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._drain, args=(stream,), daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    @property
    def text(self) -> str:
        """The collected lines, newline-joined, without line endings."""
        with self._lock:
            return "\n".join(self._lines)

    def _drain(self, stream: IO[str]) -> None:
        for line in stream:
            with self._lock:
                self._lines.append(line.rstrip("\r\n"))
