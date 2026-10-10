"""`run_with_tui` end to end, with a headless app that leaves by itself."""

from __future__ import annotations

import io
import threading
import time
from typing import TYPE_CHECKING

import pytest
from loguru import logger
from tests.tui.fakes import PLAN

from grillmaster.core.process import ProcessRegistry
from grillmaster.events.context import stage_scope
from grillmaster.events.types import PlanKind, RunStarted, StepCompleted, StepStarted
from grillmaster.tui.app import AppExit, GrillMasterApp
from grillmaster.tui.run import RunAborted, run_with_tui

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from grillmaster.events.bus import EventSink
    from grillmaster.tui.sink import TuiSink
    from grillmaster.tui.state import PipelineState


class _HeadlessApp(GrillMasterApp):
    """Quits like a user pressing `q` once the work has finished (or right
    away with `leave_early`), recording the state it saw. With
    `retry_failures` it presses `r` after a failure instead."""

    def __init__(
        self,
        *args: object,
        leave_early: bool = False,
        retry_failures: bool = False,
        **kwargs: object,
    ):
        super().__init__(*args, tick_seconds=0.01, **kwargs)  # pyright: ignore[reportArgumentType]
        self._leave_early = leave_early
        self._retry_failures = retry_failures

    def pump(self) -> None:
        super().pump()
        if self._leave_early:
            self.exit(AppExit.ABORTED)
        elif self.state.finished and self.state.failed and self._retry_failures:
            self.action_retry()
        elif self.state.finished:
            self.exit(AppExit.DONE)

    def run(self, **_: object) -> AppExit | None:
        return super().run(headless=True, size=(120, 40))


class _CrashingApp(_HeadlessApp):
    """A dashboard whose rendering blows up on the first refresh."""

    def pump(self) -> None:
        raise RuntimeError("widget bug")


class _Factory:
    def __init__(
        self,
        *,
        leave_early: bool = False,
        retry_failures: bool = False,
        app_type: type[_HeadlessApp] = _HeadlessApp,
    ) -> None:
        self.leave_early = leave_early
        self.retry_failures = retry_failures
        self.app_type = app_type
        self.app: _HeadlessApp | None = None

    def __call__(
        self, sink: TuiSink, state: PipelineState, retry: Callable[[], None]
    ) -> GrillMasterApp:
        self.app = self.app_type(
            sink,
            state,
            on_retry=retry,
            leave_early=self.leave_early,
            retry_failures=self.retry_failures,
        )
        return self.app

    @property
    def state(self) -> PipelineState:
        assert self.app is not None
        return self.app.state


class _Console:
    """Stands in for the CLI's console logging: `handler` is the loguru id
    handed to `run_with_tui`, `restore` adds it back."""

    def __init__(self) -> None:
        self.out = io.StringIO()
        self.handler = self._add()
        self.restored = 0
        # Set once logging is restored, i.e. after the dashboard is gone.
        self.restored_event = threading.Event()

    def _add(self) -> int:
        return logger.add(self.out, level="INFO", format="{level} {message}")

    def restore(self) -> None:
        self.restored += 1
        self.handler = self._add()
        self.restored_event.set()


@pytest.fixture
def console() -> Iterator[_Console]:
    console = _Console()
    yield console
    logger.remove(console.handler)


def _run[T](
    work: Callable[[EventSink], T],
    console: _Console,
    factory: _Factory,
    **options: object,
) -> T:
    return run_with_tui(
        work,
        restore_logging=console.restore,
        console_handler=console.handler,
        app_factory=factory,
        **options,  # pyright: ignore[reportArgumentType]
    )


def test_returns_the_work_result_and_shows_its_events_and_logs(console: _Console):
    factory = _Factory()

    def work(sink: EventSink) -> str:
        sink.emit(RunStarted("epabc123", PLAN))
        with stage_scope("download"):
            sink.emit(StepStarted("download", PlanKind.STAGE))
            logger.info("fetching")
            sink.emit(StepCompleted("download", PlanKind.STAGE, 1.0))
        return "layout"

    assert _run(work, console, factory) == "layout"
    assert console.restored == 1
    download = factory.state.step("download")
    assert download is not None
    assert [entry.text.split(" ", 2)[2] for entry in download.log] == ["fetching"]
    assert factory.state.finished
    assert not factory.state.failed


def test_only_the_console_handler_is_swapped_out(console: _Console):
    kept = io.StringIO()
    file_handler = logger.add(kept, level="INFO", format="{message}")

    def work(_sink: EventSink) -> None:
        logger.info("during the dashboard")

    try:
        _run(work, console, _Factory())
        logger.info("after the dashboard")
    finally:
        logger.remove(file_handler)
    assert kept.getvalue().splitlines() == [
        "during the dashboard",
        "after the dashboard",
    ]
    assert "during the dashboard" not in console.out.getvalue()
    assert "after the dashboard" in console.out.getvalue()


def test_raises_the_work_error_after_showing_it(console: _Console):
    factory = _Factory()

    def work(sink: EventSink) -> None:
        raise ValueError("no such video")

    with pytest.raises(ValueError, match="no such video"):
        _run(work, console, factory)
    assert factory.state.error == "no such video"
    traceback_line = factory.state.pipeline_log.last()
    assert traceback_line is not None
    assert "ValueError: no such video" in traceback_line.text


def test_retry_returns_the_second_attempt(console: _Console):
    factory = _Factory(retry_failures=True)
    attempts: list[int] = []

    def work(sink: EventSink) -> str:
        attempts.append(1)
        sink.emit(RunStarted("epabc123", PLAN))
        if len(attempts) == 1:
            raise RuntimeError("abema flake")
        return "second"

    assert _run(work, console, factory) == "second"
    assert len(attempts) == 2
    assert factory.state.finished
    assert not factory.state.failed


def test_a_crashed_dashboard_falls_back_to_the_console(console: _Console):
    factory = _Factory(app_type=_CrashingApp)

    def work(sink: EventSink) -> str:
        # Emit only once the dashboard is gone: nothing drains the queue then.
        console.restored_event.wait(30)
        sink.emit(StepStarted("download", PlanKind.STAGE))
        return "late"

    assert _run(work, console, factory) == "late"
    assert factory.app is not None
    assert factory.app.return_code == 1
    assert factory.app.sink.drain() == []
    output = console.out.getvalue()
    assert "Dashboard crashed (exit code 1)" in output
    assert "[download] started" in output


class _FakeProcess:
    def __init__(self) -> None:
        self.killed = threading.Event()


def test_abort_kills_the_work_processes_and_waits_for_the_worker(console: _Console):
    registry = ProcessRegistry[_FakeProcess](lambda process: process.killed.set())
    finished = threading.Event()

    def work(_sink: EventSink) -> None:
        process = _FakeProcess()
        registry.register(process)
        try:
            process.killed.wait(30)
        finally:
            registry.unregister(process)
            finished.set()

    with pytest.raises(RunAborted):
        _run(
            work,
            console,
            _Factory(leave_early=True),
            kill_processes=registry.kill_all,
        )
    assert finished.is_set()
    assert registry.live() == []
    assert console.restored == 1


def test_abort_abandons_a_worker_that_will_not_stop(console: _Console):
    release = threading.Event()
    started = time.monotonic()

    def work(_sink: EventSink) -> None:
        release.wait()

    try:
        with pytest.raises(RunAborted):
            _run(
                work,
                console,
                _Factory(leave_early=True),
                kill_processes=lambda: None,
                interrupt_wait_s=0.3,
            )
    finally:
        release.set()
    assert time.monotonic() - started < 10
