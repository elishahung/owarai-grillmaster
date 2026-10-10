"""Child-process helpers shared by `media.ffmpeg`, `agents.process` and the
Claude adapter: one way to start, track and kill a child process tree.

A process tree is killed as a whole: `Popen.kill` alone reaches only the
direct child, and a surviving descendant keeps the stdout pipe open.
`spawn_tree` starts a child as a `ProcessTree` whose every descendant can be
ended even after the child itself exited: on Windows the child joins its own
Job Object before it runs (`TerminateJobObject`; the job also dies with this
interpreter, crash included), on POSIX it leads a new session whose process
group `killpg` signals. `adopt_tree` wraps a child spawned elsewhere (the
Claude SDK's CLI) the same way as far as it can: on Windows it joins a job
once running; on POSIX it shares this interpreter's process group, so only
the child itself is killed.

Every tree is held in `LIVE_PROCESSES` while it runs (`track` / `release`),
so an abort can end them all from the main thread (`kill_all`): on Windows a
child does not die with its parent, and the daemon threads that own the
children are frozen at interpreter exit before their `finally` blocks run.
`kill_all` first sets the process-wide `ABORT` latch, for good: the registry
then refuses new children (`ProcessAbortedError`) and every retry wait
(`ABORT.wait(delay)`) wakes up. `kill_all` is also registered with `atexit`.
"""

from __future__ import annotations

import atexit
import contextlib
import functools
import os
import signal
import subprocess
import sys
import threading
from collections import deque
from typing import IO, TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path


class ProcessAbortedError(RuntimeError):
    """An abort began (`kill_all`): no new child process may start."""


class ChildProcess(Protocol):
    """The leader of a `ProcessTree`: a `Popen`, or a handle on a child
    spawned elsewhere (the Claude SDK's CLI)."""

    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None:
        """The exit code, or `None` while the process runs."""
        ...

    def kill(self) -> None: ...


class ProcessTree[P: ChildProcess]:
    """A child (`leader`) and every process it starts.

    `kill` ends whatever of the tree still runs, the leader exited or not;
    `close` kills too and releases the Windows job. Thread-safe: an abort may
    kill from another thread while the owner closes.
    """

    def __init__(self, leader: P, job: int | None, *, group: bool) -> None:
        """`job`: the Windows Job Object holding the tree; `group`: (POSIX)
        the leader leads its own process group."""
        self.leader = leader
        self._job = job
        self._group = group
        self._lock = threading.Lock()
        self._closed = False

    @property
    def pid(self) -> int:
        return self.leader.pid

    def poll(self) -> int | None:
        return self.leader.poll()

    def kill(self) -> None:
        with self._lock:
            if not self._closed:
                self._kill_locked()

    def close(self) -> None:
        """Kill what is left of the tree and release its job; idempotent."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._kill_locked()
            finally:
                if self._job is not None:
                    _job_api().close(self._job)

    def _kill_locked(self) -> None:
        if self._job is not None:
            _job_api().terminate(self._job)
            return
        if not self._group:
            if self.leader.poll() is None:
                self.leader.kill()
            return
        try:
            os.killpg(self.leader.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # the whole group is gone
        except PermissionError:
            # Only zombies left in the group (macOS); the leader may still run.
            if self.leader.poll() is None:
                self.leader.kill()


def spawn_tree(
    argv: Sequence[str],
    *,
    cwd: Path | None,
    env: Mapping[str, str] | None,
    stdin: int,
    stdout: int,
    stderr: int,
) -> ProcessTree[subprocess.Popen[str]]:
    """Start `argv` in text mode (UTF-8, undecodable bytes replaced: the
    Windows code page would garble Japanese) as a `ProcessTree`. A missing
    program raises `FileNotFoundError`."""
    platform: dict[str, Any] = (
        # Suspended until it is in its job, so no descendant escapes it.
        {"creationflags": _CREATE_SUSPENDED}
        if sys.platform == "win32"
        # A new session makes the child a process-group leader, so `killpg`
        # reaches every descendant, even after the leader exited.
        else {"start_new_session": True}
    )
    popen = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        text=True,
        encoding="utf-8",
        errors="replace",
        **platform,
    )
    if sys.platform != "win32":
        return ProcessTree(popen, None, group=True)
    try:
        job = _job_api().new_job(popen.pid, resume=True)
    except BaseException:
        popen.kill()
        popen.wait()
        raise
    return ProcessTree(popen, job, group=False)


def adopt_tree[P: ChildProcess](process: P) -> ProcessTree[P]:
    """`process`, spawned elsewhere and already running, as a `ProcessTree`.

    On Windows it joins a new job now (a descendant it started before
    escapes); failing that raises `OSError`. On POSIX it is no group leader,
    so a kill reaches only `process` itself.
    """
    if sys.platform != "win32":
        return ProcessTree(process, None, group=False)
    return ProcessTree(process, _job_api().new_job(process.pid), group=False)


_CREATE_SUSPENDED = 0x00000004
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_SUSPEND_RESUME = 0x0800


class _JobApi:
    """The Win32 Job Object calls; bound once, on first use (`_job_api`)."""

    def __init__(self) -> None:
        if sys.platform != "win32":  # pragma: no cover - Windows only
            raise OSError("Job Objects exist on Windows only")
        import ctypes  # noqa: PLC0415 - nothing of it is needed off Windows
        from ctypes import wintypes  # noqa: PLC0415

        class BasicLimits(ctypes.Structure):
            _fields_ = (
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            )

        class IoCounters(ctypes.Structure):
            _fields_ = tuple(
                (name, ctypes.c_uint64)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            )

        class ExtendedLimits(ctypes.Structure):
            _fields_ = (
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            )

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
        ntdll.NtResumeProcess.restype = ctypes.c_long

        self._ctypes = ctypes
        self._kernel32 = kernel32
        self._ntdll = ntdll
        self._limits = ExtendedLimits

    def new_job(self, pid: int, *, resume: bool = False) -> int:
        """A new kill-on-close job holding process `pid` (resumed from
        `CREATE_SUSPENDED` when `resume`); returns the job handle."""
        kernel32 = self._kernel32
        job = kernel32.CreateJobObjectW(None, None)
        self._check(job)
        try:
            limits = self._limits()
            limits.BasicLimitInformation.LimitFlags = (
                _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            self._check(
                kernel32.SetInformationJobObject(
                    job,
                    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                    self._ctypes.byref(limits),
                    self._ctypes.sizeof(limits),
                )
            )
            access = _PROCESS_TERMINATE | _PROCESS_SET_QUOTA
            if resume:
                access |= _PROCESS_SUSPEND_RESUME
            process = kernel32.OpenProcess(access, False, pid)
            self._check(process)
            try:
                self._check(kernel32.AssignProcessToJobObject(job, process))
                if resume:
                    status = self._ntdll.NtResumeProcess(process)
                    if status < 0:
                        raise OSError(
                            "NtResumeProcess failed: "
                            f"NTSTATUS {status & 0xFFFFFFFF:#010x}"
                        )
            finally:
                kernel32.CloseHandle(process)
        except BaseException:
            # Closing the job kills the process it may already hold.
            kernel32.CloseHandle(job)
            raise
        return job

    def terminate(self, job: int) -> None:
        self._check(self._kernel32.TerminateJobObject(job, 1))

    def close(self, handle: int) -> None:
        self._kernel32.CloseHandle(handle)

    def _check(self, ok: object) -> None:
        """Raise the thread's last Win32 error when a call returned 0."""
        if not ok:
            raise self._ctypes.WinError(self._ctypes.get_last_error())


