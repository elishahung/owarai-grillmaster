"""`run_with_tui`: run some work under the dashboard and hand back its result.

The dashboard owns the terminal on the calling thread while the work runs
on a daemon worker thread, reporting through the `TuiSink` it is given.
While the dashboard is up, loguru writes only into it (`log_bridge`): every
handler is removed on entry and `restore_logging` re-creates the console
logging on exit.
"""

from __future__ import annotations

import sys
import threading
import traceback
from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.events.types import LogLine
from grillmaster.tui.app import AppExit, GrillMasterApp
from grillmaster.tui.sink import LOG_FORMAT, TuiSink, log_bridge
from grillmaster.tui.state import PipelineState

if TYPE_CHECKING:
    from collections.abc import Callable

    from grillmaster.events.bus import EventSink


class RunAborted(KeyboardInterrupt):
    """The user left the dashboard while the work was still running; the
    worker thread is abandoned (a daemon) and the project stays resumable."""


def _stderr_logging() -> None:
    logger.add(sys.stderr, level="INFO")


def run_with_tui[T](
    work: Callable[[EventSink], T],
    *,
    restore_logging: Callable[[], None] = _stderr_logging,
    app_factory: Callable[[TuiSink, PipelineState, Callable[[], None]], GrillMasterApp]
    | None = None,
) -> T:
    """Run `work(sink)` under the dashboard; returns its result or raises its
    error once the user leaves the dashboard (`q`).

    After a failure `r` runs `work` again on a fresh thread; the last
    attempt decides the outcome. Raises `RunAborted` when the user quits
    while the work runs. Should the dashboard itself crash, the work is
    waited for with console logging restored. `app_factory` builds the app
    (tests drive a headless one).
    """
    sink = TuiSink()
    state = PipelineState()
    worker = _Worker(work, sink)
    app = (app_factory or _default_app)(sink, state, worker.retry)
    logger.remove()
    handler = logger.add(
        log_bridge(sink), level="DEBUG", format=LOG_FORMAT, colorize=False
    )
    crash: Exception | None = None
    try:
        worker.start()
        try:
            exit_code = app.run()
        except Exception as error:  # noqa: BLE001 - a dashboard crash must not kill paid work
            crash = error
            exit_code = None
    finally:
        logger.remove(handler)
        restore_logging()
    if crash is not None:
        logger.opt(exception=crash).error("Dashboard crashed")
    if exit_code is AppExit.ABORTED:
        logger.warning("Aborted from the dashboard; re-run to resume.")
        raise RunAborted
    if worker.running():
        logger.warning(
            "Dashboard exited while the work is still running; waiting for it "
            "with console logging."
        )
    return worker.outcome()


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
        if self._thread is not None:
            self._thread.join()
        self.start()

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def outcome(self) -> T:
        """Wait for the current attempt; its result, or its error raised."""
        if self._thread is not None:
            self._thread.join()
        if self._error is not None:
            raise self._error
        if self._result is None:
            raise RuntimeError("run_with_tui: the work was never started")
        return self._result[0]

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
