"""Running pipeline work with live output, and how an aborted run exits.

On a terminal the work runs under the dashboard (`tui.run_with_tui`), whose
`TuiSink` is then the only live sink: a `ConsoleSink` beside it would log
every event into the dashboard's log pane. Anywhere else (a pipe, CI) the
events go to a `ConsoleSink`. Either way the run's JSONL log is added by the
pipeline itself.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import threading
from typing import TYPE_CHECKING, NoReturn

import typer
from loguru import logger

from grillmaster.cli.common import console_handler, fail, restore_console_logging

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence
    from types import FrameType

    from grillmaster.events.bus import EventSink

# 128 + SIGINT, what a shell reports for a run stopped by Ctrl-C.
ABORT_EXIT_CODE = 130


def interactive_terminal() -> bool:
    """Whether stdout is a terminal that should get the dashboard."""
    return (
        sys.stdout.isatty()
        and not os.environ.get("CI")
        and not os.environ.get("NO_COLOR")
    )


def run_live[T](
    work: Callable[[Sequence[EventSink]], T],
    *,
    interactive: bool | None = None,
    kill_processes: Callable[[], object] | None = None,
) -> T:
    """`work(sinks)` under the dashboard when `interactive` (default: on a
    terminal), else with console logging.

    Raises `KeyboardInterrupt` (`tui.RunAborted` from the dashboard) once
    the work's child processes are killed; `kill_processes` (default
    `core.process.kill_all`) kills them on a console run's Ctrl-C.
    """
    if interactive is None:
        interactive = interactive_terminal()
    if interactive:
        from grillmaster.tui import run_with_tui

        return run_with_tui(
            lambda sink: work([sink]),
            restore_logging=restore_console_logging,
            console_handler=console_handler(),
        )
    from grillmaster.events.sinks import ConsoleSink

    if kill_processes is None:
        from grillmaster.core.process import kill_all

        kill_processes = kill_all
    with _kill_on_interrupt(kill_processes):
        return work([ConsoleSink()])


@contextlib.contextmanager
def _kill_on_interrupt(kill_processes: Callable[[], object]) -> Iterator[None]:
    """While the block runs, Ctrl-C kills the live child processes before
    `KeyboardInterrupt` unwinds the main thread: a thread pool's exit joins
    its workers, which would otherwise wait on those children first."""

    def interrupt(signum: int, frame: FrameType | None) -> NoReturn:  # noqa: ARG001 - the signal handler signature
        kill_processes()
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGINT, interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def run_or_exit[T](work: Callable[[Sequence[EventSink]], T], *, failure: str) -> T:
    """`run_live(work)`; a failure exits 1 with `"<failure>: <error>"`, an
    abort exits `ABORT_EXIT_CODE`."""
    try:
        return run_live(work)
    except KeyboardInterrupt:
        exit_aborted()
    except Exception as error:  # noqa: BLE001 - reported as the command's failure
        fail(f"{failure}: {error}")


def exit_aborted(
    *,
    threads: Callable[[], Iterable[threading.Thread]] = threading.enumerate,
    hard_exit: Callable[[int], object] = os._exit,
) -> NoReturn:
    """Exit with `ABORT_EXIT_CODE` after an abort (child processes killed)."""
    logger.warning("Run aborted; re-run the same command to resume.")
    main = threading.main_thread()
    if any(thread is not main and thread.is_alive() for thread in threads()):
        # A worker may still be blocked on a killed process's pipes or a
        # network call. A daemon flag does not make it safe to leave behind:
        # thread-pool workers inherit it from the (daemon) dashboard worker,
        # yet `concurrent.futures` joins them at interpreter exit all the
        # same, so a normal exit could hang for the length of an agent
        # timeout. Their processes are dead and every state save is atomic,
        # so nothing is lost by skipping the shutdown once the logs are out.
        logger.complete()
        logger.remove()
        sys.stdout.flush()
        sys.stderr.flush()
        hard_exit(ABORT_EXIT_CODE)
    raise typer.Exit(code=ABORT_EXIT_CODE)
