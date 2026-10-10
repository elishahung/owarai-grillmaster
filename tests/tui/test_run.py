"""`run_with_tui` end to end, with a headless app that leaves by itself."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest
from loguru import logger
from tests.tui.fakes import PLAN

from grillmaster.events.context import stage_scope
from grillmaster.events.types import PlanKind, RunStarted, StepCompleted, StepStarted
from grillmaster.tui.app import AppExit, GrillMasterApp
from grillmaster.tui.run import RunAborted, run_with_tui

if TYPE_CHECKING:
    from collections.abc import Callable

    from grillmaster.events.bus import EventSink
    from grillmaster.tui.sink import TuiSink
    from grillmaster.tui.state import PipelineState


class _HeadlessApp(GrillMasterApp):
    """Quits like a user pressing `q` once the work has finished (or right
    away with `leave_early`), recording the state it saw."""

    def __init__(self, *args: object, leave_early: bool = False, **kwargs: object):
        super().__init__(*args, tick_seconds=0.01, **kwargs)  # pyright: ignore[reportArgumentType]
        self._leave_early = leave_early

    def pump(self) -> None:
        super().pump()
        if self._leave_early:
            self.exit(AppExit.ABORTED)
        elif self.state.finished:
            self.exit(AppExit.DONE)

    def run(self, **_: object) -> AppExit | None:  # pyright: ignore[reportIncompatibleMethodOverride]
        return super().run(headless=True, size=(120, 40))


class _Factory:
    def __init__(self, *, leave_early: bool = False) -> None:
        self.leave_early = leave_early
        self.app: _HeadlessApp | None = None

    def __call__(
        self, sink: TuiSink, state: PipelineState, retry: Callable[[], None]
    ) -> GrillMasterApp:
        self.app = _HeadlessApp(
            sink, state, on_retry=retry, leave_early=self.leave_early
        )
        return self.app

    @property
    def state(self) -> PipelineState:
        assert self.app is not None
        return self.app.state


def _restored() -> tuple[list[int], Callable[[], None]]:
    calls: list[int] = []
    return calls, lambda: calls.append(1)


def test_returns_the_work_result_and_shows_its_events_and_logs():
    factory = _Factory()
    restored, restore = _restored()

    def work(sink: EventSink) -> str:
        sink.emit(RunStarted("epabc123", PLAN))
        with stage_scope("download"):
            sink.emit(StepStarted("download", PlanKind.STAGE))
            logger.info("fetching")
            sink.emit(StepCompleted("download", PlanKind.STAGE, 1.0))
        return "layout"

    assert run_with_tui(work, restore_logging=restore, app_factory=factory) == "layout"
    assert restored == [1]
    download = factory.state.step("download")
    assert download is not None
    assert [entry.text.split(" ", 2)[2] for entry in download.log] == ["fetching"]
    assert factory.state.finished
    assert not factory.state.failed


def test_raises_the_work_error_after_showing_it():
    factory = _Factory()
    _restored_calls, restore = _restored()

    def work(sink: EventSink) -> None:
        raise ValueError("no such video")

    with pytest.raises(ValueError, match="no such video"):
        run_with_tui(work, restore_logging=restore, app_factory=factory)
    assert factory.state.error == "no such video"
    traceback_line = factory.state.pipeline_log.last()
    assert traceback_line is not None
    assert "ValueError: no such video" in traceback_line.text


def test_leaving_while_the_work_runs_aborts():
    factory = _Factory(leave_early=True)
    release = threading.Event()
    _restored_calls, restore = _restored()

    def work(sink: EventSink) -> None:
        release.wait()

    try:
        with pytest.raises(RunAborted):
            run_with_tui(work, restore_logging=restore, app_factory=factory)
    finally:
        release.set()
