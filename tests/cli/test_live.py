from __future__ import annotations

import signal
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest
import typer

from grillmaster.cli.live import ABORT_EXIT_CODE, exit_aborted, run_live
from grillmaster.events.sinks import ConsoleSink

if TYPE_CHECKING:
    from collections.abc import Sequence

    from grillmaster.events.bus import EventSink


def test_without_a_terminal_events_go_to_the_console():
    seen: list[Sequence[EventSink]] = []

    def work(sinks: Sequence[EventSink]) -> str:
        seen.append(sinks)
        return "done"

    assert run_live(work, interactive=False) == "done"
    [sinks] = seen
    assert [type(sink) for sink in sinks] == [ConsoleSink]


def test_an_interrupt_without_a_terminal_propagates():
    def work(sinks: Sequence[EventSink]) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_live(work, interactive=False, kill_processes=list)


def test_ctrl_c_without_a_terminal_kills_children_before_unwinding():
    before = signal.getsignal(signal.SIGINT)
    killed: list[str] = []

    def work(sinks: Sequence[EventSink]) -> None:
        signal.raise_signal(signal.SIGINT)

    with pytest.raises(KeyboardInterrupt):
        run_live(work, interactive=False, kill_processes=lambda: killed.append("kill"))
    assert killed == ["kill"]
    assert signal.getsignal(signal.SIGINT) is before


def test_abort_exits_130_normally_when_no_other_thread_is_alive():
    exits: list[int] = []
    finished = threading.Thread(target=lambda: None)
    finished.start()
    finished.join()
    with pytest.raises(typer.Exit) as raised:
        exit_aborted(
            threads=lambda: [threading.main_thread(), finished],
            hard_exit=exits.append,
        )
    assert raised.value.exit_code == ABORT_EXIT_CODE
    assert exits == []


def test_abort_exits_hard_while_a_daemon_pool_worker_lingers():
    # Pool workers started from a daemon thread (the dashboard's worker) are
    # daemons too, yet `concurrent.futures` joins them at interpreter exit.
    release = threading.Event()
    pool = ThreadPoolExecutor(thread_name_prefix="lingering")

    def start_worker() -> None:
        pool.submit(release.wait)

    starter = threading.Thread(target=start_worker, daemon=True)
    starter.start()
    starter.join()
    exits: list[int] = []

    def threads() -> list[threading.Thread]:
        return [
            thread
            for thread in threading.enumerate()
            if thread is threading.main_thread() or thread.name.startswith("lingering")
        ]

    try:
        [_, worker] = threads()
        assert worker.daemon
        # A real hard exit never returns; the fake does, and the normal exit follows.
        with pytest.raises(typer.Exit):
            exit_aborted(threads=threads, hard_exit=exits.append)
    finally:
        release.set()
        pool.shutdown()
    assert exits == [ABORT_EXIT_CODE]
