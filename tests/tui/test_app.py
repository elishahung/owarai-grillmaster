"""Pilot tests for the dashboard.

The refresh timer is off (`tick_seconds=None`): each test feeds events into
the sink and calls `app.pump()` itself, so nothing depends on timing.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from rich.console import Console
from tests.tui.fakes import PLAN

from grillmaster.events.context import stage_scope, task_scope
from grillmaster.events.types import (
    ActivityKind,
    AgentActivity,
    AgentSessionFinished,
    AgentSessionStarted,
    LogLine,
    PlanKind,
    ProgressAdvanced,
    ProgressStarted,
    RunStarted,
    SessionOutcome,
    SkipReason,
    StepCompleted,
    StepSkipped,
    StepStarted,
)
from grillmaster.tui.app import AppExit, GrillMasterApp
from grillmaster.tui.sink import TuiSink
from grillmaster.tui.state import PipelineState
from grillmaster.tui.widgets import ActivityLog, ChunkBoard, SessionTable
from grillmaster.tui.widgets.chunk_board import render_chunk_board
from grillmaster.tui.widgets.session_table import listed_sessions, render_session_table
from grillmaster.tui.widgets.stage_detail import render_stage_detail
from grillmaster.tui.widgets.summary import render_summary

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from rich.console import RenderableType
    from textual.pilot import Pilot

SIZE = (140, 45)


def _run[T](coro: Awaitable[T]) -> T:
    async def main() -> T:
        return await coro

    return asyncio.run(main())  # noqa: TID251 - Textual's pilot needs an event loop


def _text(renderable: RenderableType) -> str:
    console = Console(width=140, record=True, color_system=None)
    console.print(renderable)
    return console.export_text()


def _scripted(sink: TuiSink) -> None:
    """A run in the middle of the chunks stage, two chunk sessions live."""
    sink.emit(RunStarted("epabc123", PLAN))
    sink.emit(StepSkipped("metadata", PlanKind.STAGE, SkipReason.ALREADY_COMPLETE))
    sink.emit(StepCompleted("download", PlanKind.STAGE, 5.0))
    with stage_scope("chunks"):
        sink.emit(StepStarted("chunks", PlanKind.STAGE))
        sink.emit(LogLine("INFO", "Translating 2/2 chunks", "chunks"))
        for task in ("chunks/0001-0040", "chunks/0041-0080"):
            with task_scope(task):
                sink.emit(AgentSessionStarted(task, "chunks", "agy", "gemini", None))
        sink.emit(
            AgentActivity("chunks/0041-0080", ActivityKind.TOOL_CALL, "get_frames 62.5")
        )
        sink.emit(AgentSessionFinished("chunks/0001-0040", SessionOutcome.OK, 9.0, 0))


def _app(
    sink: TuiSink,
    *,
    on_retry: Callable[[], None] | None = None,
    clipboard: Callable[[str], None] | None = None,
) -> GrillMasterApp:
    # Monotonic like the sink, so stamped events and the live clock agree.
    state = PipelineState()
    if clipboard is None:
        return GrillMasterApp(sink, state, on_retry=on_retry, tick_seconds=None)
    return GrillMasterApp(
        sink, state, on_retry=on_retry, clipboard=clipboard, tick_seconds=None
    )


async def _pump(app: GrillMasterApp, pilot: Pilot[AppExit]) -> None:
    app.pump()
    await pilot.pause()


def test_follow_tracks_the_running_stage_and_arrows_take_over():
    sink = TuiSink()
    _scripted(sink)
    app = _app(sink)

    async def drive() -> None:
        async with app.run_test(size=SIZE) as pilot:
            await _pump(app, pilot)
            assert app.view.selected == "chunks"
            assert app.query_one(ChunkBoard).display
            assert app.query_one(SessionTable).display

            await pilot.press("down")
            assert not app.view.follow
            # refine (disabled) follows chunks in the list.
            assert app.view.selected == "refine"
            assert not app.query_one(ChunkBoard).display
            await pilot.press("up")
            assert app.view.selected == "chunks"

            await pilot.press("f")
            assert app.view.follow

    _run(drive())


def test_arrow_keys_select_a_chunk_and_show_its_activity():
    sink = TuiSink()
    _scripted(sink)
    app = _app(sink)

    async def drive() -> None:
        async with app.run_test(size=SIZE) as pilot:
            await _pump(app, pilot)
            await pilot.press("right")
            assert app.view.chunk == "chunks/0001-0040"
            await pilot.press("right")
            assert app.view.chunk == "chunks/0041-0080"
            await pilot.press("right")
            assert app.view.chunk == "chunks/0001-0040"
            await pilot.press("left")
            assert app.view.chunk == "chunks/0041-0080"

    _run(drive())
    text = _text(render_chunk_board(app.state, "chunks", app.view.chunk))
    assert "[02]" in text
    assert "get_frames 62.5" in text
    assert "1/2 done" in text


def test_a_toggles_the_full_activity_log():
    sink = TuiSink()
    _scripted(sink)
    app = _app(sink)

    async def drive() -> None:
        async with app.run_test(size=SIZE) as pilot:
            await _pump(app, pilot)
            activity = app.query_one(ActivityLog)
            assert not activity.display
            await pilot.press("a")
            assert activity.display
            assert not app.query_one("#detail-scroll").display
            sink.emit(AgentActivity("chunks/0041-0080", ActivityKind.THOUGHT, "hmm"))
            await _pump(app, pilot)
            assert len(activity.lines) == 2
            await pilot.press("a")
            assert not activity.display

    _run(drive())


def test_session_table_lists_running_chunks_and_last_activity():
    sink = TuiSink()
    _scripted(sink)
    state = PipelineState()
    sink.feed(state)
    sessions = listed_sessions(state, "chunks")
    # The finished chunk lives on the board only.
    assert [session.task for session in sessions] == ["chunks/0041-0080"]
    text = _text(render_session_table(state, sessions))
    assert "agy/gemini" in text
    assert "get_frames 62.5" in text


def test_copy_log_puts_the_whole_buffer_on_the_clipboard():
    sink = TuiSink()
    _scripted(sink)
    for n in range(30):
        sink.emit(LogLine("INFO", f"chunk line {n}", "chunks"))
    copied: list[str] = []
    app = _app(sink, clipboard=copied.append)

    async def drive() -> None:
        async with app.run_test(size=SIZE) as pilot:
            await _pump(app, pilot)
            await pilot.press("c")

    _run(drive())
    lines = copied[0].splitlines()
    assert lines[0] == "# Translate chunks"
    assert lines[1] == "Translating 2/2 chunks"
    assert lines[2:] == [f"chunk line {n}" for n in range(30)]


class _NotifyRecorder(GrillMasterApp):
    severities: list[str]

    def notify(self, message: str, *, severity: str = "information", **_: object):  # type: ignore[override]  # pyright: ignore[reportIncompatibleMethodOverride]
        self.severities.append(severity)


def test_copy_log_reports_a_clipboard_failure():
    sink = TuiSink()
    _scripted(sink)

    def broken(_text: str) -> None:
        raise FileNotFoundError("no clip.exe")

    app = _NotifyRecorder(sink, PipelineState(), clipboard=broken, tick_seconds=None)
    app.severities = []

    async def drive() -> None:
        async with app.run_test(size=SIZE) as pilot:
            await _pump(app, pilot)
            await pilot.press("c")

    _run(drive())
    assert app.severities == ["error"]


def test_quit_needs_a_second_press_while_running():
    sink = TuiSink()
    _scripted(sink)
    app = _app(sink)

    async def drive() -> None:
        async with app.run_test(size=SIZE) as pilot:
            await _pump(app, pilot)
            await pilot.press("q")
            assert app.return_value is None
            await pilot.press("q")
            await pilot.pause()

    _run(drive())
    assert app.return_value is AppExit.ABORTED


def test_quit_leaves_at_once_when_finished():
    sink = TuiSink()
    _scripted(sink)
    sink.work_done(None)
    app = _app(sink)

    async def drive() -> None:
        async with app.run_test(size=SIZE) as pilot:
            await _pump(app, pilot)
            assert app.query_one("#summary").display
            await pilot.press("q")
            await pilot.pause()

    _run(drive())
    assert app.return_value is AppExit.DONE


def test_retry_only_after_a_failure():
    sink = TuiSink()
    _scripted(sink)
    retries: list[int] = []
    app = _app(sink, on_retry=lambda: retries.append(1))

    async def drive() -> None:
        async with app.run_test(size=SIZE) as pilot:
            await _pump(app, pilot)
            await pilot.press("r")
            assert retries == []

            sink.work_done("abema flake")
            await _pump(app, pilot)
            app.view.follow = False
            await pilot.press("r")
            assert retries == [1]
            assert app.view.follow
            assert not app.state.finished

    _run(drive())


def test_detail_and_summary_render_bars_results_and_failure():
    sink = TuiSink()
    _scripted(sink)
    with stage_scope("chunks"):
        sink.emit(ProgressStarted("frames", "Extracting frames", 10.0))
        sink.emit(ProgressAdvanced("frames", 5.0, "chunk 3"))
        sink.emit(ProgressStarted("spin", "Waiting for a slot", None))
    state = PipelineState()
    sink.feed(state)
    chunks = state.step("chunks")
    assert chunks is not None
    detail = _text(render_stage_detail(state, chunks))
    assert "Extracting frames  chunk 3" in detail
    assert " 50%" in detail
    assert "working…" in detail
    assert "agy/gemini-3.1-pro" in detail  # params

    state.work_finished("chunks/0041-0080: output still invalid")
    failed = _text(render_summary(state))
    assert "Pipeline failed" in failed
    assert "output still invalid" in failed


def test_summary_lists_step_results_and_agent_totals():
    sink = TuiSink()
    _scripted(sink)
    sink.emit(StepCompleted("package", PlanKind.DELIVERY, 3.0, "V:/out/show.mp4"))
    sink.work_done(None)
    state = PipelineState()
    sink.feed(state)
    text = _text(render_summary(state))
    assert "Pipeline completed" in text
    assert "V:/out/show.mp4" in text
    assert "agent sessions  2 (0 repairs)" in text