@functools.cache
def _job_api() -> _JobApi:
    return _JobApi()


class ProcessRegistry[P]:
    """The live child processes of this interpreter; thread-safe.

    Owners `register` a process right after spawning it and `unregister` it
    once its tree is dead. `kill` must end a process tree and accept one
    that already exited. `kill_all` sets `abort` for good; from then on
    `register` refuses with `ProcessAbortedError` (the caller still owns,
    and must end, the refused process).
    """

    def __init__(
        self, kill: Callable[[P], None], abort: threading.Event | None = None
    ) -> None:
        self._kill = kill
        self.abort = abort if abort is not None else threading.Event()
        self._lock = threading.Lock()
        self._live: list[P] = []

    def register(self, process: P) -> None:
        with self._lock:
            if self.abort.is_set():
                raise ProcessAbortedError(
                    "an abort is in progress: no new child process may start"
                )
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
        """Set `abort`, then kill every registered tree (outside the lock, so
        owners can still unregister); returns how many were registered.
        Killed processes stay registered until their owners unregister them."""
        with self._lock:
            self.abort.set()
            processes = list(self._live)
        for process in processes:
            # Best effort: one tree that cannot be killed must not spare the rest.
            with contextlib.suppress(OSError):
                self._kill(process)
        return len(processes)


# The process-wide abort latch: set by `kill_all`, never cleared.
ABORT = threading.Event()
LIVE_PROCESSES: ProcessRegistry[ProcessTree[Any]] = ProcessRegistry(
    ProcessTree.kill, ABORT
)


def track[T: ProcessTree[Any]](tree: T) -> T:
    """Hold `tree` in `LIVE_PROCESSES`; once an abort began it is closed
    instead and `ProcessAbortedError` raised."""
    try:
        LIVE_PROCESSES.register(tree)
    except ProcessAbortedError:
        tree.close()
        raise
    return tree


def release(tree: ProcessTree[Any]) -> None:
    """Kill what is left of a `track`ed tree, release it and forget it."""
    try:
        tree.close()
    finally:
        LIVE_PROCESSES.unregister(tree)


def kill_all() -> int:
    """The abort: latch `ABORT`, then kill every live child-process tree in
    `LIVE_PROCESSES`; returns how many trees were live."""
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
