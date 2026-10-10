"""Scrolling logs fed incrementally from `RingLog`s: the full agent
activity stream (`a`) and the selected step's log pane.

Each view keeps no more lines than its source ring. A `RichLog` that has
never been laid out (the activity log starts hidden) defers every write
without bound, so a hidden view is not fed at all: the app calls `forget`
and the next `show` replays the ring's tail.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.widgets import RichLog

from grillmaster.tui.state import ACTIVITY_LINES, LOG_LINES_PER_SCOPE
from grillmaster.tui.widgets._common import LOG_LEVEL_STYLE, activity_text

if TYPE_CHECKING:
    from collections.abc import Callable

    from grillmaster.tui.state import ActivityEntry, LogEntry, PipelineState, RingLog


class _RingLogView[T](RichLog):
    """Writes only the lines its source gained since the last call, batched
    into one write; a new source (or one after `forget`) clears the view and
    replays what the source still keeps."""

    def __init__(
        self,
        to_line: Callable[[T], Text],
        *,
        max_lines: int,
        id: str | None = None,  # noqa: A002 - Textual's name
    ) -> None:
        super().__init__(id=id, markup=False, wrap=False, max_lines=max_lines)
        self._to_line = to_line
        self._source: RingLog[T] | None = None
        self._seen = 0

    def forget(self) -> None:
        """Drop the source, so the next `show` starts over from its ring."""
        self._source = None

    def _feed(self, source: RingLog[T]) -> None:
        if source is not self._source:
            self.clear()
            self._source = source
            self._seen = 0
        fresh = source.since(self._seen)
        self._seen = source.count
        if fresh:
            self.write(Text("\n").join(self._to_line(entry) for entry in fresh))


def _activity_line(entry: ActivityEntry) -> Text:
    return activity_text(entry, with_task=True)


def _log_line(entry: LogEntry) -> Text:
    return Text(entry.text, style=LOG_LEVEL_STYLE.get(entry.level, ""))


class ActivityLog(_RingLogView["ActivityEntry"]):
    """Every `AgentActivity` of the run, tagged with its task."""

    def __init__(self, *, id: str | None = None) -> None:  # noqa: A002 - Textual's name
        super().__init__(_activity_line, max_lines=ACTIVITY_LINES, id=id)

    def show(self, state: PipelineState) -> None:
        self._feed(state.activity)


class LogPane(_RingLogView["LogEntry"]):
    """The log lines of one scope: the selected step, or the run itself."""

    def __init__(self, *, id: str | None = None) -> None:  # noqa: A002 - Textual's name
        super().__init__(_log_line, max_lines=LOG_LINES_PER_SCOPE, id=id)

    def show(self, source: RingLog[LogEntry], title: str) -> None:
        self.border_title = title
        self._feed(source)
