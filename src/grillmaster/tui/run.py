"""`run_with_tui`: run some work under the dashboard and hand back its result.

The dashboard owns the terminal on the calling thread while the work runs
on a daemon worker thread, reporting through the `TuiSink` it is given.
While the dashboard is up, loguru writes into it (`log_bridge`) instead of
the console: the caller's console handler is removed on entry and
`restore_logging` re-creates it on exit; every other handler (the run log
file) stays. The event bus handed to the work must therefore not also carry
a `ConsoleSink`: it would log every event into the dashboard's log pane.

Once the dashboard is gone the sink no longer queues: it forwards to a
`ConsoleSink` while the work is waited for, or drops events after an abort.
An abort kills every live child-process tree (`core.process.kill_all`),
since on Windows a child outlives its parent, then gives the worker
`INTERRUPT_WAIT_S` to unwind before `RunAborted` is raised.
"""

from __future__ import annotations

import threading
import time
import traceback
from typing import TYPE_CHECKING, NoReturn

from loguru import logger

from grillmaster.core.process import kill_all
from grillmaster.events.sinks import ConsoleSink
from grillmaster.events.types import LogLine
from grillmaster.tui.app import AppExit, GrillMasterApp
from grillmaster.tui.sink import LOG_FORMAT, TuiSink, log_bridge
from grillmaster.tui.state import PipelineState

if TYPE_CHECKING:
    from collections.abc import Callable

    from grillmaster.events.bus import EventSink

# How long an aborted worker gets to unwind once its processes are killed.
INTERRUPT_WAIT_S = 10.0
# Joins wait in slices: on Windows an unbounded `Thread.join` ignores Ctrl-C.
_JOIN_SLICE_S = 0.2


class RunAborted(KeyboardInterrupt):
    """The user left the dashboard (or pressed Ctrl-C while the work was
    waited for) before the work finished; its processes were killed, its
    thread is abandoned (a daemon) and the project stays resumable."""


def run_with_tui[T](
    work: Callable[[EventSink], T],
    *,
    restore_logging: Callable[[], None],
    console_handler: int | None = None,
    app_factory: Callable[[TuiSink, PipelineState, Callable[[], None]], GrillMasterApp]
    | None = None,
    kill_processes: Callable[[], object] = kill_all,
    interrupt_wait_s: float = INTERRUPT_WAIT_S,
) -> T:
    """Run `work(sink)` under the dashboard; returns its result or raises its
    error once the user leaves the dashboard (`q`).

    `console_handler` is the loguru handler id the dashboard replaces and
    `restore_logging` re-creates it afterwards. After a failure `r` runs
    `work` again on a fresh thread; the last attempt decides the outcome.
    Raises `RunAborted` when the user quits while the work runs. Should the
    dashboard itself crash, the work is waited for with console output.
    `app_factory` builds the app (tests drive a headless one);
    `kill_processes` ends the work's child processes on an abort.
    """
    sink = TuiSink()
    state = PipelineState()
    worker = _Worker(work, sink)
    app = (app_factory or _default_app)(sink, state, worker.retry)
    if console_handler is not None:
        logger.remove(console_handler)
    handler = logger.add(
        log_bridge(sink), level="DEBUG", format=LOG_FORMAT, colorize=False
    )
    crash: Exception | None = None
    exit_code: AppExit | None = None
    try:
        worker.start()
        try:
            exit_code = app.run()
        except Exception as error:  # noqa: BLE001 - a dashboard crash must not kill paid work
            crash = error
    finally:
        logger.remove(handler)
        aborted = exit_code is AppExit.ABORTED
        sink.detach(None if aborted else ConsoleSink())
        restore_logging()
    if crash is not None:
        logger.opt(exception=crash).error("Dashboard crashed")
    elif app.return_code not in {None, 0}:
        # Textual caught the error itself and printed its traceback.
        logger.error(f"Dashboard crashed (exit code {app.return_code})")
    if aborted:
        _abort(worker, kill_processes, interrupt_wait_s)
    if worker.running():
        logger.warning(
            "Dashboard exited while the work is still running; waiting for it "
            "with console logging (Ctrl-C aborts)."
        )
    try:
        return worker.outcome()
    except RunAborted:
        raise
    except KeyboardInterrupt:
        _abort(worker, kill_processes, interrupt_wait_s)


def _abort[T](
    worker: _Worker[T], kill_processes: Callable[[], object], wait_s: float
) -> NoReturn:
    logger.warning("Aborted; killing child processes. Re-run to resume.")
    if not worker.abandon(kill_processes, wait_s):
        logger.warning(f"The work did not stop within {wait_s:g}s; abandoning it.")
    raise RunAborted


def _default_app(
    sink: TuiSink, state: PipelineState, retry: Callable[[], None]
) -> GrillMasterApp:
    return GrillMasterApp(sink, state, on_retry=retry)


class _Worker[T]:
    """Runs `work` on a daemon thread, once per attempt."""

    def __init__(self, work: Callable[[EventSink], T], sink: TuiSink) -> None:
        self._work = work
        self._sink = sink
        self._thread: threading.Thread | None = None
        self._result: tuple[T] | None = None
        self._error: BaseException | None = None

    def start(self) -> None:
        self._result = None
        self._error = None
        self._thread = threading.Thread(target=self._run, name="pipeline", daemon=True)
        self._thread.start()

    def retry(self) -> None:
        """Start the next attempt; the app calls it only once the previous
        attempt reported `WorkDone`, so its thread is ending."""
        self._join()
        self.start()

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def outcome(self) -> T:
        """Wait for the current attempt; its result, or its error raised.
        The wait stays interruptible by Ctrl-C."""
        self._join()
        if self._error is not None:
            raise self._error
        if self._result is None:
            raise RuntimeError("run_with_tui: the work was never started")
        return self._result[0]

    def abandon(self, kill_processes: Callable[[], object], wait_s: float) -> bool:
        """Kill the work's processes until its thread ends, for at most
        `wait_s` (killing repeatedly, as the work may spawn more while it
        unwinds); whether it ended."""
        deadline = time.monotonic() + wait_s
        while True:
            kill_processes()
            remaining = deadline - time.monotonic()
            if not self.running() or remaining <= 0:
                return not self.running()
            assert self._thread is not None  # noqa: S101 - running() checked it
            self._thread.join(min(_JOIN_SLICE_S, remaining))

    def _join(self) -> None:
        thread = self._thread
        while thread is not None and thread.is_alive():
            thread.join(_JOIN_SLICE_S)

    def _run(self) -> None:
        try:
            self._result = (self._work(self._sink),)
        except BaseException as error:  # noqa: BLE001 - handed back by outcome()
            self._error = error
            # Failures outside the pipeline's own step handling (opening the
            # project, say) would otherwise leave no trace on the dashboard.
            self._sink.emit(
                LogLine("ERROR", "".join(traceback.format_exception(error)).rstrip())
            )
            self._sink.work_done(str(error) or type(error).__name__)
            return
        self._sink.work_done(None)
