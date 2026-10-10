"""Child-process helpers shared by `media.ffmpeg` and `agents.process`.

Both spawn their child with `start_new_session` on POSIX, so the child leads
a process group that `kill_process_tree` can signal as a whole; on Windows
`taskkill /T` walks the tree instead. `Popen.kill` alone reaches only the
direct child, and a surviving descendant keeps the stdout pipe open.

Every spawned tree (the Claude SDK's CLI too, registered by
`agents.adapters.claude`) is also held in `LIVE_PROCESSES` while it runs, so an
abort can end them all from the main thread (`kill_all`): on Windows a child
does not die with its parent, and the daemon threads that own the children
are frozen at interpreter exit before their `finally` blocks run. `kill_all`
is also registered with `atexit`.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import signal
import subprocess
import sys
import threading
from collections import deque
from typing import IO, TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable


class ChildProcess(Protocol):
    """What a tree kill needs of a child: a `Popen`, or a handle on a child
    spawned elsewhere (the Claude SDK's CLI)."""

    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None:
        """The exit code, or `None` while the process runs."""
        ...

    def kill(self) -> None: ...


def kill_process_tree(process: ChildProcess) -> None:
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


class ProcessRegistry[P]:
    """The live child processes of this interpreter; thread-safe.

    Owners `register` a process right after spawning it and `unregister` it
    once its tree is dead. `kill` must end a process tree and accept one
    that already exited.
    """

    def __init__(self, kill: Callable[[P], None]) -> None:
        self._kill = kill
        self._lock = threading.Lock()
        self._live: list[P] = []

    def register(self, process: P) -> None:
        with self._lock:
            self._live.append(process)

    def unregister(self, process: P) -> None:
        """Forget `process`; one that is not registered is fine."""
        with self._lock, contextlib.suppress(ValueError):
            self._live.remove(process)

    def live(self) -> list[P]:
        """A snapshot of the registered processes, oldest first."""
        with self._lock:
            return list(self._live)

    def kill_all(self) -> int:
        """Kill every registered tree (outside the lock, so owners can still
        unregister); returns how many were registered. Killed processes stay
        registered until their owners unregister them."""
        processes = self.live()
        for process in processes:
            # Best effort: one tree that cannot be killed must not spare the rest.
            with contextlib.suppress(OSError):
                self._kill(process)
        return len(processes)


LIVE_PROCESSES: ProcessRegistry[ChildProcess] = ProcessRegistry(kill_process_tree)


def kill_all() -> int:
    """Kill every live child-process tree spawned through `LIVE_PROCESSES`."""
    return LIVE_PROCESSES.kill_all()


atexit.register(kill_all)


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
