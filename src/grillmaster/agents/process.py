"""The one place agent CLIs are spawned: JSONL stdout, per-turn timeout, tree-kill.

Agent CLIs on Windows are batch shims (cmd.exe -> node) that spawn children
of their own, and an agent may start shell tools. `Popen.kill` reaches only
the direct child, and a surviving descendant keeps the stdout pipe open, so
each CLI is a `core.process.ProcessTree` (its own Job Object on Windows, its
own process group on POSIX) and a timeout kills the whole tree, even when the
CLI itself already exited and only a descendant holds stdout. Every spawned
tree is held in `core.process.LIVE_PROCESSES` until it is dead, so an abort
can kill it from another thread; once an abort began, spawning raises
`core.process.ProcessAbortedError`. Output is decoded as UTF-8 explicitly: the
Windows locale code page would garble Japanese.

A CLI that serves several turns (gemini's ACP mode) keeps stdin open, gets
each request through `send` and restarts the watchdog per turn.
"""

from __future__ import annotations

import contextlib
import subprocess
import threading
from dataclasses import dataclass
from subprocess import CalledProcessError, TimeoutExpired
from typing import IO, TYPE_CHECKING, Protocol

from grillmaster.core.process import StderrTail, release, spawn_tree, track

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Mapping, Sequence
    from pathlib import Path

__all__ = [
    "CalledProcessError",
    "LineProcess",
    "ProcessSpec",
    "Spawn",
    "TimeoutExpired",
    "run_text",
    "spawn",
]

_STDERR_TAIL_LINES = 40
# Bound on waiting for the exit code once stdout closed or the tree was killed.
_EXIT_WAIT_S = 30.0
# How long a CLI that closed stdout may take to exit before what is left of
# its tree is killed (which would replace its exit code).
_EXIT_GRACE_S = 5.0


@dataclass(frozen=True, slots=True)
class ProcessSpec:
    argv: Sequence[str]
    cwd: Path | None
    timeout_s: float
    # `None` inherits the parent's environment.
    env: Mapping[str, str] | None = None
    # Written by a background thread so a large prompt cannot deadlock
    # against a child that is already writing stdout.
    stdin: str = ""
    # Leave stdin open after writing (agy's stream-json input reads further
    # messages until stdin closes, gemini's ACP mode further requests); write
    # more with `send`, close it with `close_stdin`.
    keep_stdin_open: bool = False


class LineProcess(Protocol):
    """A running CLI whose stdout is consumed line by line."""

    def lines(self) -> Generator[str]:
        """Stdout lines without the newline, until EOF or the timeout kill;
        closing it early kills the tree."""
        ...

    def send(self, text: str) -> None:
        """Write `text` to the open stdin after what was written before, on a
        background thread (see `ProcessSpec.stdin`); lost if the child exited."""
        ...

    def restart_watchdog(self, timeout_s: float | None) -> None:
        """Kill the tree `timeout_s` from now instead (`None`: never); a
        tree that already timed out stays timed out."""
        ...

    def close_stdin(self) -> None: ...

    def wait(self) -> int:
        """The exit code; call after `lines()` is exhausted."""
        ...

    @property
    def timed_out(self) -> bool: ...

    @property
    def stderr_tail(self) -> str: ...


type Spawn = Callable[[ProcessSpec], LineProcess]


