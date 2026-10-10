"""The full-screen Textual dashboard for one run (or a serial batch).

Layout: header with weighted overall progress; sidebar of stages, delivery
steps and side tasks; detail panel for the selected step (params, progress
bars, chunk board, sessions table) or the run summary; a log pane for the
selected step. `a` swaps the detail panel for the full agent activity log.

On a timer the app drains `TuiSink` into its `PipelineState` and re-renders;
it never receives events directly.
"""

from __future__ import annotations

import subprocess
import sys
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from textual.app import App
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Footer

from grillmaster.events.types import PlanKind
from grillmaster.tui.state import ItemState, PipelineState
from grillmaster.tui.widgets import (
    ActivityLog,
    ChunkBoard,
    Header,
    LogPane,
    SessionTable,
    StageDetail,
    StageList,
    Summary,
    View,
    row_line,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual.app import ComposeResult

    from grillmaster.tui.sink import TuiSink

ABORT_CONFIRM_WINDOW = 3.0  # seconds between the two `q` presses
TICK_SECONDS = 0.1

CLIPBOARD_COMMAND = {
    "win32": ["clip.exe"],
    "darwin": ["pbcopy"],
}.get(sys.platform, ["xclip", "-selection", "clipboard"])


class AppExit(StrEnum):
    DONE = "done"  # the work had finished
    ABORTED = "aborted"  # the user left while the work was running


def put_on_clipboard(text: str) -> None:
    """Hand `text` to the OS clipboard, raising if the helper fails.

    Textual's own copy rides OSC 52, which conhost ignores; the platform
    helper actually lands it. clip.exe wants UTF-8 without a BOM (a BOM
    arrives as a literal character in the pasted text).
    """
    subprocess.run(CLIPBOARD_COMMAND, input=text.encode("utf-8"), check=True)  # noqa: TID251 - an OS helper, not a pipeline child process


class GrillMasterApp(App[AppExit]):
    """`on_retry` restarts the work after a failure (the app has already
    reset the state). `tick_seconds=None` disables the refresh timer; tests
    then call `pump` themselves."""

    TITLE = "Owarai GrillMaster"

    CSS = """
    #header { height: 4; padding: 0 1; background: $surface; }
    #body { height: 1fr; }
    #stages-scroll { width: 42; border: round $primary-darken-2; padding: 0 1; }
    #detail-scroll { width: 1fr; border: round $primary-darken-2; padding: 0 1; }
    #stages, #detail, #chunks, #sessions, #summary { width: 100%; height: auto; }
    #activity { width: 1fr; border: round $primary-darken-2; display: none; }
    #log { height: 9; border: round $surface-lighten-2; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        ("q", "quit_or_abort", "Quit"),
        ("r", "retry", "Retry"),
        ("f", "toggle_follow", "Follow"),
        ("a", "toggle_activity", "Activity"),
        ("c", "copy_log", "Copy log"),
        Binding("up", "select_step(-1)", "Prev step", priority=True),
        Binding("down", "select_step(1)", "Next step", priority=True),
        Binding("k", "select_step(-1)", "Prev step", show=False),
        Binding("j", "select_step(1)", "Next step", show=False),
        Binding("left", "select_chunk(-1)", "Prev chunk", priority=True),
        Binding("right", "select_chunk(1)", "Next chunk", priority=True),
        Binding("h", "select_chunk(-1)", "Prev chunk", show=False),
        Binding("l", "select_chunk(1)", "Next chunk", show=False),
        Binding("pageup", "detail_scroll(-1)", "Scroll detail", priority=True),
        Binding(
            "pagedown", "detail_scroll(1)", "Scroll detail", priority=True, show=False
        ),
        Binding("ctrl+c", "quit_or_abort", "Quit", show=False, priority=True),
    ]

    def __init__(
        self,
        sink: TuiSink,
        state: PipelineState | None = None,
        *,
        on_retry: Callable[[], None] | None = None,
        clipboard: Callable[[str], None] = put_on_clipboard,
        tick_seconds: float | None = TICK_SECONDS,
    ) -> None:
        super().__init__()
        self.sink = sink
        self.state = state if state is not None else PipelineState()
        self.view = View()
        self._on_retry = on_retry
        self._copy = clipboard
        self._tick_seconds = tick_seconds
        self._abort_armed_at: float | None = None
        self._scrolled_to: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(id="header")
        with Horizontal(id="body"):
            with VerticalScroll(id="stages-scroll"):
                yield StageList(id="stages")
            with VerticalScroll(id="detail-scroll"):
                yield StageDetail(id="detail")
                yield ChunkBoard(id="chunks")
                yield SessionTable(id="sessions")
                yield Summary(id="summary")
            yield ActivityLog(id="activity")
        yield LogPane(id="log")
        yield Footer()

    def on_mount(self) -> None:
        if self._tick_seconds is not None:
            self.set_interval(self._tick_seconds, self.pump)
        self.pump()

    # -- refresh -------------------------------------------------------------

    def pump(self) -> None:
        """Apply the queued events and re-render."""
        self.sink.feed(self.state)
        if self.view.follow:
            self._follow_running()
        self._keep_selection_valid()
        self.refresh_view()

    def _follow_running(self) -> None:
        running = [
            step
            for step in self.state.display_steps()
            if step.state is ItemState.RUNNING
        ]
        main = [step for step in running if step.kind is not PlanKind.SIDE_TASK]
        if main or running:
            self.view.selected = (main or running)[0].key

    def _keep_selection_valid(self) -> None:
        steps = self.state.display_steps()
        if steps and self.state.step(self.view.selected or "") is None:
            self.view.selected = steps[0].key

    def refresh_view(self) -> None:
        state, view = self.state, self.view
        try:
            self.query_one(Header).show(state, view)
            self.query_one(StageList).show(state, view)
            step = state.step(view.selected) if view.selected is not None else None
            summary_mode = state.finished and view.follow
            detail = self.query_one(StageDetail)
            summary = self.query_one(Summary)
            detail.display = not summary_mode
            summary.display = summary_mode
            key = None if summary_mode or step is None else step.key
            if summary_mode:
                summary.show(state)
            else:
                detail.show(state, step)
            self.query_one(ChunkBoard).show(state, key, view.chunk)
            self.query_one(SessionTable).show(state, key)
            self.query_one("#detail-scroll").display = not view.activity
            activity = self.query_one(ActivityLog)
            activity.display = view.activity
            activity.show(state)
            if step is None:
                self.query_one(LogPane).show(state.pipeline_log, "log")
            else:
                self.query_one(LogPane).show(step.log, f"log · {step.label}")
        except NoMatches:
            # A timer tick can land while the app shuts down.
            return
        if view.selected != self._scrolled_to:
            self._scroll_sidebar_to_selection()

    def _scroll_sidebar_to_selection(self) -> None:
        scroll = self.query_one("#stages-scroll", VerticalScroll)
        height = scroll.container_size.height
        line = row_line(self.state, self.view.selected or "")
        if height <= 0 or line is None:
            return
        self._scrolled_to = self.view.selected
        top = scroll.scroll_offset.y
        if line < top:
            scroll.scroll_to(y=line, animate=False)
        elif line >= top + height:
            scroll.scroll_to(y=line - height + 1, animate=False)

    # -- actions -------------------------------------------------------------

    def action_quit_or_abort(self) -> None:
        if self.state.finished:
            self.exit(AppExit.DONE)
            return
        now = self.state.now()
        if (
            self._abort_armed_at is not None
            and now - self._abort_armed_at <= ABORT_CONFIRM_WINDOW
        ):
            self.exit(AppExit.ABORTED)
            return
        self._abort_armed_at = now
        self.notify(
            "Pipeline is running — press q again within 3s to abort "
            "(the project is resumable).",
            severity="warning",
        )

    def action_retry(self) -> None:
        if not (self.state.finished and self.state.failed):
            self.notify("Retry is available after a failure.", severity="information")
            return
        if self._on_retry is None:
            return
        self.state.reset_for_retry()
        self.view.follow = True
        self.view.chunk = None
        self._on_retry()
        self.notify("Retrying — resuming from the failed stage.")
        self.refresh_view()

    def action_toggle_follow(self) -> None:
        self.view.follow = not self.view.follow
        self.refresh_view()

    def action_toggle_activity(self) -> None:
        self.view.activity = not self.view.activity
        self.refresh_view()

    def action_select_step(self, delta: int) -> None:
        keys = [step.key for step in self.state.display_steps()]
        if not keys:
            return
        self.view.follow = False
        current = keys.index(self.view.selected) if self.view.selected in keys else 0
        self.view.selected = keys[(current + delta) % len(keys)]
        self.view.chunk = None
        self.query_one("#detail-scroll", VerticalScroll).scroll_home(animate=False)
        self.refresh_view()

    def action_select_chunk(self, delta: int) -> None:
        tasks = [
            cell.session.task
            for cell in self.state.chunk_cells(self.view.selected or "")
        ]
        if not tasks:
            return
        if self.view.chunk in tasks:
            position = (tasks.index(self.view.chunk) + delta) % len(tasks)
        else:
            position = 0 if delta > 0 else len(tasks) - 1
        self.view.chunk = tasks[position]
        self.refresh_view()

    def action_detail_scroll(self, direction: int) -> None:
        scroll = self.query_one("#detail-scroll", VerticalScroll)
        if direction < 0:
            scroll.scroll_page_up(animate=False)
        else:
            scroll.scroll_page_down(animate=False)

    def action_copy_log(self) -> None:
        """Copy the selected step's whole log buffer, not just the pane's tail."""
        step = self.state.step(self.view.selected or "")
        label, source = (
            ("pipeline", self.state.pipeline_log)
            if step is None
            else (step.label, step.log)
        )
        lines = [entry.text for entry in source]
        if not lines:
            self.notify("Nothing in this log yet.", severity="information")
            return
        try:
            self._copy("\n".join([f"# {label}", *lines]))
        except (OSError, subprocess.SubprocessError) as error:
            self.notify(f"Could not copy the log: {error}", severity="error")
            return
        self.notify(f"Copied {len(lines)} log lines to the clipboard.")
