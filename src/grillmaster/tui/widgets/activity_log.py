"""Scrolling logs fed incrementally from `RingLog`s: the full agent
activity stream (`a`) and the selected step's log pane."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.widgets import RichLog

from grillmaster.tui.widgets._common import LOG_LEVEL_STYLE, activity_text

if TYPE_CHECKING:
    from grillmaster.tui.state import ActivityEntry, LogEntry, PipelineState, RingLog


class _RingLogView(RichLog):
    """Writes only the lines its source gained since the last call; a new
    source clears the view first."""

    def __init__(self, *, id: str | None = None) -> None:  # noqa: A002 - Textual's name
        super().__init__(id=id, markup=False, wrap=False)
        self._source: object | None = None
        self._seen = 0

    def _switch(self, source: object) -> None:
        if source is not self._source:
            self.clear()
            self._source = source
            self._seen = 0


class ActivityLog(_RingLogView):
    """Every `AgentActivity` of the run, tagged with its task."""

    def show(self, state: PipelineState) -> None:
        source: RingLog[ActivityEntry] = state.activity
        self._switch(source)
        for entry in source.since(self._seen):
            self.write(activity_text(entry, with_task=True))
        self._seen = source.count


class LogPane(_RingLogView):
    """The log lines of one scope: the selected step, or the run itself."""

    def show(self, source: RingLog[LogEntry], title: str) -> None:
        self._switch(source)
        self.border_title = title
        for entry in source.since(self._seen):
            self.write(Text(entry.text, style=LOG_LEVEL_STYLE.get(entry.level, "")))
        self._seen = source.count