class _PopenLineProcess:
    def __init__(self, spec: ProcessSpec) -> None:
        self._tree = track(
            spawn_tree(
                spec.argv,
                cwd=spec.cwd,
                env=spec.env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )
        self._stderr = StderrTail(_pipe(self._tree.leader.stderr), _STDERR_TAIL_LINES)
        self._timed_out = threading.Event()
        self._stdin_lock = threading.Lock()
        self._writer = threading.Thread(
            target=self._write,
            args=(None, spec.stdin, not spec.keep_stdin_open),
            daemon=True,
        )
        self._watchdog_lock = threading.Lock()
        self._released = False
        self._watchdog: threading.Timer | None = None
        self._writer.start()
        self.restart_watchdog(spec.timeout_s)

    def lines(self) -> Generator[str]:
        stdout = _pipe(self._tree.leader.stdout)
        eof = False
        try:
            for line in stdout:
                yield line.rstrip("\r\n")
            eof = True
        finally:
            # Stdout is done: either the CLI exited, the caller stopped early
            # or the watchdog killed it. Nothing of the tree may outlive this.
            if eof:
                with contextlib.suppress(TimeoutExpired):
                    self._tree.leader.wait(timeout=_EXIT_GRACE_S)
            self._release()

    def send(self, text: str) -> None:
        previous = self._writer
        self._writer = threading.Thread(
            target=self._write, args=(previous, text, False), daemon=True
        )
        self._writer.start()

    def restart_watchdog(self, timeout_s: float | None) -> None:
        with self._watchdog_lock:
            if self._watchdog is not None:
                self._watchdog.cancel()
                self._watchdog = None
            if timeout_s is None or self._released:
                return
            self._watchdog = threading.Timer(timeout_s, self._on_timeout)
            self._watchdog.daemon = True
            self._watchdog.start()

    def close_stdin(self) -> None:
        self._writer.join()
        with self._stdin_lock:
            stdin = _pipe(self._tree.leader.stdin)
            if not stdin.closed:
                # The child may have exited already.
                with contextlib.suppress(OSError):
                    stdin.close()

    def wait(self) -> int:
        try:
            code = self._tree.leader.wait(timeout=_EXIT_WAIT_S)
        except TimeoutExpired:
            self._tree.kill()
            code = self._tree.leader.wait(timeout=_EXIT_WAIT_S)
        self._release()
        self._stderr.join(timeout=_EXIT_WAIT_S)
        self.close_stdin()
        return code

    @property
    def timed_out(self) -> bool:
        return self._timed_out.is_set()

    @property
    def stderr_tail(self) -> str:
        return self._stderr.text

    def _write(self, previous: threading.Thread | None, text: str, close: bool) -> None:  # noqa: FBT001 - a Thread target
        """Write `text` after `previous` wrote its own, then maybe close stdin."""
        if previous is not None:
            previous.join()
        stdin = _pipe(self._tree.leader.stdin)
        # Closed (`ValueError`) or the child exited (`OSError`): its exit
        # code and stdout tell the story.
        with contextlib.suppress(OSError, ValueError):
            if text:
                stdin.write(text)
                stdin.flush()
        if close:
            with self._stdin_lock, contextlib.suppress(OSError):
                stdin.close()

    def _release(self) -> None:
        """Nothing of the tree may outlive this; idempotent."""
        with self._watchdog_lock:
            self._released = True
            if self._watchdog is not None:
                self._watchdog.cancel()
        release(self._tree)

    def _on_timeout(self) -> None:
        self._timed_out.set()
        self._tree.kill()


def spawn(spec: ProcessSpec) -> LineProcess:
    """Start `spec.argv`; the default `Spawn` for every adapter."""
    return _PopenLineProcess(spec)


@dataclass(frozen=True, slots=True)
class TextResult:
    returncode: int
    stdout: str
    stderr: str


def run_text(spec: ProcessSpec, *, spawn_fn: Spawn = spawn) -> TextResult:
    """Run to completion and collect stdout; raises `TimeoutExpired` on timeout."""
    process = spawn_fn(spec)
    stdout = "\n".join(process.lines())
    code = process.wait()
    if process.timed_out:
        raise TimeoutExpired(list(spec.argv), spec.timeout_s)
    return TextResult(code, stdout, process.stderr_tail)


def _pipe(stream: IO[str] | None) -> IO[str]:
    if stream is None:  # pragma: no cover - every pipe is requested above
        raise RuntimeError("process pipe missing")
    return stream
