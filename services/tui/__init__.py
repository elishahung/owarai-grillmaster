"""Full-screen Textual dashboard for the process command.

``run_process_ui`` owns the terminal on the main thread while the pipeline
runs on a daemon worker thread; they share a lock-guarded ``PipelineState``
via ``TuiProgressReporter``. See state.py / reporter.py / app.py.
"""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable

from loguru import logger

from services.progress import NoopProgressReporter

from .app import GrillMasterApp
from .reporter import TuiProgressReporter
from .state import PipelineState

__all__ = ["run_process_ui", "GrillMasterApp", "TuiProgressReporter", "PipelineState"]


def run_process_ui(
    pipeline: Callable[[NoopProgressReporter], None],
) -> int:
    """Run ``pipeline(reporter)`` under the dashboard.

    Returns an exit code: 0 completed, 1 failed, 130 aborted by the user.
    The pipeline thread is a daemon — aborting exits the process and relies
    on stage resumability, the same contract as Ctrl-C on the plain CLI.
    """
    state = PipelineState()
    reporter = TuiProgressReporter(state)
    reporter.install_logging()

    def _worker() -> None:
        try:
            pipeline(reporter)
        except BaseException as e:
            # process_project already emitted pipeline_failed for normal
            # exceptions; this guards failures outside that wrapper and puts
            # the traceback where the dashboard can show it.
            state.append_log(
                None,
                "ERROR",
                "".join(traceback.format_exception(e)).rstrip(),
            )
            if not state.finished:
                state.on_pipeline_failed(str(e))

    threads: list[threading.Thread] = []

    def _start_attempt() -> None:
        thread = threading.Thread(
            target=_worker, name="pipeline", daemon=True
        )
        threads.append(thread)
        thread.start()

    def _retry() -> bool:
        # Called from the app on `r` after a failure: the previous attempt's
        # thread has ended (pipeline_failed fired), so a fresh one is safe.
        if threads and threads[-1].is_alive():
            return False
        state.reset_for_new_attempt()
        _start_attempt()
        return True

    app = GrillMasterApp(state, on_retry=_retry)
    try:
        _start_attempt()
        try:
            result = app.run()
        except Exception:
            # A dashboard crash must not kill paid pipeline work — fall
            # through to the plain-console wait below.
            logger.exception("Dashboard crashed")
            result = None
    finally:
        reporter.restore_logging()

    thread = threads[-1]
    if result == 130:
        logger.warning("Pipeline aborted from the dashboard; re-run to resume.")
        return 130
    if thread.is_alive():
        # The app died while the pipeline is still working (crash, terminal
        # loss). Let the run finish in plain-console mode instead of killing
        # paid work.
        logger.warning(
            "Dashboard exited while the pipeline is still running; "
            "waiting for it to finish with plain logging."
        )
        thread.join()
    return 1 if state.failed else 0
