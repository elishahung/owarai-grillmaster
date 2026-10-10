"""The single place that builds and runs ffmpeg/ffprobe command lines.

Callers assemble arguments with `ffmpeg(...)` / `ffprobe(...)`, which add the
binary and the flags every run shares, and hand the argv to an injected
`FfmpegRunner`. `SubprocessFfmpegRunner` is the real one: it enforces an
optional timeout, kills the whole process tree when the run is abandoned
(timeout, Ctrl+C, a failing progress callback), and turns a non-zero exit into
a `MediaError` carrying the stderr tail.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
import threading
from typing import TYPE_CHECKING, Protocol

from grillmaster.core.process import StderrTail, kill_process_tree
from grillmaster.media.errors import MediaError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

# Called with the output timestamp (seconds) each time ffmpeg reports progress.
type ProgressCallback = Callable[[float], None]

_STDERR_TAIL_LINES = 20
_PROGRESS_FLAGS = ("-progress", "pipe:1", "-nostats")
# Each progress block also carries `out_time_ms`, which is microseconds too (a
# long-standing ffmpeg misnomer); reading one key fires the callback once.
_PROGRESS_TIME_KEY = "out_time_us"
_MICROSECONDS = 1_000_000


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
    ) -> str:
        """Run `argv` to completion and return its stdout.

        With `on_progress` (ffmpeg argvs only), the runner adds the
        `-progress` flags itself, stdout is consumed as progress lines and
        the return value is empty. Raises `MediaError` on failure or timeout.
        """
        ...


class SubprocessFfmpegRunner:
    """Runs ffmpeg/ffprobe as child processes."""

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        cwd: Path | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> str:
        command = list(argv)
        program = command[0]
        if on_progress is not None:
            if program != "ffmpeg":
                raise ValueError(f"on_progress needs an ffmpeg argv, got {program}")
            command[1:1] = _PROGRESS_FLAGS
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
        timed_out = threading.Event()
        timer = None
        if timeout is not None:
            timer = threading.Timer(timeout, _expire, args=(process, timed_out))
            timer.daemon = True
            timer.start()
        try:
            stdout = _read_stdout(process, on_progress)
            returncode = process.wait()
        finally:
            if timer is not None:
                timer.cancel()
            kill_process_tree(process)
            process.wait()
            stderr_tail.join()

        if timed_out.is_set():
            raise MediaError(
                f"{program} timed out after {timeout}s: {shlex.join(command)}"
            )
        if returncode != 0:
            raise MediaError(
                f"{program} failed (exit {returncode}): {shlex.join(command)}\n"
                f"{stderr_tail.text}"
            )
        return stdout


def _read_stdout(
    process: subprocess.Popen[str], on_progress: ProgressCallback | None
) -> str:
    assert process.stdout is not None  # noqa: S101 - piped above
    if on_progress is None:
        return process.stdout.read()
    for line in process.stdout:
        seconds = parse_progress_line(line)
        if seconds is not None:
            on_progress(seconds)
    return ""


def _expire(process: subprocess.Popen[str], timed_out: threading.Event) -> None:
    timed_out.set()
    kill_process_tree(process)
