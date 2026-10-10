"""The single place that builds and runs ffmpeg/ffprobe command lines.

Callers assemble arguments with `ffmpeg(...)` / `ffprobe(...)`, which add the
binary and the flags every run shares, and hand the argv to an injected
`FfmpegRunner`. `SubprocessFfmpegRunner` is the real one: a watchdog kills the
whole process tree on a timeout, a stalled progress clock or a set `abort`
event; the tree is also killed when the run is abandoned (Ctrl+C, a failing
progress callback), and a non-zero exit becomes a `MediaError` carrying the
stderr tail.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from grillmaster.core.process import StderrTail, kill_process_tree
from grillmaster.media.errors import MediaError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

# Called with the output timestamp (seconds) each time ffmpeg reports progress.
type ProgressCallback = Callable[[float], None]

# A progress run whose output time has not advanced for this long is hung (a
# NAS write blocked behind a dropped VPN, a wedged NVENC session) and is
# killed instead of holding the package step forever.
STALL_TIMEOUT_SECONDS = 10 * 60.0

_STDERR_TAIL_LINES = 20
_PROGRESS_FLAGS = ("-progress", "pipe:1", "-nostats")
# Each progress block also carries `out_time_ms`, which is microseconds too (a
# long-standing ffmpeg misnomer); reading one key fires the callback once.
_PROGRESS_TIME_KEY = "out_time_us"
_MICROSECONDS = 1_000_000
# How often the watchdog checks the deadline, the stall clock and `abort`.
_WATCH_INTERVAL_SECONDS = 0.1


def ffmpeg(*args: str) -> list[str]:
    """An ffmpeg argv: quiet, non-interactive, overwriting outputs."""
    return ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args]


def ffprobe(*args: str) -> list[str]:
    """An ffprobe argv that reports errors only."""
    return ["ffprobe", "-v", "error", *args]


def parse_progress_line(line: str) -> float | None:
    """Output time in seconds from one `-progress` line, else `None`."""
    key, separator, value = line.strip().partition("=")
    if not separator or key != _PROGRESS_TIME_KEY:
        return None
    try:
        return int(value) / _MICROSECONDS
    except ValueError:  # `N/A` before the first frame
        return None


class FfmpegRunner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        cwd: Path | None = None,
        on_progress: ProgressCallback | None = None,
        abort: threading.Event | None = None,
    ) -> str:
        """Run `argv` to completion and return its stdout.

        With `on_progress` (ffmpeg argvs only), the runner adds the
        `-progress` flags itself, stdout is consumed as progress lines and
        the return value is empty. Setting `abort`, before or during the run,
        kills it. Raises `MediaError` on failure, timeout, stall or abort.
        """
        ...


@dataclass(frozen=True, slots=True)
class SubprocessFfmpegRunner:
    """Runs ffmpeg/ffprobe as child processes.

    A run with `on_progress` is killed as hung once its output time has not
    advanced for `stall_timeout` seconds (counted from the start until the
    first report).
    """

    stall_timeout: float = STALL_TIMEOUT_SECONDS

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        cwd: Path | None = None,
        on_progress: ProgressCallback | None = None,
        abort: threading.Event | None = None,
    ) -> str:
        command = list(argv)
        program = command[0]
        if on_progress is not None:
            if program != "ffmpeg":
                raise ValueError(f"on_progress needs an ffmpeg argv, got {program}")
            command[1:1] = _PROGRESS_FLAGS
        if abort is not None and abort.is_set():
            raise MediaError(f"{program} aborted before start: {shlex.join(command)}")
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                # POSIX: own process group, so the whole tree can be killed.
                start_new_session=sys.platform != "win32",
            )
        except FileNotFoundError:
            raise MediaError(f"{program} not found on PATH") from None

        assert process.stderr is not None  # noqa: S101 - piped above
        stderr_tail = StderrTail(process.stderr, _STDERR_TAIL_LINES)
        watchdog = _Watchdog(
            process,
            timeout=timeout,
            stall_timeout=self.stall_timeout if on_progress is not None else None,
            abort=abort,
        )
        try:
            stdout = _read_stdout(process, on_progress, watchdog)
            returncode = process.wait()
        finally:
            watchdog.stop()
            kill_process_tree(process)
            process.wait()
            stderr_tail.join()

        if watchdog.reason is not None:
            raise MediaError(
                f"{program} {watchdog.reason}: {shlex.join(command)}\n"
                f"{stderr_tail.text}"
            )
        if returncode != 0:
            raise MediaError(
                f"{program} failed (exit {returncode}): {shlex.join(command)}\n"
                f"{stderr_tail.text}"
            )
        return stdout


def _read_stdout(
    process: subprocess.Popen[str],
    on_progress: ProgressCallback | None,
    watchdog: _Watchdog,
) -> str:
    assert process.stdout is not None  # noqa: S101 - piped above
    if on_progress is None:
        return process.stdout.read()
    for line in process.stdout:
        seconds = parse_progress_line(line)
        if seconds is not None:
            watchdog.progressed(seconds)
            on_progress(seconds)
    return ""


class _Watchdog:
    """Kills `process`'s tree on timeout, a stall or `abort`; `reason` says
    which (read it after `stop`). Starts no thread when nothing is watched."""

    def __init__(
        self,
        process: subprocess.Popen[str],
        *,
        timeout: float | None,
        stall_timeout: float | None,
        abort: threading.Event | None,
    ) -> None:
        self._process = process
        self._timeout = timeout
        self._stall_timeout = stall_timeout
        self._abort = abort
        self._started = time.monotonic()
        self._furthest = -1.0
        self._advanced = self._started
        self._stopped = threading.Event()
        self.reason: str | None = None
        self._thread: threading.Thread | None = None
        if timeout is not None or stall_timeout is not None or abort is not None:
            self._thread = threading.Thread(target=self._watch, daemon=True)
            self._thread.start()

    def progressed(self, seconds: float) -> None:
        """Restart the stall clock when the output time moved forward."""
        if seconds > self._furthest:
            self._furthest = seconds
            self._advanced = time.monotonic()

    def stop(self) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join()

    def _watch(self) -> None:
        while not self._stopped.wait(_WATCH_INTERVAL_SECONDS):
            reason = self._verdict(time.monotonic())
            if reason is not None:
                self.reason = reason
                kill_process_tree(self._process)
                return

    def _verdict(self, now: float) -> str | None:
        if self._abort is not None and self._abort.is_set():
            return "aborted"
        if self._timeout is not None and now - self._started >= self._timeout:
            return f"timed out after {self._timeout}s"
        stall = self._stall_timeout
        if stall is not None and now - self._advanced >= stall:
            return f"stalled: no progress for {stall}s"
        return None
