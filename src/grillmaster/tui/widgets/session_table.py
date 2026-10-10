"""The selected step's agent sessions, one row per task, with the newest
activity summary of each."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from grillmaster.tui.state import SessionState, chunk_range
from grillmaster.tui.widgets._common import SESSION_ICON, activity_text, fmt_clock

if TYPE_CHECKING:
    from rich.console import RenderableType

    from grillmaster.tui.state import PipelineState, SessionView


class SessionTable(Static):
    """Hidden while the step has no session to list."""

    def show(self, state: PipelineState, key: str | None) -> None:
        sessions = listed_sessions(state, key) if key is not None else []
        self.display = bool(sessions)
        if sessions:
            self.update(render_session_table(state, sessions))


def listed_sessions(state: PipelineState, key: str) -> list[SessionView]:
    """The step's sessions; chunk sessions only while running or failed (the
    chunk board shows the rest)."""
    return [
        session
        for session in state.sessions_for(key)
        if chunk_range(session.task) is None or session.state is not SessionState.OK
    ]


def render_session_table(
    state: PipelineState, sessions: list[SessionView]
) -> RenderableType:
    table = Table(
        box=None,
        padding=(0, 1),
        show_header=True,
        header_style="grey58",
        title="Sessions",
        title_justify="left",
        title_style="bold",
        expand=True,
    )
    table.add_column("", width=1)
    table.add_column("task", no_wrap=True)
    table.add_column("model", style="cyan", no_wrap=True)
    table.add_column("elapsed", justify="right", no_wrap=True)
    table.add_column("tools", justify="right")
    table.add_column("repairs", justify="right")
    table.add_column("last activity", ratio=1, no_wrap=True, overflow="ellipsis")
    now = state.now()
    for session in sessions:
        icon, style = SESSION_ICON[session.state]
        task = Text(session.task)
        if session.retries:
            task.append(f" x{session.attempts}", style="dark_orange")
        last = session.last_activity
        table.add_row(
            Text(icon, style=style),
            task,
            session.spec,
            fmt_clock(session.live_elapsed(now)),
            str(session.tool_calls),
            Text(str(session.repairs), style="dark_orange" if session.repairs else ""),
            activity_text(last) if last is not None else Text("…", style="grey42"),
        )
    return table
