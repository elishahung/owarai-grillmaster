"""What every widget shares: the app's view selection and the visual vocabulary."""

from __future__ import annotations

from dataclasses import dataclass

from rich.progress_bar import ProgressBar
from rich.text import Text

from grillmaster.events.types import ActivityKind
from grillmaster.tui.state import ActivityEntry, ItemState, SessionState


@dataclass(slots=True)
class View:
    """The app's view state, which widgets render against.

    `selected` is a step key; `chunk` the selected chunk-board task;
    `activity` shows the full activity log in place of the detail panel.
    """

    selected: str | None = None
    follow: bool = True
    chunk: str | None = None
    activity: bool = False


STATE_ICON = {
    ItemState.PENDING: ("○", "grey58"),
    ItemState.RUNNING: ("▶", "bold yellow1"),
    ItemState.DONE: ("✔", "green3"),
    ItemState.CACHED: ("✔", "cyan"),
    ItemState.DISABLED: ("⊘", "grey42"),
    ItemState.SKIPPED: ("⊘", "grey42"),
    ItemState.FAILED: ("✘", "bold red"),
}

SESSION_ICON = {
    SessionState.RUNNING: ("▶", "yellow1"),
    SessionState.OK: ("✔", "green3"),
    SessionState.FAILED: ("✘", "red"),
}

CHUNK_STYLE = {
    SessionState.RUNNING: "black on yellow1",
    SessionState.OK: "black on green3",
    SessionState.FAILED: "white on red",
}

ACTIVITY_ICON = {
    ActivityKind.THOUGHT: "💭",
    ActivityKind.TOOL_CALL: "🔧",
    ActivityKind.TOOL_RESULT: "↩",
    ActivityKind.MESSAGE: "💬",
    ActivityKind.REPAIR: "🩹",
}

LOG_LEVEL_STYLE = {
    "DEBUG": "grey58",
    "INFO": "",
    "SUCCESS": "green3",
    "WARNING": "yellow1",
    "ERROR": "bold red",
    "CRITICAL": "bold red",
}


def fmt_clock(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def fmt_tokens(count: int) -> str:
    if count >= 1_000_000:  # noqa: PLR2004 - unit threshold
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:  # noqa: PLR2004 - unit threshold
        return f"{count / 1_000:.1f}k"
    return str(count)


def bar(progress: float, width: int = 30) -> ProgressBar:
    return ProgressBar(total=1.0, completed=progress, width=width)


def activity_text(entry: ActivityEntry, *, with_task: bool = False) -> Text:
    text = Text()
    if with_task:
        text.append(f"{entry.task}  ", style="cyan")
    text.append(f"{ACTIVITY_ICON[entry.kind]} ")
    text.append(
        entry.summary,
        style="bold" if entry.kind is ActivityKind.REPAIR else "",
    )
    return text
